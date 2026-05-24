from __future__ import annotations

import unittest

from engine.gates.llm_auditor_adapter import LLMAuditorAdapter
from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.schemas.artifact import AuditRecord
from engine.services.gate_runner import GateInputBundle, GateRunner, MIN_CHINESE_CHAR_COUNT


def long_chinese_draft() -> str:
    paragraph = (
        "chapter 1。林七贴着废仓库冰冷的墙根向前挪去，雨水从屋檐断断续续地砸下来，"
        "把他掌心里那枚钥匙浸得发凉。他知道今晚不能退，旧账册一旦落进沈砚的对头手里，"
        "城南三条线上的人都会被连根拔起。仓库深处的灯忽明忽暗，像有人故意把呼吸藏进黑暗里。"
        "林七没有立刻闯进去，只是先听，先辨门后脚步的轻重，再判断谁在等他。"
        "他贴着木箱一点点挪动，先确认二层铁梯有没有埋伏，再决定是否提前亮出底牌。"
    )
    return "\n\n".join([paragraph for _ in range(6)])


class PassingAuditor:
    def review(self, bundle) -> AuditRecord:
        return AuditRecord(
            passed=True,
            critical_count=0,
            issues=[],
            recommended_mode="accept",
            score_summary={"overall": 7.0, "logic": 6.8, "character": 7.1, "hook": 7.0, "pace": 7.1},
        )


