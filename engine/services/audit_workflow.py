from __future__ import annotations

from dataclasses import dataclass

from engine.schemas.artifact import ChapterQualityRecord, ChapterSettlementRecord
from engine.schemas.chapter import ChapterStage
from engine.schemas.run import RunAction, RunStatus
from .gate_runner import GateInputBundle


@dataclass(slots=True)
class AuditWorkflow:
    repo: object
    gate_runner: object
    truth_service: object
    register_artifact: object
    start_run: object
    finish_run: object
    load_artifact_payload: object
    optional_artifact_payload: object
    project_chapter_quality: object
    save_status: object

    def run(self, *, book_id: str, chapter_no: int, status, settlement_artifact_id: str) -> dict:
        self.truth_service._assert_truth_fresh_for_action(book_id, chapter_no, "audit")
        if status.stage != ChapterStage.SETTLED:
            raise ValueError("audit_chapter requires settled stage")

        settlement_payload = ChapterSettlementRecord.model_validate(
            self.load_artifact_payload(book_id, settlement_artifact_id)
        )
        draft_payload = self.load_artifact_payload(book_id, settlement_payload.draft_artifact_id)
        plan_payload = self.optional_artifact_payload(book_id, status.current_artifact_refs.get("plan"))
        compose_payload = self.optional_artifact_payload(book_id, status.current_artifact_refs.get("compose"))
        baseline_payload = self.optional_artifact_payload(book_id, settlement_payload.baseline_gate_ref)
        revision_payload = self.optional_artifact_payload(book_id, status.current_artifact_refs.get("revision_brief"))
        run = self.start_run(book_id, chapter_no, RunAction.AUDIT.value, "auditor", [settlement_artifact_id])

        truth_snapshot_id = (
            settlement_payload.base_truth_snapshot_id or self.repo.json.load_truth_index(book_id).current_snapshot_id
        )
        truth_snapshot = self.repo.json.load_truth_snapshot(book_id, truth_snapshot_id)
        truth_delta = self.truth_service._build_truth_delta(
            book_id=book_id,
            chapter_no=chapter_no,
            settlement_artifact_id=settlement_artifact_id,
            draft_artifact_id=settlement_payload.draft_artifact_id,
            base_snapshot_id=truth_snapshot_id,
            draft_text=draft_payload["text"],
        )
        truth_delta_id = self.register_artifact(
            book_id,
            chapter_no,
            "truth_delta",
            truth_delta.model_dump(mode="json"),
            run.run_id,
        )
        reconcile_payload = {
            "truth_delta_id": truth_delta_id,
            "base_snapshot_id": truth_snapshot_id,
            "blocking_conflict_count": sum(1 for conflict in truth_delta.conflicts if conflict.severity == "blocking"),
            "truth_snapshot_id": truth_snapshot["snapshot_id"],
        }
        reconcile_id = self.register_artifact(
            book_id,
            chapter_no,
            "truth_reconcile",
            reconcile_payload,
            run.run_id,
        )
        if any(conflict.severity == "blocking" for conflict in truth_delta.conflicts):
            self.finish_run(
                book_id,
                chapter_no,
                run.run_id,
                RunStatus.FAILED.value,
                [truth_delta_id, reconcile_id],
                "truth reconcile produced blocking conflicts",
            )
            raise ValueError("truth reconcile produced blocking conflicts")

        bundle = GateInputBundle(
            book_id=book_id,
            chapter_no=chapter_no,
            revision_round=status.revision_round,
            settlement_artifact_id=settlement_artifact_id,
            candidate_signature=settlement_payload.candidate_signature,
            draft_text=draft_payload["text"],
            plan_summary=(plan_payload or {}).get("guidance") or (plan_payload or {}).get("hook_target", ""),
            compose_constraints=(compose_payload or {}).get("constraints", []),
            baseline_gate_summary=(baseline_payload or {}),
            revision_mode=(revision_payload or {}).get("mode"),
        )
        try:
            mechanical, audit, gate = self.gate_runner.evaluate(bundle)
        except Exception as exc:
            self.finish_run(book_id, chapter_no, run.run_id, RunStatus.FAILED.value, [], str(exc))
            raise

        mechanical_id = self.register_artifact(
            book_id,
            chapter_no,
            "mechanical_gate",
            mechanical.model_dump(mode="json"),
            run.run_id,
        )
        audit_id = self.register_artifact(
            book_id,
            chapter_no,
            "audit",
            audit.model_dump(mode="json"),
            run.run_id,
        )
        gate = gate.model_copy(update={"source_refs": {"mechanical_gate": mechanical_id, "audit": audit_id}})
        gate_id = self.register_artifact(
            book_id,
            chapter_no,
            "gate_decision",
            gate.model_dump(mode="json"),
            run.run_id,
        )
        quality = ChapterQualityRecord(
            chapter_no=chapter_no,
            revision_round=status.revision_round,
            settlement_artifact_id=settlement_artifact_id,
            candidate_signature=settlement_payload.candidate_signature,
            mechanical_gate_artifact_id=mechanical_id,
            audit_artifact_id=audit_id,
            gate_decision_artifact_id=gate_id,
            baseline_gate_artifact_id=settlement_payload.baseline_gate_ref,
            overall_score=gate.overall_score,
            dimension_scores=gate.dimension_scores,
            critical_count=gate.critical_count,
            blocked_by_mechanical=gate.blocked_by_mechanical,
            blocking_rule_ids=mechanical.blocking_rule_ids,
            reason_codes=gate.reason_codes,
            decision_status="pass" if gate.passed else "fail",
            truth_snapshot_artifact_id=truth_snapshot_id,
            truth_delta_artifact_id=truth_delta_id,
            truth_conflict_count=len(truth_delta.conflicts),
        )
        quality_id = self.register_artifact(
            book_id,
            chapter_no,
            "chapter_quality",
            quality.model_dump(mode="json"),
            run.run_id,
        )
        target = ChapterStage.AUDITED_PASSED if gate.passed else ChapterStage.AUDITED_FAILED
        next_record = self.save_status(
            status,
            target,
            run_id=run.run_id,
            artifact_refs={
                "mechanical_gate": mechanical_id,
                "audit": audit_id,
                "gate_decision": gate_id,
                "settlement": settlement_artifact_id,
                "chapter_quality": quality_id,
                "truth_delta": truth_delta_id,
                "truth_reconcile": reconcile_id,
            },
        )
        self.repo.json.append_chapter_note(
            book_id,
            chapter_no,
            {
                "pending_comparison": status.revision_round > 0,
                "latest_quality_artifact_id": quality_id,
            },
        )
        self.project_chapter_quality(book_id, quality, gate_id)
        self.finish_run(
            book_id,
            chapter_no,
            run.run_id,
            RunStatus.SUCCEEDED.value,
            [mechanical_id, audit_id, gate_id, quality_id],
            None,
        )
        return {
            "status": next_record,
            "mechanical_gate": mechanical.model_dump(mode="json"),
            "audit_report": audit.model_dump(mode="json"),
            "gate_decision": gate.model_dump(mode="json"),
            "gate_decision_artifact_id": gate_id,
            "chapter_quality": quality.model_dump(mode="json"),
            "chapter_quality_artifact_id": quality_id,
            "truth_delta": truth_delta.model_dump(mode="json"),
            "truth_delta_artifact_id": truth_delta_id,
            "truth_snapshot_artifact_id": truth_snapshot_id,
        }
