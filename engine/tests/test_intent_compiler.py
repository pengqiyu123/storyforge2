from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.schemas.artifact import AuditRecord
from engine.schemas.intent import IntentAction, ParsedIntent
from engine.services import StoryEngineService
from engine.services.intent_compiler import IntentCompilerService
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


def passing_truth_payload() -> dict:
    return {
        "fact_assertions": [
            {
                "fact_id": "fact-ledger-1",
                "category": "chapter_outcome",
                "statement": "林七拿到了旧账册",
                "hard": False,
                "assertion_basis": "explicit",
            }
        ],
        "proposed_fact_updates": [],
        "character_updates": [
            {
                "character_id": "char-linqi",
                "display_name": "林七",
                "status_tags": ["active"],
                "current_location": "旧仓库",
                "known_fact_ids": ["fact-ledger-1"],
            }
        ],
        "relationship_updates": [],
        "hook_updates": [
            {
                "hook_id": "hook-old-ledger",
                "label": "旧账册去向",
                "kind": "hook",
                "status": "advanced",
                "introduced_in": 1,
                "owner_entity_ids": ["char-linqi"],
                "source_fact_ids": ["fact-ledger-1"],
            }
        ],
        "chapter_irreversible_facts": [],
        "notes": [],
    }


class IntentCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.book_id = "intent-book"
        self.engine = StoryEngineService(self.root)
        self.engine.create_book({"book_id": self.book_id, "title": "Intent Book"})
        for chapter_no in range(1, 4):
            self.engine.init_chapter(self.book_id, chapter_no)
        self.engine.truth_extractor = TruthExtractorAdapter(
            FakeLLMProvider(json_responses=[passing_truth_payload() for _ in range(12)])
        )
        self.compiler = IntentCompilerService(engine=self.engine, batch_orchestrator=None)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_continue_chapter_parse(self) -> None:
        result = self.compiler.parse("继续第六章", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.CONTINUE_CHAPTER)
        self.assertEqual(result.chapter_no, 6)

    def test_re_audit_chapter_parse(self) -> None:
        result = self.compiler.parse("重审第三章逻辑", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.RE_AUDIT_CHAPTER)
        self.assertEqual(result.chapter_no, 3)

    def test_rollback_chapter_parse(self) -> None:
        result = self.compiler.parse("把第八章回滚到上一个通过版", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.ROLLBACK_CHAPTER)
        self.assertEqual(result.chapter_no, 8)

    def test_export_tomato_parse(self) -> None:
        result = self.compiler.parse("批准后导出番茄", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.EXPORT_CHAPTER)
        self.assertEqual(result.parameters["platform"], "tomato")

    def test_export_qidian_parse(self) -> None:
        result = self.compiler.parse("导出起点", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.EXPORT_CHAPTER)
        self.assertEqual(result.parameters["platform"], "qidian")

    def test_resume_batch_parse(self) -> None:
        result = self.compiler.parse("恢复批量run-abc", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.RESUME_BATCH)
        self.assertIn("batch_run_id", result.parameters)

    def test_query_truth_parse(self) -> None:
        result = self.compiler.parse("查真相源", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.QUERY_TRUTH)

    def test_query_gate_failure_parse(self) -> None:
        result = self.compiler.parse("查门禁失败原因", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.QUERY_GATE_FAILURE)

    def test_query_quality_parse(self) -> None:
        result = self.compiler.parse("查章节质量", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.QUERY_CHAPTER_QUALITY)

    def test_unrecognized_returns_none(self) -> None:
        result = self.compiler.parse("随便说点什么", "book-a")
        self.assertIsNone(result)

    def test_approve_chapter_parse(self) -> None:
        result = self.compiler.parse("批准第3章", "book-a")
        assert result is not None
        self.assertEqual(result.action, IntentAction.APPROVE_CHAPTER)
        self.assertEqual(result.chapter_no, 3)

    def test_continue_chapter_executes_real_lifecycle_through_audit(self) -> None:
        self.engine.gate_runner.auditor = PassingAuditor()
        intent = ParsedIntent(
            action=IntentAction.CONTINUE_CHAPTER,
            book_id=self.book_id,
            chapter_no=1,
        )

        result = self.compiler.execute(intent)

        self.assertTrue(result.success)
        status = self.engine.get_chapter_status(self.book_id, 1)["status"]
        self.assertEqual(status.stage.value, "audited_passed")
        self.assertIn("plan", status.current_artifact_refs)
        self.assertIn("compose", status.current_artifact_refs)
        self.assertIn("draft", status.current_artifact_refs)
        self.assertIn("settlement", status.current_artifact_refs)
        self.assertIn("gate_decision", status.current_artifact_refs)
        self.assertTrue(result.result_refs)

    def test_continue_chapter_can_resume_from_audited_failed_into_revising(self) -> None:
        intent = ParsedIntent(
            action=IntentAction.CONTINUE_CHAPTER,
            book_id=self.book_id,
            chapter_no=1,
        )

        first = self.compiler.execute(intent)
        self.assertTrue(first.success)
        first_status = self.engine.get_chapter_status(self.book_id, 1)["status"]
        self.assertEqual(first_status.stage.value, "audited_failed")

        second = self.compiler.execute(intent)

        self.assertTrue(second.success)
        second_status = self.engine.get_chapter_status(self.book_id, 1)["status"]
        self.assertEqual(second_status.stage.value, "revising")
        self.assertIn("revision_brief", second_status.current_artifact_refs)
        self.assertIn("draft", second_status.current_artifact_refs)
        self.assertTrue(second.result_refs)

    def test_approve_check_reads_pending_comparison_from_notes(self) -> None:
        status = self._prepare_audited_passed_chapter(1)
        self.engine.repo.json.append_chapter_note(self.book_id, 1, {"pending_comparison": True})
        intent = ParsedIntent(
            action=IntentAction.APPROVE_CHAPTER,
            book_id=self.book_id,
            chapter_no=1,
        )

        check = self.compiler.check(intent)

        self.assertFalse(check.allowed)
        self.assertIn("pending_comparison", check.blockers)
        refreshed = self.engine.get_chapter_status(self.book_id, 1)["status"]
        self.assertEqual(refreshed.stage.value, status.stage.value)

    def test_query_truth_returns_current_snapshot_id(self) -> None:
        result = self.compiler.execute(
            ParsedIntent(action=IntentAction.QUERY_TRUTH, book_id=self.book_id)
        )

        self.assertTrue(result.success)
        self.assertIn("truth head: snapshot-", result.message)

    def test_export_intent_requires_approved_or_exported_stage(self) -> None:
        intent = ParsedIntent(
            action=IntentAction.EXPORT_CHAPTER,
            book_id=self.book_id,
            chapter_no=2,
            parameters={"platform": "tomato"},
        )

        check = self.compiler.check(intent)

        self.assertFalse(check.allowed)
        self.assertIn("stage_not_approved_or_exported:planned", check.blockers)

    def _prepare_audited_passed_chapter(self, chapter_no: int):
        self.engine.plan_chapter(self.book_id, chapter_no, guidance=f"chapter {chapter_no} should advance")
        self.engine.compose_chapter(self.book_id, chapter_no)
        draft_id = self.engine.write_chapter_draft(self.book_id, chapter_no, mode="initial")
        settled = self.engine.settle_chapter(self.book_id, chapter_no, draft_id)
        original = self.engine.gate_runner.auditor
        self.engine.gate_runner.auditor = PassingAuditor()
        try:
            audit = self.engine.audit_chapter(self.book_id, chapter_no, settled.current_artifact_refs["settlement"])
        finally:
            self.engine.gate_runner.auditor = original
        self.assertTrue(audit["gate_decision"]["passed"])
        return self.engine.get_chapter_status(self.book_id, chapter_no)["status"]


if __name__ == "__main__":
    unittest.main()
