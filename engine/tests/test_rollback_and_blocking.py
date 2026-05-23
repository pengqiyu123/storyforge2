from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.schemas.run import RunAction
from engine.services import StoryEngineService
from engine.state_machine import ChapterStateTransitionError


class RollbackAndBlockingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.service = StoryEngineService(self.root)
        self.service.create_book({"book_id": "rollback-book", "title": "Rollback Book"})
        self.service.init_chapter("rollback-book", 1)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_blocked_chapter_rejects_approve(self) -> None:
        blocked = self.service.mark_blocked("rollback-book", 1, "manual hold")
        self.assertEqual(blocked.stage.value, "blocked")
        run = self.service.start_run("rollback-book", 1, RunAction.APPROVE.value, "system", [])
        with self.assertRaises(ChapterStateTransitionError):
            self.service.transition_chapter("rollback-book", 1, "approved", run.run_id, {})

    def test_invalidated_chapter_records_source(self) -> None:
        invalidated = self.service.mark_invalidated("rollback-book", 1, "story_bible#1", "canon changed")
        self.assertEqual(invalidated.stage.value, "invalidated")
        self.assertEqual(invalidated.invalidated_by, "story_bible#1")

    def test_rollback_requires_discarded_revision(self) -> None:
        compose_run = self.service.start_run("rollback-book", 1, RunAction.COMPOSE.value, "planner", [])
        self.service.transition_chapter("rollback-book", 1, "composed", compose_run.run_id, {})
        draft_run = self.service.start_run("rollback-book", 1, RunAction.WRITE.value, "writer", [])
        self.service.transition_chapter("rollback-book", 1, "drafted", draft_run.run_id, {})
        draft_artifact = self.service.register_artifact(
            "rollback-book", 1, "draft", {"text": "old"}, draft_run.run_id
        )
        settle_run = self.service.start_run("rollback-book", 1, RunAction.SETTLE.value, "system", [])
        self.service.transition_chapter(
            "rollback-book", 1, "settled", settle_run.run_id, {"draft": draft_artifact}
        )
        audit_run = self.service.start_run("rollback-book", 1, RunAction.AUDIT.value, "auditor", [])
        audit_artifact = self.service.register_artifact(
            "rollback-book",
            1,
            "audit",
            {"passed": False, "critical_count": 1, "issues": [], "recommended_mode": "rework", "score_summary": {}},
            audit_run.run_id,
        )
        self.service.transition_chapter(
            "rollback-book", 1, "audited_failed", audit_run.run_id, {"audit": audit_artifact}
        )
        revise_run = self.service.start_run("rollback-book", 1, RunAction.REVISE.value, "reviser", [])
        revising = self.service.transition_chapter("rollback-book", 1, "revising", revise_run.run_id, {})
        self.assertEqual(revising.revision_round, 1)
        rolled_back = self.service.rollback_chapter(
            "rollback-book",
            1,
            {
                "base_artifact_id": draft_artifact,
                "candidate_artifact_id": "candidate-123",
                "evaluation_result": "worse",
                "kept": False,
                "rollback_reason": "regressed",
            },
        )
        self.assertEqual(rolled_back.stage.value, "rolled_back")


if __name__ == "__main__":
    unittest.main()
