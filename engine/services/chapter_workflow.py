from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable

from engine.schemas.artifact import (
    ApprovalReceiptRecord,
    ChapterComposeRecord,
    ChapterPlanRecord,
    ChapterQualityRecord,
    ChapterSettlementRecord,
    ComparisonRecord,
    GateDecisionRecord,
    MechanicalGateRecord,
    RevisionBriefRecord,
    RevisionRecord,
    TruthDeltaRecord,
)
from engine.schemas.chapter import ChapterStage, ChapterStatusRecord
from engine.schemas.run import RunAction, RunStatus
from engine.state_machine import ChapterStateTransitionError, next_status
from engine.utils.chinese_text import count_chinese_chars, split_chinese_sentences
from .audit_workflow import AuditWorkflow
from .gate_runner import GateInputBundle
from .workflow_policies import decide_comparison, next_plateau_counter


@dataclass(slots=True)
class ChapterWorkflowService:
    repo: object
    gate_runner: object
    style_signal: object
    writer: object
    truth_service: object
    register_artifact: Callable[[str, int, str, dict, str], str]
    start_run: Callable[[str, int, str, str, list[str] | None], object]
    finish_run: Callable[[str, int, str, str, list[str], str | None], object]
    load_artifact_payload: Callable[[str, str], dict]
    optional_artifact_payload: Callable[[str, str | None], dict | None]
    max_revision_rounds: int
    plateau_delta: float
    plateau_limit: int
    utc_now: Callable[[], object]

    def plan_chapter(self, book_id: str, chapter_no: int, guidance: str | None = None) -> str:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        self._assert_not_frozen(status, "plan")
        if status.stage != ChapterStage.PLANNED:
            raise ValueError("plan_chapter requires planned chapter state")
        run = self.start_run(book_id, chapter_no, RunAction.PLAN.value, "planner", [])
        payload = ChapterPlanRecord(
            chapter_no=chapter_no,
            must_advance=["advance chapter conflict"],
            eligible_resolve=["resolve one local obstacle"],
            must_not_do=["do not reveal endgame early"],
            hook_target="information_flip",
            guidance=guidance,
        ).model_dump(mode="json")
        artifact_id = self.register_artifact(book_id, chapter_no, "plan", payload, run.run_id)
        updated = status.model_copy(
            update={
                "current_artifact_refs": {**status.current_artifact_refs, "plan": artifact_id},
                "last_run_id": run.run_id,
                "updated_at": self.utc_now(),
            }
        )
        self.repo.save_chapter_status(updated)
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [artifact_id], None)
        return artifact_id

    def compose_chapter(self, book_id: str, chapter_no: int) -> str:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        self._assert_not_frozen(status, "compose")
        plan_id = status.current_artifact_refs.get("plan")
        if not plan_id:
            raise ValueError("compose_chapter requires plan artifact")
        run = self.start_run(book_id, chapter_no, RunAction.COMPOSE.value, "composer", [plan_id])
        truth_context = self.truth_service._build_truth_context(book_id)
        compose = ChapterComposeRecord(
            chapter_no=chapter_no,
            source_refs=[plan_id],
            constraints=["preserve antagonistic tension"],
            materials=["hook goal", "chapter intent"],
            assembled_summary="Assembled chapter context for drafting.",
            truth_snapshot_id=truth_context["truth_snapshot_id"],
            truth_context_refs=truth_context["truth_context_refs"],
            truth_context_slice=truth_context["truth_context_slice"],
        )
        artifact_id = self.register_artifact(
            book_id,
            chapter_no,
            "compose_context",
            compose.model_dump(mode="json"),
            run.run_id,
        )
        next_record = next_status(
            status,
            ChapterStage.COMPOSED,
            run_id=run.run_id,
            artifact_refs={"compose": artifact_id},
        )
        self.repo.save_chapter_status(next_record)
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [artifact_id], None)
        return artifact_id

    def write_chapter_draft(self, book_id: str, chapter_no: int, mode: str = "initial") -> str:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        self._assert_not_frozen(status, "write")
        self.truth_service._assert_truth_fresh_for_action(book_id, chapter_no, "write")
        compose_id = status.current_artifact_refs.get("compose")
        if not compose_id:
            raise ValueError("write_chapter_draft requires compose_context artifact")
        if mode == "initial" and status.stage != ChapterStage.COMPOSED:
            raise ValueError("initial drafting requires composed stage")
        if mode != "initial" and status.stage != ChapterStage.REVISING:
            raise ValueError("revision drafting requires revising stage")
        inputs = [compose_id]
        if mode != "initial":
            brief_id = status.current_artifact_refs.get("revision_brief")
            if not brief_id:
                raise ValueError("revision drafting requires revision_brief artifact")
            inputs.append(brief_id)
        run = self.start_run(book_id, chapter_no, RunAction.WRITE.value, "writer", inputs)
        compose_payload = self.load_artifact_payload(book_id, compose_id)
        truth_context_slice = compose_payload.get("truth_context_slice", {})
        plan_payload = self.optional_artifact_payload(book_id, status.current_artifact_refs.get("plan")) or {}
        if mode == "initial":
            text = self.writer.generate_initial(
                chapter_no=chapter_no,
                plan_payload=plan_payload,
                compose_payload=compose_payload,
                truth_context_slice=truth_context_slice,
            )
        else:
            brief_payload = self.load_artifact_payload(book_id, status.current_artifact_refs["revision_brief"])
            audit_payload = self.optional_artifact_payload(book_id, status.current_artifact_refs.get("audit")) or {}
            mechanical_payload = self.optional_artifact_payload(book_id, status.current_artifact_refs.get("mechanical_gate")) or {}
            truth_delta_payload = self.optional_artifact_payload(book_id, status.current_artifact_refs.get("truth_delta")) or {}
            style_signal_payload = self.optional_artifact_payload(book_id, status.current_artifact_refs.get("style_signal")) or {}
            failed_rule_messages = [
                str(rule.get("message", "")).strip()
                for rule in mechanical_payload.get("rule_results", [])
                if isinstance(rule, dict) and not rule.get("passed", True) and str(rule.get("message", "")).strip()
            ]
            top_audit_issues = [
                f"{issue.get('description', '')}::{issue.get('suggestion', '')}".strip(":")
                for issue in audit_payload.get("issues", [])
                if isinstance(issue, dict) and str(issue.get("description", "")).strip()
            ]
            score_summary = audit_payload.get("score_summary", {})
            low_dimensions = []
            if isinstance(score_summary, dict):
                dimension_items = [
                    (key, float(value))
                    for key, value in score_summary.items()
                    if key in {"logic", "character", "hook", "pace"} and isinstance(value, (int, float))
                ]
                for key, value in sorted(dimension_items, key=lambda item: item[1])[:2]:
                    low_dimensions.append(f"{key}:{value}")
            truth_conflict_messages = [
                str(item.get("message", "")).strip()
                for item in truth_delta_payload.get("conflicts", [])
                if isinstance(item, dict) and str(item.get("message", "")).strip()
            ]
            style_drift_messages = [
                f"style_drift:{axis}"
                for axis in style_signal_payload.get("dominant_drift_axes", [])
                if str(axis).strip()
            ]
            text = self.writer.generate_revision(
                chapter_no=chapter_no,
                plan_payload=plan_payload,
                compose_payload=compose_payload,
                truth_context_slice=truth_context_slice,
                revision_brief=brief_payload,
                failed_rule_messages=failed_rule_messages + style_drift_messages,
                top_audit_issues=top_audit_issues,
                low_dimensions=low_dimensions,
                truth_conflict_messages=truth_conflict_messages,
                revision_mode=brief_payload.get("mode", "standard"),
            )
        payload = {"text": text, "mode": mode, "chapter_no": chapter_no, "revision_round": status.revision_round}
        artifact_id = self.register_artifact(book_id, chapter_no, "draft", payload, run.run_id)
        target = ChapterStage.DRAFTED if mode == "initial" else status.stage
        if mode == "initial":
            next_record = next_status(
                status,
                target,
                run_id=run.run_id,
                artifact_refs={"draft": artifact_id},
            )
        else:
            next_record = status.model_copy(
                update={
                    "current_artifact_refs": {**status.current_artifact_refs, "draft": artifact_id},
                    "last_run_id": run.run_id,
                    "updated_at": self.utc_now(),
                }
            )
        self.repo.save_chapter_status(next_record)
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [artifact_id], None)
        return artifact_id

    def settle_chapter(self, book_id: str, chapter_no: int, draft_artifact_id: str) -> ChapterStatusRecord:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        self._assert_not_frozen(status, "settle")
        self.truth_service._assert_truth_fresh_for_action(book_id, chapter_no, "settle")
        if status.stage not in {ChapterStage.DRAFTED, ChapterStage.REVISING}:
            raise ValueError("settle_chapter requires drafted or revising stage")
        draft_payload = self.load_artifact_payload(book_id, draft_artifact_id)
        text = draft_payload["text"]
        run = self.start_run(book_id, chapter_no, RunAction.SETTLE.value, "settler", [draft_artifact_id])
        compose_payload = self.optional_artifact_payload(book_id, status.current_artifact_refs.get("compose"))
        settlement = ChapterSettlementRecord(
            draft_artifact_id=draft_artifact_id,
            text_hash=sha256(text.encode("utf-8")).hexdigest(),
            word_count=count_chinese_chars(text),
            paragraph_count=max(1, text.count("\n\n") + 1),
            sentence_count=max(1, len(split_chinese_sentences(text))),
            baseline_gate_ref=status.current_artifact_refs.get("gate_decision"),
            audit_input_refs={
                key: value
                for key, value in status.current_artifact_refs.items()
                if key in {"plan", "compose", "draft", "revision_brief"}
            },
            candidate_signature=self.gate_runner.build_signature(text),
            base_truth_snapshot_id=(compose_payload or {}).get("truth_snapshot_id"),
        )
        artifact_id = self.register_artifact(
            book_id,
            chapter_no,
            "settlement",
            settlement.model_dump(mode="json"),
            run.run_id,
        )
        next_record = next_status(
            status,
            ChapterStage.SETTLED,
            run_id=run.run_id,
            artifact_refs={"draft": draft_artifact_id, "settlement": artifact_id},
        )
        self.repo.save_chapter_status(next_record)
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [artifact_id], None)
        return next_record

    def audit_chapter(self, book_id: str, chapter_no: int, settlement_artifact_id: str) -> dict:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        workflow = AuditWorkflow(
            repo=self.repo,
            gate_runner=self.gate_runner,
            truth_service=self.truth_service,
            register_artifact=self.register_artifact,
            start_run=self.start_run,
            finish_run=self.finish_run,
            load_artifact_payload=self.load_artifact_payload,
            optional_artifact_payload=self.optional_artifact_payload,
            project_chapter_quality=self._project_chapter_quality,
            save_status=self._save_status_target,
        )
        return workflow.run(
            book_id=book_id,
            chapter_no=chapter_no,
            status=status,
            settlement_artifact_id=settlement_artifact_id,
        )

    def revise_chapter(self, book_id: str, chapter_no: int, mode: str | None = None) -> dict:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        self._assert_not_frozen(status, "revise")
        self.truth_service._assert_truth_fresh_for_action(book_id, chapter_no, "revise")
        if status.stage not in {ChapterStage.AUDITED_FAILED, ChapterStage.ROLLED_BACK, ChapterStage.HUMAN_REVIEW_REQUIRED}:
            raise ValueError("revise_chapter requires audited_failed, rolled_back, or human_review_required stage")
        if status.revision_round >= self.max_revision_rounds:
            run = self.start_run(book_id, chapter_no, RunAction.REVISE.value, "reviser", [])
            escalated = next_status(
                status,
                ChapterStage.HUMAN_REVIEW_REQUIRED,
                run_id=run.run_id,
                artifact_refs={},
            )
            self.repo.save_chapter_status(escalated)
            self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [], None)
            return {"status": escalated, "revision_brief_artifact_id": None, "draft_artifact_id": None}
        if mode is None:
            raise ValueError("revise_chapter requires explicit mode")
        gate_id = status.current_artifact_refs.get("gate_decision")
        if not gate_id:
            raise ValueError("revise_chapter requires gate_decision artifact")
        audit_id = status.current_artifact_refs.get("audit")
        mechanical_id = status.current_artifact_refs.get("mechanical_gate")
        truth_delta_id = status.current_artifact_refs.get("truth_delta")
        if not audit_id or not mechanical_id:
            raise ValueError("revise_chapter requires audit and mechanical gate artifacts")
        gate_payload = GateDecisionRecord.model_validate(self.load_artifact_payload(book_id, gate_id))
        audit_payload = self.load_artifact_payload(book_id, audit_id)
        mechanical_payload = MechanicalGateRecord.model_validate(self.load_artifact_payload(book_id, mechanical_id))
        truth_delta_payload = (
            TruthDeltaRecord.model_validate(self.load_artifact_payload(book_id, truth_delta_id))
            if truth_delta_id
            else None
        )
        fix_targets = []
        for reason in gate_payload.reason_codes:
            fix_targets.append(reason)
        for issue in audit_payload.get("issues", []):
            fix_targets.append(issue["description"])
        for rule_id in mechanical_payload.blocking_rule_ids:
            fix_targets.append(f"remove_block:{rule_id}")
        if truth_delta_payload:
            for conflict in truth_delta_payload.conflicts:
                fix_targets.append(f"truth:{conflict.message}")
        run = self.start_run(book_id, chapter_no, RunAction.REVISE.value, "reviser", [gate_id])
        brief = RevisionBriefRecord(
            gate_decision_artifact_id=gate_id,
            fix_targets=fix_targets or ["reduce critical issues", "improve logic clarity"],
            must_not_touch=["do not change chapter premise", "do not break committed canon or hook state"],
            risk_points=["avoid overcorrecting tone", "do not introduce new mechanical blocks", "preserve truth consistency"],
            mode=mode or "standard",
        )
        brief_id = self.register_artifact(
            book_id,
            chapter_no,
            "revision_brief",
            brief.model_dump(mode="json"),
            run.run_id,
        )
        revising = next_status(
            status,
            ChapterStage.REVISING,
            run_id=run.run_id,
            artifact_refs={"revision_brief": brief_id},
        )
        self.repo.save_chapter_status(revising)
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [brief_id], None)
        draft_id = self.write_chapter_draft(book_id, chapter_no, mode="revision")
        refreshed = self.repo.load_chapter_status(book_id, chapter_no)
        return {"status": refreshed, "revision_brief_artifact_id": brief_id, "draft_artifact_id": draft_id}

    def compare_candidate(self, book_id: str, chapter_no: int, baseline_gate_id: str, candidate_gate_id: str) -> dict:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        self._assert_not_frozen(status, "compare")
        self.truth_service._assert_truth_fresh_for_action(book_id, chapter_no, "compare")
        run = self.start_run(book_id, chapter_no, RunAction.COMPARE.value, "comparator", [baseline_gate_id, candidate_gate_id])
        baseline_gate = GateDecisionRecord.model_validate(self.load_artifact_payload(book_id, baseline_gate_id))
        candidate_gate = GateDecisionRecord.model_validate(self.load_artifact_payload(book_id, candidate_gate_id))
        baseline_quality_id = self._find_quality_artifact_id(book_id, baseline_gate_id)
        candidate_quality_id = self._find_quality_artifact_id(book_id, candidate_gate_id)
        baseline_quality = ChapterQualityRecord.model_validate(self.load_artifact_payload(book_id, baseline_quality_id))
        candidate_quality = ChapterQualityRecord.model_validate(self.load_artifact_payload(book_id, candidate_quality_id))
        delta_by_dimension = {
            key: candidate_gate.dimension_scores.get(key, 0.0) - baseline_gate.dimension_scores.get(key, 0.0)
            for key in set(baseline_gate.dimension_scores) | set(candidate_gate.dimension_scores)
        }
        delta_overall = candidate_gate.overall_score - baseline_gate.overall_score
        critical_delta = candidate_gate.critical_count - baseline_gate.critical_count
        decision = decide_comparison(baseline_gate, candidate_gate, delta_overall, delta_by_dimension, critical_delta)
        comparison = ComparisonRecord(
            baseline_settlement_id=baseline_quality.settlement_artifact_id,
            candidate_settlement_id=candidate_quality.settlement_artifact_id,
            baseline_gate_id=baseline_gate_id,
            candidate_gate_id=candidate_gate_id,
            delta_overall=delta_overall,
            delta_by_dimension=delta_by_dimension,
            critical_delta=critical_delta,
            decision=decision["decision"],
            reason_codes=decision["reason_codes"],
        )
        comparison_id = self.register_artifact(
            book_id,
            chapter_no,
            "comparison",
            comparison.model_dump(mode="json"),
            run.run_id,
        )
        baseline_settlement = ChapterSettlementRecord.model_validate(
            self.load_artifact_payload(book_id, baseline_quality.settlement_artifact_id)
        )
        candidate_settlement = ChapterSettlementRecord.model_validate(
            self.load_artifact_payload(book_id, candidate_quality.settlement_artifact_id)
        )
        baseline_draft = self.load_artifact_payload(book_id, baseline_settlement.draft_artifact_id)
        candidate_draft = self.load_artifact_payload(book_id, candidate_settlement.draft_artifact_id)
        style_signal = self.style_signal.evaluate_pairwise(
            baseline_text=str(baseline_draft.get("text", "")),
            candidate_text=str(candidate_draft.get("text", "")),
            chapter_no=chapter_no,
            reference_profile_ref=baseline_quality.settlement_artifact_id,
        )
        style_signal_id: str | None = None
        if style_signal is not None:
            style_signal_id = self.register_artifact(
                book_id,
                chapter_no,
                "style_signal",
                style_signal.model_dump(mode="json"),
                run.run_id,
            )
            self.repo.save_style_signal(book_id=book_id, batch_run_id=None, artifact_id=style_signal_id, payload=style_signal, created_at=self.utc_now().isoformat())
        plateau_counter = next_plateau_counter(
            current_counter=int(self.repo.json.load_chapter_note(book_id, chapter_no).get("plateau_counter", 0)),
            candidate_gate=candidate_gate,
            delta_overall=delta_overall,
            critical_delta=critical_delta,
            plateau_delta=self.plateau_delta,
        )
        self.repo.json.append_chapter_note(
            book_id,
            chapter_no,
            {
                "pending_comparison": False,
                "plateau_counter": plateau_counter,
                "last_comparison_artifact_id": comparison_id,
            },
        )
        if plateau_counter >= self.plateau_limit:
            if candidate_gate.passed:
                refreshed = self.repo.load_chapter_status(book_id, chapter_no)
                final_status = self._apply_status_target(
                    refreshed,
                    ChapterStage.AUDITED_PASSED,
                    run_id=run.run_id,
                    artifact_refs={"comparison": comparison_id, **({"style_signal": style_signal_id} if style_signal_id else {})},
                )
                decision = {"decision": "keep", "reason_codes": ["plateau_pass_stop"]}
            else:
                refreshed = self.repo.load_chapter_status(book_id, chapter_no)
                final_status = self._apply_status_target(
                    refreshed,
                    ChapterStage.HUMAN_REVIEW_REQUIRED,
                    run_id=run.run_id,
                    artifact_refs={"comparison": comparison_id, **({"style_signal": style_signal_id} if style_signal_id else {})},
                )
                decision = {"decision": "rollback", "reason_codes": ["plateau_fail_escalate"]}
            self.repo.save_chapter_status(final_status)
        elif decision["decision"] == "rollback":
            final_status = self._apply_status_target(
                status,
                ChapterStage.ROLLED_BACK,
                run_id=run.run_id,
                artifact_refs={"comparison": comparison_id, **({"style_signal": style_signal_id} if style_signal_id else {})},
            )
            self.repo.save_chapter_status(final_status)
        else:
            next_stage = ChapterStage.AUDITED_PASSED if candidate_gate.passed else ChapterStage.AUDITED_FAILED
            refreshed = self.repo.load_chapter_status(book_id, chapter_no)
            final_status = self._apply_status_target(
                refreshed,
                next_stage,
                run_id=run.run_id,
                artifact_refs={"comparison": comparison_id, **({"style_signal": style_signal_id} if style_signal_id else {})},
            )
            self.repo.save_chapter_status(final_status)
        output_refs = [comparison_id, *( [style_signal_id] if style_signal_id else [] )]
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, output_refs, None)
        return {
            "decision": decision["decision"],
            "comparison_artifact_id": comparison_id,
            "style_signal_artifact_id": style_signal_id,
            "status": final_status,
        }

    def approve_chapter(self, book_id: str, chapter_no: int) -> ChapterStatusRecord:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        self.truth_service._assert_truth_fresh_for_action(book_id, chapter_no, "approve")
        notes = self.repo.json.load_chapter_note(book_id, chapter_no)
        if notes.get("pending_comparison"):
            raise ValueError("approve_chapter requires resolved comparison state")
        if status.stage != ChapterStage.AUDITED_PASSED:
            raise ValueError("approve_chapter requires audited_passed stage")
        draft_id = status.current_artifact_refs.get("draft")
        if not draft_id:
            raise ValueError("approve_chapter requires accepted draft artifact")
        quality_id = status.current_artifact_refs.get("chapter_quality")
        if not quality_id:
            raise ValueError("approve_chapter requires chapter_quality artifact")
        quality = ChapterQualityRecord.model_validate(self.load_artifact_payload(book_id, quality_id))
        if quality.settlement_artifact_id != status.current_artifact_refs.get("settlement"):
            raise ValueError("approve_chapter requires current settlement to match passing quality record")
        run = self.start_run(book_id, chapter_no, RunAction.APPROVE.value, "system", [draft_id])
        truth_receipt_id = self.truth_service._commit_truth_from_chapter(book_id, chapter_no, status, run.run_id)
        draft_payload = self.load_artifact_payload(book_id, draft_id)
        chapter_path = self.repo.json.ensure_book_dirs(book_id)["chapters"] / f"{chapter_no:04d}.md"
        chapter_text = draft_payload["text"]
        chapter_path.write_text(chapter_text, encoding="utf-8")
        chapter_file_sha256 = sha256(chapter_text.encode("utf-8")).hexdigest()
        chapter_semantic_sha256 = self._semantic_hash(chapter_text)
        receipt_id = self.register_artifact(
            book_id,
            chapter_no,
            "approval_receipt",
            ApprovalReceiptRecord(
                draft_artifact_id=draft_id,
                published_path=str(chapter_path.name),
                chapter_file_sha256=chapter_file_sha256,
                chapter_semantic_sha256=chapter_semantic_sha256,
                settlement_artifact_id=status.current_artifact_refs["settlement"],
                truth_commit_receipt_artifact_id=truth_receipt_id,
            ).model_dump(mode="json"),
            run.run_id,
        )
        invalidated = self.truth_service._detect_propagation_debts(book_id, truth_receipt_id, run.run_id)
        audit_id = status.current_artifact_refs.get("audit")
        if not audit_id:
            raise ValueError("approve_chapter requires accepted audit artifact")
        approved = next_status(
            status,
            ChapterStage.APPROVED,
            run_id=run.run_id,
            artifact_refs={"approval_receipt": receipt_id, "audit": audit_id, "truth_commit_receipt": truth_receipt_id},
        )
        self.repo.save_chapter_status(approved)
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [receipt_id, *invalidated], None)
        return approved

    def _find_quality_artifact_id(self, book_id: str, gate_decision_artifact_id: str) -> str:
        for artifact in self.repo.json.list_artifacts(book_id):
            if artifact.artifact_type.value != "chapter_quality":
                continue
            payload = self.load_artifact_payload(book_id, artifact.artifact_id)
            if payload.get("gate_decision_artifact_id") == gate_decision_artifact_id:
                return artifact.artifact_id
        raise FileNotFoundError(f"chapter_quality not found for gate decision: {gate_decision_artifact_id}")

    def _project_chapter_quality(self, book_id: str, quality: ChapterQualityRecord, gate_id: str) -> None:
        self.repo._read_model(book_id).upsert_chapter_quality(
            book_id=book_id,
            chapter_no=quality.chapter_no,
            gate_decision_artifact_id=gate_id,
            settlement_artifact_id=quality.settlement_artifact_id,
            overall_score=quality.overall_score,
            critical_count=quality.critical_count,
            blocked_by_mechanical=quality.blocked_by_mechanical,
            blocking_rule_ids_json=json.dumps(quality.blocking_rule_ids, ensure_ascii=False),
            reason_codes_json=json.dumps(quality.reason_codes, ensure_ascii=False),
            decision_status=quality.decision_status,
            created_at=self.utc_now().isoformat(),
        )

    @staticmethod
    def _assert_not_frozen(status: ChapterStatusRecord, action: str) -> None:
        if status.stage in {ChapterStage.APPROVED, ChapterStage.EXPORTED}:
            raise ValueError(f"chapter {status.chapter_no} is frozen and cannot {action}")

    def _apply_status_target(
        self,
        status: ChapterStatusRecord,
        target: ChapterStage,
        *,
        run_id: str,
        artifact_refs: dict[str, str],
    ) -> ChapterStatusRecord:
        if status.stage == target:
            merged_refs = dict(status.current_artifact_refs)
            merged_refs.update(artifact_refs)
            return status.model_copy(
                update={
                    "current_artifact_refs": merged_refs,
                    "last_run_id": run_id,
                    "updated_at": self.utc_now(),
                }
            )
        return next_status(
            status,
            target,
            run_id=run_id,
            artifact_refs=artifact_refs,
        )

    def _save_status_target(
        self,
        status: ChapterStatusRecord,
        target: ChapterStage,
        *,
        run_id: str,
        artifact_refs: dict[str, str],
    ) -> ChapterStatusRecord:
        next_record = self._apply_status_target(
            status,
            target,
            run_id=run_id,
            artifact_refs=artifact_refs,
        )
        self.repo.save_chapter_status(next_record)
        return next_record

    @staticmethod
    def _semantic_hash(text: str) -> str:
        normalized = "".join(ch for ch in text if not ch.isspace() and ch not in "，。！？,.!?;；:\"'`()[]{}<>《》、")
        return sha256(normalized.encode("utf-8")).hexdigest()
