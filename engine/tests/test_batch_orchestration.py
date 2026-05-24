from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from engine.schemas.artifact import AuditRecord
from engine.services import BatchOrchestratorService, StoryEngineService


class PassingAuditor:
    def review(self, bundle) -> AuditRecord:
        return AuditRecord(
            passed=True,
            critical_count=0,
            issues=[],
            recommended_mode="accept",
            score_summary={"overall": 6.8, "logic": 6.6, "character": 6.8, "hook": 6.9, "pace": 6.7},
        )


class EscalatingReaderPanel:
    def evaluate(self, *, batch_run, checkpoint, slice_payload):
        return {
            "panel_scope": "checkpoint",
            "editor_findings": ["momentum loss around the frontier"],
            "genre_reader_findings": ["momentum loss around the frontier"],
            "writer_findings": [],
            "first_reader_findings": [],
            "momentum_loss": True,
            "earned_ending": True,
            "cut_candidate": [],
            "missing_scene": [],
            "thinnest_character": None,
            "aggregate_recommendation": "pause_for_review",
            "risk_flags": ["momentum_loss"],
        }


class BatchOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.book_id = "batch-book"
        self.engine = StoryEngineService(self.root)
        self.engine.create_book({"book_id": self.book_id, "title": "Batch Book"})
        for chapter_no in range(1, 6):
            self.engine.init_chapter(self.book_id, chapter_no)
        self.batch = BatchOrchestratorService(self.root, engine=self.engine)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _approved_chapter(self, chapter_no: int) -> None:
        self.engine.plan_chapter(self.book_id, chapter_no, guidance=f"chapter {chapter_no} should advance")
        self.engine.compose_chapter(self.book_id, chapter_no)
        draft_id = self.engine.write_chapter_draft(self.book_id, chapter_no, mode="initial")
        settled = self.engine.settle_chapter(self.book_id, chapter_no, draft_id)
        original = self.engine.gate_runner.auditor
        self.engine.gate_runner.auditor = PassingAuditor()
        try:
            audit = self.engine.audit_chapter(
                self.book_id,
                chapter_no,
                settled.current_artifact_refs["settlement"],
            )
        finally:
            self.engine.gate_runner.auditor = original
        self.assertTrue(audit["gate_decision"]["passed"])
        approved = self.engine.approve_chapter(self.book_id, chapter_no)
        self.assertEqual(approved.stage.value, "approved")

    def test_prepare_batch_runs_plan_compose_draft_settle_and_creates_checkpoint(self) -> None:
        batch_run = self.batch.create_batch_run(
            self.book_id,
            chapter_range=[1, 2],
            batch_mode="prepare_batch",
        )

        result = self.batch.start_batch_run(batch_run.batch_run_id)

        self.assertEqual(result.status.value, "completed")
        for chapter_no in (1, 2):
            status = self.engine.get_chapter_status(self.book_id, chapter_no)["status"]
            self.assertEqual(status.stage.value, "settled")
            self.assertIn("settlement", status.current_artifact_refs)
        self.assertTrue(result.last_checkpoint_id)
        checkpoints = self.batch.get_batch_run(batch_run.batch_run_id)["checkpoints"]
        self.assertTrue(checkpoints)

    def test_prepare_batch_pauses_when_forward_window_is_exceeded(self) -> None:
        batch_run = self.batch.create_batch_run(
            self.book_id,
            chapter_range=[1, 3],
            batch_mode="prepare_batch",
        )

        result = self.batch.start_batch_run(batch_run.batch_run_id)

        self.assertEqual(result.status.value, "paused")
        self.assertIn("forward_window_limit", result.pause_reason_codes)
        self.assertEqual(self.engine.get_chapter_status(self.book_id, 1)["status"].stage.value, "settled")
        self.assertEqual(self.engine.get_chapter_status(self.book_id, 2)["status"].stage.value, "settled")
        self.assertEqual(self.engine.get_chapter_status(self.book_id, 3)["status"].stage.value, "planned")

    def test_audit_batch_pauses_on_failed_gate_and_leaves_downstream_queued(self) -> None:
        prepare = self.batch.create_batch_run(
            self.book_id,
            chapter_range=[1, 2],
            batch_mode="prepare_batch",
        )
        self.batch.start_batch_run(prepare.batch_run_id)
        audit_run = self.batch.create_batch_run(
            self.book_id,
            chapter_range=[1, 2],
            batch_mode="audit_batch",
        )

        result = self.batch.start_batch_run(audit_run.batch_run_id)

        self.assertEqual(result.status.value, "paused")
        self.assertIn("chapter_failed", result.pause_reason_codes)
        snapshot = self.batch.get_batch_run(audit_run.batch_run_id)
        items = snapshot["items"]
        first_audit = next(item for item in items if item.chapter_no == 1)
        second_audit = next(item for item in items if item.chapter_no == 2)
        self.assertEqual(first_audit.status.value, "failed")
        self.assertEqual(second_audit.status.value, "queued")
        self.assertEqual(self.engine.get_chapter_status(self.book_id, 1)["status"].stage.value, "audited_failed")

    def test_invalidated_chapter_is_auto_reset_to_planned_in_audit_batch(self) -> None:
        self.engine.plan_chapter(self.book_id, 2, guidance="stale chapter setup")
        self.engine.compose_chapter(self.book_id, 2)
        draft_id = self.engine.write_chapter_draft(self.book_id, 2, mode="initial")
        self.engine.settle_chapter(self.book_id, 2, draft_id)
        self.engine.plan_chapter(self.book_id, 3, guidance="stale chapter setup")
        self.engine.compose_chapter(self.book_id, 3)
        draft_id_3 = self.engine.write_chapter_draft(self.book_id, 3, mode="initial")
        self.engine.settle_chapter(self.book_id, 3, draft_id_3)
        self._approved_chapter(1)
        receipt_id = self.engine.get_chapter_status(self.book_id, 1)["status"].current_artifact_refs["truth_commit_receipt"]
        self.engine.invalidate_downstream(self.book_id, receipt_id)

        audit_run = self.batch.create_batch_run(
            self.book_id,
            chapter_range=[2, 3],
            batch_mode="audit_batch",
        )
        paused = self.batch.start_batch_run(audit_run.batch_run_id)
        self.assertEqual(paused.status.value, "paused")
        self.assertIn("chapter_failed", paused.pause_reason_codes)
        self.assertEqual(self.engine.get_chapter_status(self.book_id, 2)["status"].stage.value, "planned")

    def test_invalidated_chapter_does_not_break_prepare_batch_reentry(self) -> None:
        prepare = self.batch.create_batch_run(
            self.book_id,
            chapter_range=[1, 2],
            batch_mode="prepare_batch",
        )
        self.batch.start_batch_run(prepare.batch_run_id)
        self._approved_chapter(3)
        receipt_id = self.engine.get_chapter_status(self.book_id, 3)["status"].current_artifact_refs["truth_commit_receipt"]
        self.engine.invalidate_downstream(self.book_id, receipt_id)

        rerun = self.batch.start_batch_run(prepare.batch_run_id)

        self.assertEqual(rerun.status.value, "completed")
        status = self.engine.get_chapter_status(self.book_id, 2)["status"]
        self.assertEqual(status.stage.value, "settled")

    def test_auto_reset_resolves_open_propagation_debt(self) -> None:
        self.engine.plan_chapter(self.book_id, 2, guidance="stale chapter setup")
        self.engine.compose_chapter(self.book_id, 2)
        draft_id = self.engine.write_chapter_draft(self.book_id, 2, mode="initial")
        self.engine.settle_chapter(self.book_id, 2, draft_id)
        self._approved_chapter(1)
        receipt_id = self.engine.get_chapter_status(self.book_id, 1)["status"].current_artifact_refs["truth_commit_receipt"]
        self.engine.invalidate_downstream(self.book_id, receipt_id)

        audit_run = self.batch.create_batch_run(
            self.book_id,
            chapter_range=[2],
            batch_mode="audit_batch",
        )
        self.batch.start_batch_run(audit_run.batch_run_id)

        freshness = self.engine.get_chapter_truth_freshness(self.book_id, 2)
        self.assertEqual(self.engine.get_chapter_status(self.book_id, 2)["status"].stage.value, "planned")
        self.assertTrue(freshness["is_fresh"])

    def test_checkpoint_review_creates_reader_panel_and_can_pause_batch(self) -> None:
        batch = BatchOrchestratorService(
            self.root,
            engine=self.engine,
            reader_panel=EscalatingReaderPanel(),
        )
        prepare = batch.create_batch_run(
            self.book_id,
            chapter_range=[1, 2],
            batch_mode="prepare_batch",
        )
        batch.start_batch_run(prepare.batch_run_id)

        review = batch.run_checkpoint_review(prepare.batch_run_id)

        self.assertEqual(review["batch_run"].status.value, "paused")
        self.assertIn("reader_panel_escalation", review["batch_run"].pause_reason_codes)
        self.assertTrue(review["reader_panel_artifact_id"])

    def test_noop_style_adapter_does_not_block_checkpoint_review(self) -> None:
        prepare = self.batch.create_batch_run(
            self.book_id,
            chapter_range=[1, 2],
            batch_mode="prepare_batch",
        )
        self.batch.start_batch_run(prepare.batch_run_id)

        review = self.batch.run_checkpoint_review(prepare.batch_run_id)

        self.assertEqual(review["batch_run"].status.value, "completed")
        self.assertIsNone(review["style_signal_artifact_id"])

    def test_rebuild_read_model_projects_batch_runs_items_and_checkpoints(self) -> None:
        batch_run = self.batch.create_batch_run(
            self.book_id,
            chapter_range=[1, 2],
            batch_mode="prepare_batch",
        )
        self.batch.start_batch_run(batch_run.batch_run_id)
        db_path = self.root / "books" / self.book_id / "memory.db"
        with closing(sqlite3.connect(db_path)) as conn:
            batch_row = conn.execute(
                "SELECT status FROM batch_runs WHERE batch_run_id = ?",
                (batch_run.batch_run_id,),
            ).fetchone()
            item_count = conn.execute(
                "SELECT COUNT(*) FROM batch_items WHERE batch_run_id = ?",
                (batch_run.batch_run_id,),
            ).fetchone()[0]
            checkpoint_count = conn.execute(
                "SELECT COUNT(*) FROM batch_checkpoints WHERE batch_run_id = ?",
                (batch_run.batch_run_id,),
            ).fetchone()[0]
        self.assertEqual(batch_row[0], "completed")
        self.assertGreater(item_count, 0)
        self.assertGreater(checkpoint_count, 0)

        os.remove(db_path)
        self.engine.rebuild_read_model(self.book_id)

        with closing(sqlite3.connect(db_path)) as conn:
            rebuilt_batch = conn.execute(
                "SELECT status FROM batch_runs WHERE batch_run_id = ?",
                (batch_run.batch_run_id,),
            ).fetchone()
            rebuilt_items = conn.execute(
                "SELECT COUNT(*) FROM batch_items WHERE batch_run_id = ?",
                (batch_run.batch_run_id,),
            ).fetchone()[0]
            rebuilt_checkpoints = conn.execute(
                "SELECT COUNT(*) FROM batch_checkpoints WHERE batch_run_id = ?",
                (batch_run.batch_run_id,),
            ).fetchone()[0]
        self.assertEqual(rebuilt_batch[0], "completed")
        self.assertEqual(rebuilt_items, item_count)
        self.assertEqual(rebuilt_checkpoints, checkpoint_count)


if __name__ == "__main__":
    unittest.main()