class GateRunnerChineseTests(unittest.TestCase):
    def test_chinese_mechanical_gate_accepts_long_chinese_text(self) -> None:
        runner = GateRunner(auditor=PassingAuditor())
        bundle = GateInputBundle(
            book_id="book-a",
            chapter_no=1,
            revision_round=0,
            settlement_artifact_id="settlement-1",
            candidate_signature="sig-1",
            draft_text=long_chinese_draft(),
            plan_summary="推进冲突",
            compose_constraints=["保持悬念"],
            baseline_gate_summary={},
            revision_mode=None,
        )
        mechanical, audit, gate = runner.evaluate(bundle)
        self.assertFalse(mechanical.blocked)
        self.assertTrue(audit.passed)
        self.assertTrue(gate.passed)

    def test_mechanical_gate_blocks_when_chinese_chars_below_800(self) -> None:
        runner = GateRunner(auditor=PassingAuditor())
        short_text = "林七沿着仓库阴影往前走。" * (799 // 12)
        self.assertLess(len(short_text), 8000)
        bundle = GateInputBundle(
            book_id="book-a",
            chapter_no=1,
            revision_round=0,
            settlement_artifact_id="settlement-1",
            candidate_signature="sig-1",
            draft_text=short_text,
            plan_summary="推进冲突",
            compose_constraints=["保持悬念"],
            baseline_gate_summary={},
            revision_mode=None,
        )
        mechanical, _, gate = runner.evaluate(bundle)
        min_rule = next(result for result in mechanical.rule_results if result.rule_id == "below_min_word_count")
        self.assertEqual(MIN_CHINESE_CHAR_COUNT, 800)
        self.assertFalse(min_rule.passed)
        self.assertTrue(mechanical.blocked)
        self.assertFalse(gate.passed)

    def test_mechanical_gate_accepts_exactly_800_chinese_chars(self) -> None:
        runner = GateRunner(auditor=PassingAuditor())
        exact_text = "林" * 800
        bundle = GateInputBundle(
            book_id="book-a",
            chapter_no=1,
            revision_round=0,
            settlement_artifact_id="settlement-1",
            candidate_signature="sig-1",
            draft_text=exact_text,
            plan_summary="推进冲突",
            compose_constraints=["保持悬念"],
            baseline_gate_summary={},
            revision_mode=None,
        )
        mechanical, _, _ = runner.evaluate(bundle)
        min_rule = next(result for result in mechanical.rule_results if result.rule_id == "below_min_word_count")
        self.assertTrue(min_rule.passed)

    def test_llm_auditor_adapter_returns_audit_record_from_fake_provider(self) -> None:
        provider = FakeLLMProvider(
            json_responses=[
                {
                    "passed": True,
                    "critical_count": 0,
                    "issues": [],
                    "recommended_mode": "accept",
                    "score_summary": {
                        "overall": 7.1,
                        "logic": 7.0,
                        "character": 7.2,
                        "hook": 7.0,
                        "pace": 7.2,
                    },
                }
            ]
        )
        adapter = LLMAuditorAdapter(provider)
        audit = adapter.review(
            GateInputBundle(
                book_id="book-a",
                chapter_no=1,
                revision_round=0,
                settlement_artifact_id="settlement-1",
                candidate_signature="sig-1",
                draft_text=long_chinese_draft(),
                plan_summary="推进冲突",
                compose_constraints=["保持悬念"],
                baseline_gate_summary={},
                revision_mode=None,
            )
        )
        self.assertTrue(audit.passed)
        self.assertIn("overall", audit.score_summary)

    def test_llm_auditor_adapter_downgrades_invalid_payload_to_fail_record(self) -> None:
        provider = FakeLLMProvider(
            json_responses=[
                {
                    "passed": True,
                    "critical_count": 0,
                    "issues": [],
                    "recommended_mode": "accept",
                    "score_summary": {"overall": 7.1, "logic": 7.0},
                }
            ]
        )
        adapter = LLMAuditorAdapter(provider)
        audit = adapter.review(
            GateInputBundle(
                book_id="book-a",
                chapter_no=1,
                revision_round=0,
                settlement_artifact_id="settlement-1",
                candidate_signature="sig-1",
                draft_text=long_chinese_draft(),
                plan_summary="推进冲突",
                compose_constraints=["保持悬念"],
                baseline_gate_summary={},
                revision_mode=None,
            )
        )
        self.assertFalse(audit.passed)
        self.assertGreater(audit.critical_count, 0)
        self.assertEqual(audit.recommended_mode, "human_review")

    def test_llm_auditor_adapter_normalizes_weak_real_provider_payload(self) -> None:
        provider = FakeLLMProvider(
            json_responses=[
                {
                    "passed": False,
                    "critical_count": 1,
                    "issues": [
                        {
                            "severity": "major",
                            "title": "节奏拖慢",
                            "detail": "中段解释过多，动作推进不足。",
                            "suggestion": "删掉重复说明，保留关键动作。",
                        },
                        "章尾钩子不够集中",
                    ],
                    "recommended_mode": "轻修",
                    "score_summary": {
                        "overall": 72,
                        "clarity": 68,
                        "voice": 74,
                        "tension": 71,
                        "readability": 70,
                    },
                }
            ]
        )
        adapter = LLMAuditorAdapter(provider)
        audit = adapter.review(
            GateInputBundle(
                book_id="book-a",
                chapter_no=1,
                revision_round=1,
                settlement_artifact_id="settlement-1",
                candidate_signature="sig-1",
                draft_text=long_chinese_draft(),
                plan_summary="推进冲突",
                compose_constraints=["保持悬念"],
                baseline_gate_summary={},
                revision_mode="surgical",
            )
        )
        self.assertAlmostEqual(audit.score_summary["overall"], 7.2)
        self.assertIn("logic", audit.score_summary)
        self.assertIn("character", audit.score_summary)
        self.assertIn("hook", audit.score_summary)
        self.assertIn("pace", audit.score_summary)
        self.assertEqual(audit.recommended_mode, "surgical")
        self.assertEqual(audit.issues[0].severity, "critical")
        self.assertEqual(audit.issues[0].category, "节奏拖慢")
        self.assertEqual(audit.issues[1].severity, "warning")

    def test_workspace_report_and_meta_terms_are_detected(self) -> None:
        runner = GateRunner(auditor=PassingAuditor())
        draft_text = (
            "这一段正文已经满足最小字数。"
            "核心动机需要和当前处境一起交代清楚。"
            "到这里算是完成了第一步推进，读者可能已经意识到风险。"
        ) + ("林七在仓库里压低呼吸，贴着墙根向前走。" * 120)
        bundle = GateInputBundle(
            book_id="book-a",
            chapter_no=1,
            revision_round=0,
            settlement_artifact_id="settlement-1",
            candidate_signature="sig-1",
            draft_text=draft_text,
            plan_summary="推进冲突",
            compose_constraints=["保持悬念"],
            baseline_gate_summary={},
            revision_mode=None,
        )
        mechanical, _, _ = runner.evaluate(bundle)
        report_rule = next(result for result in mechanical.rule_results if result.rule_id == "report_term_leak")
        meta_rule = next(result for result in mechanical.rule_results if result.rule_id == "meta_narration_patterns")
        self.assertFalse(report_rule.passed)
        self.assertFalse(meta_rule.passed)
        self.assertIn("核心动机", report_rule.evidence)
        self.assertIn("到这里算是", meta_rule.evidence)

    def test_workspace_forbidden_but_rather_pattern_is_not_yet_mechanical_block(self) -> None:
        runner = GateRunner(auditor=PassingAuditor())
        draft_text = "不是他没看见，而是他故意把视线从林七身上滑开。" + ("林七沿着墙根前行。" * 400)
        bundle = GateInputBundle(
            book_id="book-a",
            chapter_no=1,
            revision_round=0,
            settlement_artifact_id="settlement-1",
            candidate_signature="sig-1",
            draft_text=draft_text,
            plan_summary="推进冲突",
            compose_constraints=["保持悬念"],
            baseline_gate_summary={},
            revision_mode=None,
        )
        mechanical, _, gate = runner.evaluate(bundle)
        forbidden_rule = next(result for result in mechanical.rule_results if result.rule_id == "forbidden_patterns")
        self.assertTrue(forbidden_rule.passed)
        self.assertFalse(mechanical.blocked)
        self.assertTrue(gate.passed)

    def test_llm_auditor_adapter_normalizes_plan7_dimension_names(self) -> None:
        provider = FakeLLMProvider(
            json_responses=[
                {
                    "passed": True,
                    "critical_count": 0,
                    "issues": [],
                    "recommended_mode": "continue",
                    "score_summary": {
                        "overall": 94,
                        "plot_coherence": 93,
                        "tension": 96,
                        "ending_hook": 97,
                        "language_fluency": 91,
                    },
                }
            ]
        )
        adapter = LLMAuditorAdapter(provider)
        audit = adapter.review(
            GateInputBundle(
                book_id="book-a",
                chapter_no=9,
                revision_round=0,
                settlement_artifact_id="settlement-9",
                candidate_signature="sig-9",
                draft_text=long_chinese_draft(),
                plan_summary="推进冲突",
                compose_constraints=["保持悬念"],
                baseline_gate_summary={},
                revision_mode=None,
            )
        )
        self.assertTrue(audit.passed)
        self.assertIn("logic", audit.score_summary)
        self.assertIn("character", audit.score_summary)
        self.assertIn("hook", audit.score_summary)
        self.assertIn("pace", audit.score_summary)
        self.assertGreater(audit.score_summary["hook"], 9.0)


if __name__ == "__main__":
    unittest.main()
