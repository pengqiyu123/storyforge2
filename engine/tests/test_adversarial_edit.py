from __future__ import annotations

import unittest

from engine.services.adversarial_editor import AdversarialEditor


class AdversarialEditorTests(unittest.TestCase):
    def test_compress_generates_shorter_candidate(self) -> None:
        source = "林七贴着废仓库冰冷的墙根向前挪去。" * 20
        editor = AdversarialEditor()
        candidate = editor.generate_candidate(source, "compress")
        self.assertLess(len(candidate), len(source))
        self.assertGreater(len(candidate), 0)

    def test_redundant_truncates_without_llm(self) -> None:
        source = "沈砚没有回头。他知道今晚不能退。" * 30
        editor = AdversarialEditor()
        candidate = editor.generate_candidate(source, "redundant")
        self.assertLess(len(candidate), len(source))

    def test_overexplain_appends_marker_without_llm(self) -> None:
        source = "仓库深处的灯忽明忽暗。"
        editor = AdversarialEditor()
        candidate = editor.generate_candidate(source, "overexplain")
        self.assertIn("overexplain-marker", candidate)

    def test_decide_keeps_when_candidate_score_gte(self) -> None:
        record = AdversarialEditor.decide(
            original_overall_score=6.0,
            candidate_overall_score=6.5,
            source_settlement_artifact_id="settle-1",
            candidate_draft_artifact_id="draft-2",
            candidate_settlement_artifact_id="settle-2",
            edit_instruction="compress",
            original_char_count=1000,
            candidate_char_count=870,
        )
        self.assertTrue(record.kept)
        self.assertIn("candidate_score_gte_original", record.decision_reason_codes)
        self.assertEqual(record.reduction_stats["reduction"], 130)

    def test_decide_discards_when_candidate_worse(self) -> None:
        record = AdversarialEditor.decide(
            original_overall_score=7.0,
            candidate_overall_score=5.5,
            source_settlement_artifact_id="settle-1",
            candidate_draft_artifact_id="draft-2",
            candidate_settlement_artifact_id="settle-2",
            edit_instruction="compress",
            original_char_count=1000,
            candidate_char_count=870,
        )
        self.assertFalse(record.kept)
        self.assertIn("candidate_score_lt_original", record.decision_reason_codes)

    def test_compress_truncates_at_sentence_boundary(self) -> None:
        source = "第一句。第二句。第三句。第四句。第五句。"
        editor = AdversarialEditor()
        candidate = editor.generate_candidate(source, "compress")
        self.assertTrue(
            candidate.endswith("。") or candidate.endswith("！") or candidate.endswith("？") or len(candidate) < 10,
            f"candidate should end at sentence boundary, got: ...{candidate[-5:]}"
        )


if __name__ == "__main__":
    unittest.main()
