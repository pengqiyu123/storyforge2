from __future__ import annotations

import unittest

from engine.services.style_signal import StyleSignalAdapter


class StyleSignalAdapterTests(unittest.TestCase):
    def _make_slice(self, reference_text: str, current_text: str, chapter_no: int = 1) -> dict:
        return {
            "reference_text": reference_text,
            "current_text": current_text,
            "chapter_no": chapter_no,
            "reference_profile_ref": "ref-1",
        }

    def test_returns_none_when_texts_are_similar(self) -> None:
        text = "林七在雨里停住脚步。他听见仓库深处传来铁链碰撞声。" * 5
        adapter = StyleSignalAdapter()
        result = adapter.evaluate(
            batch_run=None,
            checkpoint=None,
            slice_payload=self._make_slice(text, text),
        )
        self.assertIsNone(result)

    def test_returns_review_when_styles_drift(self) -> None:
        reference = "林七贴着墙根走。沈砚回头看了他一眼。" * 10
        current = "似乎好像可能应该大概差不多左右有些某种程度上。" * 20
        adapter = StyleSignalAdapter(drift_threshold=0.1)
        result = adapter.evaluate(
            batch_run=None,
            checkpoint=None,
            slice_payload=self._make_slice(reference, current),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.recommended_action, "review")
        self.assertGreater(result.drift_score, 0.0)

    def test_returns_none_when_missing_texts(self) -> None:
        adapter = StyleSignalAdapter()
        result = adapter.evaluate(
            batch_run=None,
            checkpoint=None,
            slice_payload={"chapter_no": 1},
        )
        self.assertIsNone(result)

    def test_pairwise_detects_sentence_length_uniformity(self) -> None:
        adapter = StyleSignalAdapter(drift_threshold=0.01)
        baseline = "林七在雨里停住脚步。仓库深处传来铁链碰撞声。沈砚没有立刻露面。"
        candidate = "林七走进去。沈砚看着他。门外雨未停。楼上灯在闪。呼吸声很轻。"
        result = adapter.evaluate_pairwise(
            baseline_text=baseline,
            candidate_text=candidate,
            chapter_no=1,
            reference_profile_ref="settlement-1",
        )
        self.assertIsNotNone(result)
        self.assertIn("sentence_length_uniformity", result.dominant_drift_axes)

    def test_pairwise_detects_dialogue_and_metaphor_and_particles(self) -> None:
        adapter = StyleSignalAdapter(drift_threshold=0.01)
        baseline = "“你来了吧？”林七低声说。雨像细针一样落下来啊。" * 4
        candidate = "林七沿着墙根前行。他观察灯光变化。他确认门后脚步。他判断埋伏位置。" * 4
        result = adapter.evaluate_pairwise(
            baseline_text=baseline,
            candidate_text=candidate,
            chapter_no=1,
            reference_profile_ref="settlement-1",
        )
        self.assertIsNotNone(result)
        self.assertIn("dialogue_density", result.dominant_drift_axes)
        self.assertIn("metaphor_density", result.dominant_drift_axes)
        self.assertIn("discourse_particle_presence", result.dominant_drift_axes)


if __name__ == "__main__":
    unittest.main()
