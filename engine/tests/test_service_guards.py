from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.schemas.artifact import AuditRecord
from engine.schemas.chapter import ChapterStage
from engine.schemas.run import RunAction
from engine.services import StoryEngineService
from engine.state_machine import ChapterStateTransitionError, MissingRunContextError
from engine.truth.truth_extractor_adapter import TruthExtractorAdapter


class PassingAuditor:
    def review(self, bundle) -> AuditRecord:
        return AuditRecord(
            passed=True,
            critical_count=0,
            issues=[],
            recommended_mode="accept",
            score_summary={"overall": 6.8, "logic": 6.6, "character": 6.8, "hook": 6.9, "pace": 6.7},
        )


class MissingDimensionAuditor:
    def review(self, bundle) -> AuditRecord:
        return AuditRecord(
            passed=True,
            critical_count=0,
            issues=[],
            recommended_mode="accept",
            score_summary={"overall": 6.8, "logic": 6.6, "character": 6.8, "hook": 6.9},
        )


class PlateauPassingAuditor:
    def review(self, bundle) -> AuditRecord:
        return AuditRecord(
            passed=True,
            critical_count=0,
            issues=[],
            recommended_mode="accept",
            score_summary={"overall": 6.2, "logic": 6.1, "character": 6.2, "hook": 6.3, "pace": 6.2},
        )


class StoryEngineServiceGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.service = StoryEngineService(self.root)
        self.service.create_book({"book_id": "test-book", "title": "Test Book"})
        self.service.init_chapter("test-book", 1)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cannot_approve_before_audit_pass(self) -> None:
        run = self.service.start_run("test-book", 1, RunAction.COMPOSE.value, "planner", [])
        self.service.transition_chapter("test-book", 1, "composed", run.run_id, {})
        run2 = self.service.start_run("test-book", 1, RunAction.WRITE.value, "writer", [])
        self.service.transition_chapter("test-book", 1, "drafted", run2.run_id, {})
        run3 = self.service.start_run("test-book", 1, RunAction.APPROVE.value, "system", [])
        with self.assertRaises(ChapterStateTransitionError):
            self.service.transition_chapter("test-book", 1, "approved", run3.run_id, {})

    def test_cannot_settle_without_draft_artifact(self) -> None:
        run = self.service.start_run("test-book", 1, RunAction.COMPOSE.value, "planner", [])
        self.service.transition_chapter("test-book", 1, "composed", run.run_id, {})
        run2 = self.service.start_run("test-book", 1, RunAction.WRITE.value, "writer", [])
        self.service.transition_chapter("test-book", 1, "drafted", run2.run_id, {})
        run3 = self.service.start_run("test-book", 1, RunAction.SETTLE.value, "system", [])
        with self.assertRaises(MissingRunContextError):
            self.service.transition_chapter("test-book", 1, "settled", run3.run_id, {})

    def test_cannot_compose_without_plan_artifact(self) -> None:
        with self.assertRaises(ValueError):
            self.service.compose_chapter("test-book", 1)

    def test_cannot_write_without_compose_context(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        with self.assertRaises(ValueError):
            self.service.write_chapter_draft("test-book", 1, mode="initial")

    def test_happy_path_with_artifacts(self) -> None:
        plan_id = self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        compose_id = self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")

        settled = self.service.settle_chapter("test-book", 1, draft_id)
        self.assertEqual(settled.stage.value, "settled")
        settlement_ref = settled.current_artifact_refs["settlement"]

        first_decision = self.service.audit_chapter("test-book", 1, settlement_ref)
        self.assertFalse(first_decision["gate_decision"]["passed"])
        self.assertEqual(first_decision["status"].stage.value, "audited_failed")

        revision = self.service.revise_chapter("test-book", 1, mode="surgical")
        revised_settled = self.service.settle_chapter("test-book", 1, revision["draft_artifact_id"])
        revised_decision = self.service.audit_chapter("test-book", 1, revised_settled.current_artifact_refs["settlement"])
        comparison = self.service.compare_candidate(
            "test-book",
            1,
            first_decision["gate_decision_artifact_id"],
            revised_decision["gate_decision_artifact_id"],
        )
        self.assertEqual(comparison["decision"], "keep")
        self.assertEqual(comparison["status"].stage.value, "audited_passed")

        approved = self.service.approve_chapter("test-book", 1)
        self.assertEqual(approved.stage.value, "approved")

        chapter_path = self.root / "books" / "test-book" / "chapters" / "0001.md"
        self.assertTrue(chapter_path.exists())
        chapter_text = chapter_path.read_text(encoding="utf-8")
        self.assertIn("林七", chapter_text)
        self.assertIn(plan_id, json.dumps(self.service.get_chapter_status("test-book", 1)["status"].current_artifact_refs))
        self.assertIn(compose_id, json.dumps(self.service.get_chapter_status("test-book", 1)["status"].current_artifact_refs))

    def test_settle_does_not_publish_chapter_file(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        self.service.settle_chapter("test-book", 1, draft_id)
        chapter_path = self.root / "books" / "test-book" / "chapters" / "0001.md"
        self.assertFalse(chapter_path.exists())

    def test_approve_requires_no_pending_comparison(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        self.service.repo.json.append_chapter_note("test-book", 1, {"pending_comparison": True})
        with self.assertRaises(ValueError):
            self.service.approve_chapter("test-book", 1)

    def test_revise_requires_failed_audit(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        with self.assertRaises(ValueError):
            self.service.revise_chapter("test-book", 1)

    def test_revise_accepts_rolled_back_stage(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        first_gate = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        revision = self.service.revise_chapter("test-book", 1, mode="rework")
        revised_draft = revision["draft_artifact_id"]
        revised_settled = self.service.settle_chapter("test-book", 1, revised_draft)
        revised_gate = self.service.audit_chapter("test-book", 1, revised_settled.current_artifact_refs["settlement"])
        comparison = self.service.compare_candidate(
            "test-book",
            1,
            first_gate["gate_decision_artifact_id"],
            revised_gate["gate_decision_artifact_id"],
        )
        self.assertEqual(comparison["status"].stage.value, "rolled_back")

        retry = self.service.revise_chapter("test-book", 1, mode="rework")

        self.assertEqual(retry["status"].stage.value, "revising")
        self.assertTrue(retry["draft_artifact_id"])

    def test_compare_candidate_rolls_back_on_regression(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        first_draft = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, first_draft)
        first_gate = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        self.assertFalse(first_gate["gate_decision"]["passed"])

        revision = self.service.revise_chapter("test-book", 1, mode="rework")
        self.assertEqual(revision["status"].stage.value, "revising")
        revised_draft = revision["draft_artifact_id"]
        revised_settled = self.service.settle_chapter("test-book", 1, revised_draft)
        revised_gate = self.service.audit_chapter("test-book", 1, revised_settled.current_artifact_refs["settlement"])

        comparison = self.service.compare_candidate(
            "test-book",
            1,
            first_gate["gate_decision_artifact_id"],
            revised_gate["gate_decision_artifact_id"],
        )
        self.assertEqual(comparison["decision"], "rollback")
        status = self.service.get_chapter_status("test-book", 1)["status"]
        self.assertEqual(status.stage.value, "rolled_back")

    def test_compare_candidate_keeps_improved_passed_version(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        first_draft = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, first_draft)
        first_gate = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])

        revision = self.service.revise_chapter("test-book", 1, mode="surgical")
        revised_draft = revision["draft_artifact_id"]
        revised_settled = self.service.settle_chapter("test-book", 1, revised_draft)
        revised_gate = self.service.audit_chapter("test-book", 1, revised_settled.current_artifact_refs["settlement"])

        comparison = self.service.compare_candidate(
            "test-book",
            1,
            first_gate["gate_decision_artifact_id"],
            revised_gate["gate_decision_artifact_id"],
        )
        self.assertEqual(comparison["decision"], "keep")
        status = self.service.get_chapter_status("test-book", 1)["status"]
        self.assertEqual(status.stage.value, "audited_passed")

    def test_mechanical_block_still_records_quality_when_auditor_passes(self) -> None:
        self.service.gate_runner.auditor = PassingAuditor()
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        compose_id = self.service.get_chapter_status("test-book", 1)["status"].current_artifact_refs["compose"]
        run = self.service.start_run("test-book", 1, RunAction.WRITE.value, "writer", [compose_id])
        draft_id = self.service.register_artifact(
            "test-book",
            1,
            "draft",
            {
                "text": "This candidate keeps the scene readable but includes a forbidden token for the mechanical gate.",
                "mode": "initial",
                "chapter_no": 1,
                "revision_round": 0,
            },
            run.run_id,
        )
        self.service.transition_chapter("test-book", 1, "drafted", run.run_id, {"draft": draft_id})
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        audit = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        self.assertFalse(audit["gate_decision"]["passed"])
        self.assertTrue(audit["gate_decision"]["blocked_by_mechanical"])
        self.assertTrue(audit["chapter_quality"]["blocking_rule_ids"])

    def test_audit_failure_keeps_chapter_settled_when_core_dimensions_missing(self) -> None:
        self.service.gate_runner.auditor = MissingDimensionAuditor()
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        with self.assertRaises(ValueError):
            self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        status = self.service.get_chapter_status("test-book", 1)["status"]
        self.assertEqual(status.stage.value, "settled")
        artifact_types = {artifact.artifact_type.value for artifact in self.service.repo.json.list_artifacts("test-book")}
        self.assertNotIn("gate_decision", artifact_types)
        self.assertNotIn("chapter_quality", artifact_types)

    def test_re_audit_sets_pending_comparison_until_compare_runs(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        first_draft = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, first_draft)
        first_gate = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        revision = self.service.revise_chapter("test-book", 1, mode="surgical")
        revised_settled = self.service.settle_chapter("test-book", 1, revision["draft_artifact_id"])
        revised_gate = self.service.audit_chapter("test-book", 1, revised_settled.current_artifact_refs["settlement"])
        notes = self.service.repo.json.load_chapter_note("test-book", 1)
        self.assertTrue(notes.get("pending_comparison"))
        self.service.compare_candidate(
            "test-book",
            1,
            first_gate["gate_decision_artifact_id"],
            revised_gate["gate_decision_artifact_id"],
        )
        notes = self.service.repo.json.load_chapter_note("test-book", 1)
        self.assertFalse(notes.get("pending_comparison"))

    def test_approve_rejects_stale_pass_when_settlement_ref_changes(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        first_draft = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, first_draft)
        first_gate = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        revision = self.service.revise_chapter("test-book", 1, mode="surgical")
        revised_settled = self.service.settle_chapter("test-book", 1, revision["draft_artifact_id"])
        revised_gate = self.service.audit_chapter("test-book", 1, revised_settled.current_artifact_refs["settlement"])
        self.service.compare_candidate(
            "test-book",
            1,
            first_gate["gate_decision_artifact_id"],
            revised_gate["gate_decision_artifact_id"],
        )
        status = self.service.get_chapter_status("test-book", 1)["status"]
        tampered = status.model_copy(
            update={
                "current_artifact_refs": {
                    **status.current_artifact_refs,
                    "settlement": "stale-settlement-ref",
                }
            }
        )
        self.service.repo.save_chapter_status(tampered)
        with self.assertRaises(ValueError):
            self.service.approve_chapter("test-book", 1)

    def test_compose_records_truth_snapshot_context(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        compose_id = self.service.compose_chapter("test-book", 1)
        compose_payload = self.service._load_artifact_payload("test-book", compose_id)
        self.assertIn("truth_snapshot_id", compose_payload)
        self.assertTrue(compose_payload["truth_snapshot_id"])

    def test_settle_records_base_truth_snapshot(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        settlement_payload = self.service._load_artifact_payload(
            "test-book", settled.current_artifact_refs["settlement"]
        )
        self.assertIn("base_truth_snapshot_id", settlement_payload)
        self.assertTrue(settlement_payload["base_truth_snapshot_id"])

    def test_audit_emits_truth_delta_and_links_quality_to_truth(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        audit = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        self.assertIn("truth_delta", audit)
        self.assertIn("truth_delta_artifact_id", audit)
        self.assertIn("truth_snapshot_artifact_id", audit)
        self.assertEqual(
            audit["chapter_quality"]["truth_delta_artifact_id"],
            audit["truth_delta_artifact_id"],
        )

    def test_approve_commits_truth_and_advances_truth_head(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        first_draft = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, first_draft)
        first_gate = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        revision = self.service.revise_chapter("test-book", 1, mode="surgical")
        revised_settled = self.service.settle_chapter("test-book", 1, revision["draft_artifact_id"])
        revised_gate = self.service.audit_chapter("test-book", 1, revised_settled.current_artifact_refs["settlement"])
        self.service.compare_candidate(
            "test-book",
            1,
            first_gate["gate_decision_artifact_id"],
            revised_gate["gate_decision_artifact_id"],
        )
        before = self.service.get_truth_head("test-book")
        approved = self.service.approve_chapter("test-book", 1)
        after = self.service.get_truth_head("test-book")
        self.assertEqual(approved.stage.value, "approved")
        self.assertNotEqual(before["truth_index"]["current_snapshot_id"], after["truth_index"]["current_snapshot_id"])
        self.assertEqual(after["truth_index"]["committed_through_chapter"], 1)

    def test_reset_invalidated_chapter_clears_runtime_refs(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        audit = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        status = self.service.get_chapter_status("test-book", 1)["status"].model_copy(
            update={
                "stage": ChapterStage.INVALIDATED,
                "invalidated_by": "truth-commit-1",
                "current_artifact_refs": {
                    **self.service.get_chapter_status("test-book", 1)["status"].current_artifact_refs,
                    "comparison": "cmp-1",
                    "approval_receipt": "approval-1",
                    "truth_commit_receipt": "receipt-1",
                    "export_manifest": "manifest-1",
                },
            }
        )
        self.service.repo.save_chapter_status(status)
        reset = self.service.reset_invalidated_chapter("test-book", 1)
        self.assertEqual(reset.stage.value, "planned")
        self.assertEqual(reset.current_artifact_refs, {"plan": status.current_artifact_refs["plan"]})
        self.assertIsNone(reset.invalidated_by)
        self.assertEqual(self.service.get_chapter_truth_freshness("test-book", 1)["basis_snapshot_id"], None)
        self.assertIn(audit["gate_decision_artifact_id"], self.service._load_artifact_payload("test-book", audit["chapter_quality_artifact_id"])["gate_decision_artifact_id"])

    def test_plateau_stop_keeps_passed_best_candidate(self) -> None:
        self.service.gate_runner.auditor = PlateauPassingAuditor()
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        first_draft = self.service.write_chapter_draft("test-book", 1, mode="initial")
        first_settled = self.service.settle_chapter("test-book", 1, first_draft)
        first_gate = self.service.audit_chapter("test-book", 1, first_settled.current_artifact_refs["settlement"])
        self.assertTrue(first_gate["gate_decision"]["passed"])

        for _ in range(2):
            status = self.service.get_chapter_status("test-book", 1)["status"].model_copy(
                update={"stage": ChapterStage.AUDITED_FAILED}
            )
            self.service.repo.save_chapter_status(status)
            revision = self.service.revise_chapter("test-book", 1, mode="surgical")
            revised_settled = self.service.settle_chapter("test-book", 1, revision["draft_artifact_id"])
            revised_gate = self.service.audit_chapter(
                "test-book",
                1,
                revised_settled.current_artifact_refs["settlement"],
            )
            notes = self.service.repo.json.load_chapter_note("test-book", 1)
            notes["plateau_counter"] = int(notes.get("plateau_counter", 0))
            comparison = self.service.compare_candidate(
                "test-book",
                1,
                revised_gate["gate_decision_artifact_id"],
                revised_gate["gate_decision_artifact_id"],
            )
            self.assertEqual(comparison["decision"], "keep")

        status = self.service.get_chapter_status("test-book", 1)["status"]
        notes = self.service.repo.json.load_chapter_note("test-book", 1)
        self.assertEqual(status.stage.value, "audited_passed")
        self.assertGreaterEqual(int(notes.get("plateau_counter", 0)), 2)

    def test_max_revision_rounds_escalates_to_human_review(self) -> None:
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        status = self.service.get_chapter_status("test-book", 1)["status"].model_copy(
            update={"revision_round": 5}
        )
        self.service.repo.save_chapter_status(status)
        result = self.service.revise_chapter("test-book", 1, mode="rework")
        self.assertEqual(result["status"].stage.value, "human_review_required")

    def test_approved_and_exported_chapters_reject_mutating_workflow_apis(self) -> None:
        self._approved_chapter()
        with self.assertRaises(ValueError):
            self.service.write_chapter_draft("test-book", 1, mode="initial")
        with self.assertRaises(ValueError):
            self.service.settle_chapter("test-book", 1, self.service.get_chapter_status("test-book", 1)["status"].current_artifact_refs["draft"])
        with self.assertRaises(ValueError):
            self.service.revise_chapter("test-book", 1, mode="surgical")
        approved_status = self.service.get_chapter_status("test-book", 1)["status"]
        with self.assertRaises(ValueError):
            self.service.compare_candidate(
                "test-book",
                1,
                approved_status.current_artifact_refs["gate_decision"],
                approved_status.current_artifact_refs["gate_decision"],
            )

        self.service.export_chapter("test-book", 1)
        with self.assertRaises(ValueError):
            self.service.plan_chapter("test-book", 1, guidance="retry")
        with self.assertRaises(ValueError):
            self.service.compose_chapter("test-book", 1)

    def _approved_chapter(self) -> None:
        self.service.gate_runner.auditor = PassingAuditor()
        self.service.plan_chapter("test-book", 1, guidance="keep the hook sharp")
        self.service.compose_chapter("test-book", 1)
        draft_id = self.service.write_chapter_draft("test-book", 1, mode="initial")
        settled = self.service.settle_chapter("test-book", 1, draft_id)
        audit = self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])
        self.assertTrue(audit["gate_decision"]["passed"])
        approved = self.service.approve_chapter("test-book", 1)
        self.assertEqual(approved.stage.value, "approved")

    def test_truth_reconcile_warns_on_character_location_change(self) -> None:
        self._install_truth_extractor(
            [
                self._truth_payload(
                    fact_assertions=[{"fact_id": "fact-1", "statement": "林七进入旧仓库"}],
                    character_updates=[{"character_id": "char-linqi", "display_name": "林七", "current_location": "旧仓库", "known_fact_ids": ["fact-1"]}],
                    hook_updates=[{"hook_id": "hook-ledger", "label": "旧账册", "kind": "hook", "status": "open", "introduced_in": 1}],
                ),
                self._truth_payload(
                    fact_assertions=[{"fact_id": "fact-2", "statement": "林七仍被确认停留在码头"}],
                    character_updates=[{"character_id": "char-linqi", "display_name": "林七", "current_location": "码头", "known_fact_ids": ["fact-1", "fact-2"]}],
                    hook_updates=[{"hook_id": "hook-ledger", "label": "旧账册", "kind": "hook", "status": "advanced", "introduced_in": 1}],
                ),
            ]
        )
        self.service.gate_runner.auditor = PassingAuditor()
        self._approve_single_pass_chapter(1)
        settled = self._prepare_settled_chapter(2)

        audit = self.service.audit_chapter("test-book", 2, settled.current_artifact_refs["settlement"])
        location_warnings = [
            c for c in audit["truth_delta"]["conflicts"]
            if c["category"] == "character_location_conflict"
        ]
        self.assertTrue(location_warnings)
        self.assertEqual(location_warnings[0]["severity"], "warning")
        self.assertTrue(audit["gate_decision"]["passed"])

    def test_truth_reconcile_blocks_knowledge_boundary_conflict(self) -> None:
        self._install_truth_extractor(
            [
                self._truth_payload(
                    fact_assertions=[{"fact_id": "fact-1", "statement": "林七进入旧仓库"}],
                    character_updates=[{"character_id": "char-linqi", "display_name": "林七", "current_location": "旧仓库", "known_fact_ids": ["fact-1"]}],
                ),
                self._truth_payload(
                    fact_assertions=[{"fact_id": "fact-2", "statement": "林七听见楼上脚步"}],
                    character_updates=[{"character_id": "char-linqi", "display_name": "林七", "current_location": "旧仓库", "known_fact_ids": ["fact-1", "unknown-fact"]}],
                ),
            ]
        )
        self.service.gate_runner.auditor = PassingAuditor()
        self._approve_single_pass_chapter(1)
        settled = self._prepare_settled_chapter(2)

        with self.assertRaisesRegex(ValueError, "truth reconcile produced blocking conflicts"):
            self.service.audit_chapter("test-book", 2, settled.current_artifact_refs["settlement"])

    def test_truth_reconcile_blocks_illegal_hook_transition(self) -> None:
        self._install_truth_extractor(
            [
                self._truth_payload(
                    fact_assertions=[{"fact_id": "fact-1", "statement": "旧账册线索被推进"}],
                    hook_updates=[{"hook_id": "hook-ledger", "label": "旧账册", "kind": "hook", "status": "advanced", "introduced_in": 1}],
                ),
                self._truth_payload(
                    fact_assertions=[{"fact_id": "fact-2", "statement": "旧账册线索被错误回退"}],
                    hook_updates=[{"hook_id": "hook-ledger", "label": "旧账册", "kind": "hook", "status": "open", "introduced_in": 1}],
                ),
            ]
        )
        self.service.gate_runner.auditor = PassingAuditor()
        self._approve_single_pass_chapter(1)
        settled = self._prepare_settled_chapter(2)

        with self.assertRaisesRegex(ValueError, "truth reconcile produced blocking conflicts"):
            self.service.audit_chapter("test-book", 2, settled.current_artifact_refs["settlement"])

    def test_truth_reconcile_blocks_hard_canon_rewrite(self) -> None:
        truth = self.service.repo.json.load_truth_payloads("test-book")
        truth["canon"]["facts"].append(
            {
                "fact_id": "canon-law-1",
                "category": "world_rule",
                "statement": "城南夜禁后不准通行",
                "hard": True,
                "assertion_basis": "explicit",
                "source_ref": "seed",
            }
        )
        self.service.repo.json.write_truth_payloads(
            "test-book",
            canon=truth["canon"],
            characters=truth["characters"],
            hook_ledger=truth["hook_ledger"],
            chapter_facts=truth["chapter_facts"],
        )
        self.service.repo.json.save_truth_snapshot_bundle(
            "test-book",
            snapshot_id="snapshot-0000",
            snapshot_payload={
                "snapshot_id": "snapshot-0000",
                "base_snapshot_id": None,
                "committed_through_chapter": 0,
                "created_by_run_id": "seed-run",
            },
            canon=truth["canon"],
            characters=truth["characters"],
            hook_ledger=truth["hook_ledger"],
            chapter_facts=truth["chapter_facts"],
        )
        self._install_truth_extractor(
            [
                self._truth_payload(
                    fact_assertions=[],
                    proposed_fact_updates=[{"fact_id": "canon-law-1", "category": "world_rule", "statement": "城南夜禁后仍可随意通行", "hard": True}],
                )
            ]
        )
        self.service.gate_runner.auditor = PassingAuditor()
        settled = self._prepare_settled_chapter(1)

        with self.assertRaisesRegex(ValueError, "truth reconcile produced blocking conflicts"):
            self.service.audit_chapter("test-book", 1, settled.current_artifact_refs["settlement"])

    def test_truth_reconcile_patches_existing_character_without_duplication(self) -> None:
        self._install_truth_extractor(
            [
                self._truth_payload(
                    fact_assertions=[{"fact_id": "fact-1", "statement": "林七进入旧仓库"}],
                    character_updates=[{"character_id": "char-linqi", "display_name": "林七", "current_location": "旧仓库", "known_fact_ids": ["fact-1"]}],
                ),
                self._truth_payload(
                    fact_assertions=[{"fact_id": "fact-2", "statement": "林七发现缺页账册"}],
                    character_updates=[{"character_id": "char-linqi", "display_name": "林七", "current_location": "旧仓库", "known_fact_ids": ["fact-1", "fact-2"], "status_tags": ["active", "injured"]}],
                ),
            ]
        )
        self.service.gate_runner.auditor = PassingAuditor()
        self._approve_single_pass_chapter(1)
        self._approve_single_pass_chapter(2)

        truth_head = self.service.get_truth_head("test-book")
        snapshot_id = truth_head["truth_index"]["current_snapshot_id"]
        characters = self.service.repo.json.load_truth_snapshot_payloads("test-book", snapshot_id)["characters"]["characters"]
        linqi = [item for item in characters if item["character_id"] == "char-linqi"]
        self.assertEqual(len(linqi), 1)
        self.assertEqual(linqi[0]["current_location"], "旧仓库")
        self.assertIn("fact-2", linqi[0]["known_fact_ids"])
        self.assertIn("injured", linqi[0]["status_tags"])

    def _prepare_settled_chapter(self, chapter_no: int):
        self.service.init_chapter("test-book", chapter_no)
        self.service.plan_chapter("test-book", chapter_no, guidance=f"chapter {chapter_no} should stay coherent")
        self.service.compose_chapter("test-book", chapter_no)
        draft_id = self.service.write_chapter_draft("test-book", chapter_no, mode="initial")
        return self.service.settle_chapter("test-book", chapter_no, draft_id)

    def _approve_single_pass_chapter(self, chapter_no: int) -> None:
        settled = self._prepare_settled_chapter(chapter_no)
        audit = self.service.audit_chapter("test-book", chapter_no, settled.current_artifact_refs["settlement"])
        self.assertTrue(audit["gate_decision"]["passed"])
        approved = self.service.approve_chapter("test-book", chapter_no)
        self.assertEqual(approved.stage.value, "approved")

    def _install_truth_extractor(self, payloads: list[dict]) -> None:
        self.service.truth_extractor = TruthExtractorAdapter(FakeLLMProvider(json_responses=payloads))

    @staticmethod
    def _truth_payload(
        *,
        fact_assertions: list[dict] | None = None,
        proposed_fact_updates: list[dict] | None = None,
        character_updates: list[dict] | None = None,
        relationship_updates: list[dict] | None = None,
        hook_updates: list[dict] | None = None,
        chapter_irreversible_facts: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> dict:
        return {
            "fact_assertions": fact_assertions or [],
            "proposed_fact_updates": proposed_fact_updates or [],
            "character_updates": character_updates or [],
            "relationship_updates": relationship_updates or [],
            "hook_updates": hook_updates or [],
            "chapter_irreversible_facts": chapter_irreversible_facts or [],
            "notes": notes or [],
        }


if __name__ == "__main__":
    unittest.main()
