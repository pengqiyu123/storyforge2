from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from engine.schemas.artifact import AuditRecord
from engine.services import StoryEngineService


class PassingAuditor:
    def review(self, bundle) -> AuditRecord:
        return AuditRecord(
            passed=True,
            critical_count=0,
            issues=[],
            recommended_mode="accept",
            score_summary={"overall": 6.8, "logic": 6.6, "character": 6.8, "hook": 6.9, "pace": 6.7},
        )


class PropagationDebtTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.book_id = "propagation-book"
        self.service = StoryEngineService(self.root)
        self.service.create_book({"book_id": self.book_id, "title": "Propagation Debt Book"})
        for chapter_no in range(1, 9):
            self.service.init_chapter(self.book_id, chapter_no)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _plan_and_compose(self, chapter_no: int) -> str:
        self.service.plan_chapter(self.book_id, chapter_no, guidance=f"chapter {chapter_no} should stay coherent")
        return self.service.compose_chapter(self.book_id, chapter_no)

    def _write_initial_draft(self, chapter_no: int) -> str:
        self._plan_and_compose(chapter_no)
        return self.service.write_chapter_draft(self.book_id, chapter_no, mode="initial")

    def _settle_initial_draft(self, chapter_no: int) -> tuple[str, object]:
        draft_id = self._write_initial_draft(chapter_no)
        status = self.service.settle_chapter(self.book_id, chapter_no, draft_id)
        return draft_id, status

    def _audit_failed_chapter(self, chapter_no: int) -> dict:
        _, settled = self._settle_initial_draft(chapter_no)
        decision = self.service.audit_chapter(self.book_id, chapter_no, settled.current_artifact_refs["settlement"])
        self.assertFalse(decision["gate_decision"]["passed"])
        self.assertEqual(decision["status"].stage.value, "audited_failed")
        return decision

    def _prepare_pending_comparison(self, chapter_no: int) -> dict[str, str]:
        first_gate = self._audit_failed_chapter(chapter_no)
        revision = self.service.revise_chapter(self.book_id, chapter_no, mode="surgical")
        revised_settled = self.service.settle_chapter(self.book_id, chapter_no, revision["draft_artifact_id"])
        revised_gate = self.service.audit_chapter(
            self.book_id,
            chapter_no,
            revised_settled.current_artifact_refs["settlement"],
        )
        notes = self.service.repo.json.load_chapter_note(self.book_id, chapter_no)
        self.assertTrue(notes.get("pending_comparison"))
        return {
            "baseline_gate_id": first_gate["gate_decision_artifact_id"],
            "candidate_gate_id": revised_gate["gate_decision_artifact_id"],
        }

    def _prepare_audited_passed(self, chapter_no: int) -> None:
        comparison_inputs = self._prepare_pending_comparison(chapter_no)
        comparison = self.service.compare_candidate(
            self.book_id,
            chapter_no,
            comparison_inputs["baseline_gate_id"],
            comparison_inputs["candidate_gate_id"],
        )
        self.assertEqual(comparison["status"].stage.value, "audited_passed")

    def _approve_upstream_truth_change(self, chapter_no: int) -> str:
        draft_id, settled = self._settle_initial_draft(chapter_no)
        original_auditor = self.service.gate_runner.auditor
        self.service.gate_runner.auditor = PassingAuditor()
        try:
            audit = self.service.audit_chapter(self.book_id, chapter_no, settled.current_artifact_refs["settlement"])
        finally:
            self.service.gate_runner.auditor = original_auditor
        self.assertTrue(audit["gate_decision"]["passed"])
        approved = self.service.approve_chapter(self.book_id, chapter_no)
        self.assertEqual(approved.stage.value, "approved")
        self.assertEqual(approved.current_artifact_refs["draft"], draft_id)
        return approved.current_artifact_refs["truth_commit_receipt"]

    def _invalidate_downstream(self, from_artifact: str) -> dict:
        invalidate_downstream = getattr(self.service, "invalidate_downstream", None)
        self.assertIsNotNone(
            invalidate_downstream,
            "StoryEngineService must expose invalidate_downstream(book_id, from_artifact=...)",
        )
        result = invalidate_downstream(self.book_id, from_artifact=from_artifact)
        self.assertIsInstance(result, dict)
        self.assertIn("invalidated_chapter_nos", result)
        self.assertIn("propagation_debt_ids", result)
        self.assertIsInstance(result["invalidated_chapter_nos"], list)
        self.assertIsInstance(result["propagation_debt_ids"], list)
        return result

    def _build_stale_downstream_scenario(self) -> dict[str, object]:
        stale_snapshot_id = self.service.get_truth_head(self.book_id)["truth_index"]["current_snapshot_id"]

        self._plan_and_compose(2)

        chapter_3_draft = self._write_initial_draft(3)

        chapter_4_draft, chapter_4_settled = self._settle_initial_draft(4)

        chapter_5_failed = self._audit_failed_chapter(5)

        chapter_6_compare = self._prepare_pending_comparison(6)

        self._prepare_audited_passed(7)

        receipt_id = self._approve_upstream_truth_change(1)
        current_snapshot_id = self.service.get_truth_head(self.book_id)["truth_index"]["current_snapshot_id"]
        self.assertNotEqual(stale_snapshot_id, current_snapshot_id)

        result = self._invalidate_downstream(receipt_id)

        return {
            "stale_snapshot_id": stale_snapshot_id,
            "current_snapshot_id": current_snapshot_id,
            "receipt_id": receipt_id,
            "result": result,
            "chapter_3_draft": chapter_3_draft,
            "chapter_4_draft": chapter_4_draft,
            "chapter_4_settlement": chapter_4_settled.current_artifact_refs["settlement"],
            "chapter_5_gate_id": chapter_5_failed["gate_decision_artifact_id"],
            "chapter_6_compare": chapter_6_compare,
        }

    def _open_debts_by_chapter(self) -> dict[int, list[dict]]:
        grouped: dict[int, list[dict]] = {}
        for entry in self.service.repo.json.load_propagation_debts(self.book_id):
            if entry.get("status") != "open":
                continue
            grouped.setdefault(int(entry["chapter_no"]), []).append(entry)
        return grouped

    def _fetch_propagation_debt_projection(self) -> list[tuple]:
        db_path = self.root / "books" / self.book_id / "memory.db"
        with closing(sqlite3.connect(db_path)) as conn:
            return conn.execute(
                """
                SELECT
                    debt_id,
                    chapter_no,
                    trigger_chapter_no,
                    stale_snapshot_id,
                    current_snapshot_id,
                    source_truth_commit_receipt_id,
                    dependency_scope,
                    reason_code,
                    blocking,
                    status,
                    dependency_hits_json,
                    created_at,
                    resolved_at
                FROM propagation_debts
                WHERE book_id = ?
                ORDER BY chapter_no, debt_id
                """,
                (self.book_id,),
            ).fetchall()

    def test_truth_head_change_invalidates_stale_downstream_chapters_and_records_debt(self) -> None:
        scenario = self._build_stale_downstream_scenario()
        invalidated = set(scenario["result"]["invalidated_chapter_nos"])
        expected = {2, 3, 4, 5, 6, 7}

        self.assertEqual(invalidated, expected)
        self.assertNotIn(8, invalidated)
        self.assertGreaterEqual(len(scenario["result"]["propagation_debt_ids"]), len(expected))

        debts_by_chapter = self._open_debts_by_chapter()
        self.assertFalse(debts_by_chapter.get(8))
        for chapter_no in expected:
            self.assertIn(chapter_no, debts_by_chapter)
            status = self.service.get_chapter_status(self.book_id, chapter_no)["status"]
            self.assertEqual(status.stage.value, "invalidated")
            self.assertTrue(status.invalidated_by)
            for debt in debts_by_chapter[chapter_no]:
                self.assertEqual(debt["trigger_chapter_no"], 1)
                self.assertEqual(debt["stale_snapshot_id"], scenario["stale_snapshot_id"])
                self.assertEqual(debt["current_snapshot_id"], scenario["current_snapshot_id"])
                self.assertEqual(debt["source_truth_commit_receipt_id"], scenario["receipt_id"])
                self.assertTrue(debt["debt_id"])
                self.assertTrue(debt["reason_code"])
                self.assertTrue(debt["blocking"])
                self.assertIn(debt["dependency_scope"], {"snapshot_only", "targeted"})

    def test_invalidated_chapters_reject_write_settle_audit_revise_compare_and_approve(self) -> None:
        scenario = self._build_stale_downstream_scenario()

        with self.assertRaises(ValueError):
            self.service.write_chapter_draft(self.book_id, 2, mode="initial")

        with self.assertRaises(ValueError):
            self.service.settle_chapter(self.book_id, 3, scenario["chapter_3_draft"])

        with self.assertRaises(ValueError):
            self.service.audit_chapter(self.book_id, 4, scenario["chapter_4_settlement"])

        with self.assertRaises(ValueError):
            self.service.revise_chapter(self.book_id, 5, mode="surgical")

        with self.assertRaises(ValueError):
            self.service.compare_candidate(
                self.book_id,
                6,
                scenario["chapter_6_compare"]["baseline_gate_id"],
                scenario["chapter_6_compare"]["candidate_gate_id"],
            )

        with self.assertRaises(ValueError):
            self.service.approve_chapter(self.book_id, 7)

    def test_planned_untouched_chapters_are_not_invalidated(self) -> None:
        scenario = self._build_stale_downstream_scenario()
        chapter_8 = self.service.get_chapter_status(self.book_id, 8)["status"]

        self.assertEqual(chapter_8.stage.value, "planned")
        self.assertIsNone(chapter_8.invalidated_by)
        self.assertNotIn(8, scenario["result"]["invalidated_chapter_nos"])
        self.assertFalse(any(entry["chapter_no"] == 8 for entry in self.service.repo.json.load_propagation_debts(self.book_id)))

    def test_rebuild_read_model_preserves_propagation_debt_projection(self) -> None:
        self._build_stale_downstream_scenario()
        before = self._fetch_propagation_debt_projection()

        self.assertTrue(before)
        self.assertEqual({row[1] for row in before}, {2, 3, 4, 5, 6, 7})

        db_path = self.root / "books" / self.book_id / "memory.db"
        os.remove(db_path)
        self.service.rebuild_read_model(self.book_id)

        after = self._fetch_propagation_debt_projection()
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
