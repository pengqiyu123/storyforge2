from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.cli.main import run_chapter_full_cycle
from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.schemas.artifact import AuditIssue, AuditRecord
from engine.services import StoryEngineService
from engine.truth.truth_extractor_adapter import TruthExtractorAdapter


def _truth_payload() -> dict:
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
        "notes": ["truth_ok"],
    }


class SequenceAuditor:
    def __init__(self, responses: list[AuditRecord]) -> None:
        self._responses = list(responses)

    def review(self, bundle) -> AuditRecord:
        if not self._responses:
            raise AssertionError("no auditor response remaining")
        return self._responses.pop(0)


def _audit(
    *,
    passed: bool,
    overall: float,
    critical_count: int = 0,
    logic: float | None = None,
    character: float | None = None,
    hook: float | None = None,
    pace: float | None = None,
    issues: list[AuditIssue] | None = None,
    recommended_mode: str = "accept",
) -> AuditRecord:
    score = {
        "overall": overall,
        "logic": logic if logic is not None else overall,
        "character": character if character is not None else overall,
        "hook": hook if hook is not None else overall,
        "pace": pace if pace is not None else overall,
    }
    return AuditRecord(
        passed=passed,
        critical_count=critical_count,
        issues=issues or [],
        recommended_mode=recommended_mode,
        score_summary=score,
    )


class ChapterFullCycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.book_id = "cycle-book"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _service(self) -> StoryEngineService:
        service = StoryEngineService(self.root)
        service.truth_extractor = TruthExtractorAdapter(
            FakeLLMProvider(json_responses=[_truth_payload() for _ in range(20)])
        )
        return service

    def test_full_cycle_revises_then_exports(self) -> None:
        service = self._service()
        service.gate_runner.auditor = SequenceAuditor(
            [
                _audit(
                    passed=False,
                    overall=5.2,
                    critical_count=1,
                    issues=[AuditIssue(severity="critical", category="logic", description="初稿仍需收紧逻辑")],
                    recommended_mode="surgical",
                ),
                _audit(
                    passed=True,
                    overall=6.9,
                    logic=6.8,
                    character=6.9,
                    hook=7.0,
                    pace=6.9,
                ),
            ]
        )

        result = run_chapter_full_cycle(
            service=service,
            book_id=self.book_id,
            title="Cycle Book",
            chapter_no=1,
        )

        self.assertEqual(result["final_outcome"], "exported")
        self.assertEqual(result["final_stage"], "exported")
        self.assertTrue(result["approved"])
        self.assertTrue(result["exported"])
        self.assertEqual(result["revision_rounds_executed"], 1)
        self.assertEqual(len(result["comparison_history"]), 1)
        self.assertEqual(result["comparison_history"][0]["decision"], "keep")
        self.assertIsNotNone(result["approval_receipt_artifact_id"])
        self.assertIsNotNone(result["export_manifest_artifact_id"])
        self.assertEqual(len(result["audit_diagnostics_history"]), 2)
        self.assertEqual(len(result["truth_diagnostics_history"]), 2)
        self.assertIn("style_signal_artifact_id", result)
        self.assertIn("style_drift_axes", result)

    def test_full_cycle_stops_on_compare_rollback(self) -> None:
        service = self._service()
        service.gate_runner.auditor = SequenceAuditor(
            [
                _audit(
                    passed=False,
                    overall=5.4,
                    critical_count=1,
                    issues=[AuditIssue(severity="critical", category="logic", description="初稿不稳")],
                    recommended_mode="surgical",
                ),
                _audit(
                    passed=False,
                    overall=5.6,
                    logic=4.7,
                    character=6.0,
                    hook=5.9,
                    pace=5.8,
                    critical_count=1,
                    issues=[AuditIssue(severity="critical", category="logic", description="核心维度下滑")],
                    recommended_mode="rework",
                ),
            ]
        )

        result = run_chapter_full_cycle(
            service=service,
            book_id=self.book_id,
            title="Cycle Book",
            chapter_no=1,
        )

        self.assertEqual(result["final_outcome"], "rolled_back")
        self.assertEqual(result["final_stage"], "rolled_back")
        self.assertFalse(result["approved"])
        self.assertFalse(result["exported"])
        self.assertEqual(result["comparison_history"][0]["decision"], "rollback")

    def test_full_cycle_stops_when_max_auto_rounds_reached(self) -> None:
        service = self._service()
        service.gate_runner.auditor = SequenceAuditor(
            [
                _audit(
                    passed=False,
                    overall=5.2,
                    critical_count=1,
                    issues=[AuditIssue(severity="critical", category="logic", description="初稿不通过")],
                    recommended_mode="surgical",
                ),
                _audit(
                    passed=False,
                    overall=5.8,
                    logic=5.8,
                    character=5.8,
                    hook=5.8,
                    pace=5.8,
                    critical_count=1,
                    issues=[AuditIssue(severity="critical", category="logic", description="仍需人工判断")],
                    recommended_mode="surgical",
                ),
            ]
        )

        result = run_chapter_full_cycle(
            service=service,
            book_id=self.book_id,
            title="Cycle Book",
            chapter_no=1,
            max_auto_rounds=1,
        )

        self.assertEqual(result["final_outcome"], "audited_failed")
        self.assertEqual(result["final_stage"], "audited_failed")
        self.assertFalse(result["approved"])
        self.assertFalse(result["exported"])
        self.assertEqual(result["revision_rounds_executed"], 1)

    def test_full_cycle_returns_structured_draft_generation_failure(self) -> None:
        service = self._service()
        service._chapter_workflow.writer = type(
            "FailingWriter",
            (),
            {
                "generate_initial": lambda *args, **kwargs: (_ for _ in ()).throw(
                    ValueError("draft_generation_failed:provider_down")
                )
            },
        )()

        result = run_chapter_full_cycle(
            service=service,
            book_id=self.book_id,
            title="Cycle Book",
            chapter_no=1,
        )

        self.assertEqual(result["final_outcome"], "draft_generation_failed")
        self.assertIn("draft_generation_failed", result["error"])

    def test_full_cycle_returns_structured_draft_generation_failure_during_revision(self) -> None:
        service = self._service()
        service.gate_runner.auditor = SequenceAuditor(
            [
                _audit(
                    passed=False,
                    overall=5.2,
                    critical_count=1,
                    issues=[AuditIssue(severity="critical", category="logic", description="初稿不通过")],
                    recommended_mode="surgical",
                )
            ]
        )
        placeholder = service._chapter_workflow.writer
        service._chapter_workflow.writer = type(
            "RevisionFailingWriter",
            (),
            {
                "generate_initial": lambda self, **kwargs: placeholder.generate_initial(**kwargs),
                "generate_revision": lambda *args, **kwargs: (_ for _ in ()).throw(
                    ValueError("draft_generation_failed:provider_timeout")
                ),
            },
        )()

        result = run_chapter_full_cycle(
            service=service,
            book_id=self.book_id,
            title="Cycle Book",
            chapter_no=1,
        )

        self.assertEqual(result["final_outcome"], "draft_generation_failed")
        self.assertIn("draft_generation_failed", result["error"])


if __name__ == "__main__":
    unittest.main()
