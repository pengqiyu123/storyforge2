from __future__ import annotations

import unittest

from engine.schemas.chapter import ChapterStage, ChapterStatusRecord
from engine.state_machine import (
    ChapterStateTransitionError,
    MissingRunContextError,
    assert_transition_allowed,
    next_status,
)


class ChapterLifecycleTests(unittest.TestCase):
    def test_rejects_invalid_transition(self) -> None:
        with self.assertRaises(ChapterStateTransitionError):
            assert_transition_allowed(ChapterStage.DRAFTED, ChapterStage.APPROVED)

    def test_requires_run_id(self) -> None:
        record = ChapterStatusRecord(book_id="book-a", chapter_no=1, stage=ChapterStage.DRAFTED)
        with self.assertRaises(MissingRunContextError):
            next_status(record, ChapterStage.SETTLED, run_id="", artifact_refs={"draft": "a"})

    def test_settled_requires_draft_artifact(self) -> None:
        record = ChapterStatusRecord(book_id="book-a", chapter_no=2, stage=ChapterStage.DRAFTED)
        with self.assertRaises(MissingRunContextError):
            next_status(record, ChapterStage.SETTLED, run_id="run-1", artifact_refs={})

    def test_revision_increments_round(self) -> None:
        record = ChapterStatusRecord(
            book_id="book-a",
            chapter_no=4,
            stage=ChapterStage.AUDITED_FAILED,
            revision_round=0,
        )
        revised = next_status(record, ChapterStage.REVISING, run_id="run-2", artifact_refs={})
        self.assertEqual(revised.revision_round, 1)
        self.assertEqual(revised.stage, ChapterStage.REVISING)

    def test_blocked_can_transition_to_invalidated(self) -> None:
        record = ChapterStatusRecord(
            book_id="book-a",
            chapter_no=5,
            stage=ChapterStage.BLOCKED,
        )
        invalidated = next_status(
            record,
            ChapterStage.INVALIDATED,
            run_id="run-3",
            artifact_refs={},
            invalidated_by="truth-commit-1",
        )
        self.assertEqual(invalidated.stage, ChapterStage.INVALIDATED)
        self.assertEqual(invalidated.invalidated_by, "truth-commit-1")

    def test_unknown_stage_raises_explicit_error(self) -> None:
        with self.assertRaisesRegex(ChapterStateTransitionError, "unknown chapter stage"):
            assert_transition_allowed("mystery-stage", ChapterStage.PLANNED)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
