from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from engine.schemas.run import RunAction
from engine.services import StoryEngineService


class StorageRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.service = StoryEngineService(self.root)
        self.service.create_book({"book_id": "demo-book", "title": "Demo Book"})
        self.service.init_chapter("demo-book", 1)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_json_round_trip_book_and_status(self) -> None:
        book = self.service.get_book("demo-book")["book"]
        status = self.service.get_chapter_status("demo-book", 1)["status"]
        self.assertEqual(book.book_id, "demo-book")
        self.assertEqual(status.chapter_no, 1)
        self.assertEqual(status.stage.value, "planned")
        truth_root = self.root / "books" / "demo-book" / "state" / "truth"
        self.assertTrue((truth_root / "truth_index.json").exists())
        self.assertTrue((truth_root / "canon.json").exists())
        self.assertTrue((truth_root / "characters.json").exists())
        self.assertTrue((truth_root / "hook_ledger.json").exists())
        self.assertTrue((truth_root / "chapter_facts.json").exists())

    def test_sqlite_read_model_updates_and_rebuilds(self) -> None:
        compose_run = self.service.start_run("demo-book", 1, RunAction.COMPOSE.value, "planner", [])
        self.service.transition_chapter("demo-book", 1, "composed", compose_run.run_id, {})
        db_path = self.root / "books" / "demo-book" / "memory.db"
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                "SELECT stage FROM chapter_status WHERE book_id = ? AND chapter_no = ?",
                ("demo-book", 1),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "composed")

        os.remove(db_path)
        self.service.rebuild_read_model("demo-book")
        with closing(sqlite3.connect(db_path)) as conn:
            rebuilt = conn.execute(
                "SELECT stage FROM chapter_status WHERE book_id = ? AND chapter_no = ?",
                ("demo-book", 1),
            ).fetchone()
        self.assertEqual(rebuilt[0], "composed")

    def test_artifacts_are_append_only(self) -> None:
        draft_run = self.service.start_run("demo-book", 1, RunAction.WRITE.value, "writer", [])
        artifact_a = self.service.register_artifact("demo-book", 1, "draft", {"text": "A"}, draft_run.run_id)
        artifact_b = self.service.register_artifact("demo-book", 1, "draft", {"text": "B"}, draft_run.run_id)
        self.assertNotEqual(artifact_a, artifact_b)
        manifest = self.service.repo.json.list_artifacts("demo-book")
        self.assertEqual(len(manifest), 2)

    def test_gate_audit_compare_artifacts_round_trip(self) -> None:
        self.service.plan_chapter("demo-book", 1, guidance="tight pacing")
        self.service.compose_chapter("demo-book", 1)
        draft_id = self.service.write_chapter_draft("demo-book", 1, mode="initial")
        settled = self.service.settle_chapter("demo-book", 1, draft_id)
        audit = self.service.audit_chapter("demo-book", 1, settled.current_artifact_refs["settlement"])
        artifacts = self.service.repo.json.list_artifacts("demo-book")
        artifact_types = {artifact.artifact_type.value for artifact in artifacts}
        self.assertIn("plan", artifact_types)
        self.assertIn("compose_context", artifact_types)
        self.assertIn("settlement", artifact_types)
        self.assertIn("mechanical_gate", artifact_types)
        self.assertIn("audit", artifact_types)
        self.assertIn("gate_decision", artifact_types)
        self.assertIn("chapter_quality", artifact_types)
        self.assertIn(audit["gate_decision_artifact_id"], {artifact.artifact_id for artifact in artifacts})

    def test_read_model_projects_latest_chapter_quality(self) -> None:
        self.service.plan_chapter("demo-book", 1, guidance="tight pacing")
        self.service.compose_chapter("demo-book", 1)
        draft_id = self.service.write_chapter_draft("demo-book", 1, mode="initial")
        settled = self.service.settle_chapter("demo-book", 1, draft_id)
        audit = self.service.audit_chapter("demo-book", 1, settled.current_artifact_refs["settlement"])
        db_path = self.root / "books" / "demo-book" / "memory.db"
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                """
                SELECT gate_decision_artifact_id, blocked_by_mechanical, critical_count
                FROM chapter_quality
                WHERE book_id = ? AND chapter_no = ?
                """,
                ("demo-book", 1),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], audit["gate_decision_artifact_id"])
        os.remove(db_path)
        self.service.rebuild_read_model("demo-book")
        with closing(sqlite3.connect(db_path)) as conn:
            rebuilt = conn.execute(
                """
                SELECT gate_decision_artifact_id, blocked_by_mechanical, critical_count
                FROM chapter_quality
                WHERE book_id = ? AND chapter_no = ?
                """,
                ("demo-book", 1),
            ).fetchone()
        self.assertEqual(rebuilt[0], audit["gate_decision_artifact_id"])

    def test_truth_head_projects_and_rebuilds(self) -> None:
        self.service.plan_chapter("demo-book", 1, guidance="tight pacing")
        self.service.compose_chapter("demo-book", 1)
        first_draft = self.service.write_chapter_draft("demo-book", 1, mode="initial")
        settled = self.service.settle_chapter("demo-book", 1, first_draft)
        first_gate = self.service.audit_chapter("demo-book", 1, settled.current_artifact_refs["settlement"])
        revision = self.service.revise_chapter("demo-book", 1, mode="surgical")
        revised_settled = self.service.settle_chapter("demo-book", 1, revision["draft_artifact_id"])
        revised_gate = self.service.audit_chapter("demo-book", 1, revised_settled.current_artifact_refs["settlement"])
        self.service.compare_candidate(
            "demo-book",
            1,
            first_gate["gate_decision_artifact_id"],
            revised_gate["gate_decision_artifact_id"],
        )
        self.service.approve_chapter("demo-book", 1)
        db_path = self.root / "books" / "demo-book" / "memory.db"
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                """
                SELECT current_snapshot_id, committed_through_chapter
                FROM truth_head
                WHERE book_id = ?
                """,
                ("demo-book",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], 1)
        os.remove(db_path)
        self.service.rebuild_read_model("demo-book")
        with closing(sqlite3.connect(db_path)) as conn:
            rebuilt = conn.execute(
                """
                SELECT current_snapshot_id, committed_through_chapter
                FROM truth_head
                WHERE book_id = ?
                """,
                ("demo-book",),
            ).fetchone()
        self.assertEqual(rebuilt[1], 1)

    def test_truth_snapshot_uses_private_ledger_copies(self) -> None:
        self.service.plan_chapter("demo-book", 1, guidance="tight pacing")
        self.service.compose_chapter("demo-book", 1)
        first_draft = self.service.write_chapter_draft("demo-book", 1, mode="initial")
        settled = self.service.settle_chapter("demo-book", 1, first_draft)
        first_gate = self.service.audit_chapter("demo-book", 1, settled.current_artifact_refs["settlement"])
        revision = self.service.revise_chapter("demo-book", 1, mode="surgical")
        revised_settled = self.service.settle_chapter("demo-book", 1, revision["draft_artifact_id"])
        revised_gate = self.service.audit_chapter("demo-book", 1, revised_settled.current_artifact_refs["settlement"])
        self.service.compare_candidate(
            "demo-book",
            1,
            first_gate["gate_decision_artifact_id"],
            revised_gate["gate_decision_artifact_id"],
        )
        self.service.approve_chapter("demo-book", 1)

        truth_head = self.service.get_truth_head("demo-book")
        snapshot = truth_head["truth_snapshot"]
        self.assertIn("/snapshots/", snapshot["canon_ref"])
        self.assertIn("/snapshots/", snapshot["character_ref"])
        self.assertIn("/snapshots/", snapshot["hook_ref"])
        self.assertIn("/snapshots/", snapshot["chapter_fact_ref"])
        book_root = self.root / "books" / "demo-book"
        for key in ("canon_ref", "character_ref", "hook_ref", "chapter_fact_ref"):
            self.assertTrue((book_root / snapshot[key]).exists(), key)


if __name__ == "__main__":
    unittest.main()
