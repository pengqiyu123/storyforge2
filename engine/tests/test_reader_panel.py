from __future__ import annotations

import unittest

from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.services.reader_panel import LLMReaderPanelAdapter


class LLMReaderPanelAdapterTests(unittest.TestCase):
    def test_returns_four_role_evaluation_from_fake_provider(self) -> None:
        provider = FakeLLMProvider(
            json_responses=[
                {
                    "editor_findings": ["节奏偏慢"],
                    "genre_reader_findings": ["钩子不足"],
                    "writer_findings": ["对话标签单一"],
                    "first_reader_findings": ["沉浸感良好"],
                    "momentum_loss": True,
                    "earned_ending": False,
                    "cut_candidate": ["第二段"],
                    "missing_scene": ["过渡场景"],
                    "thinnest_character": "沈砚",
                    "aggregate_recommendation": "pause",
                    "risk_flags": ["momentum_loss"],
                }
            ]
        )
        adapter = LLMReaderPanelAdapter(provider)
        result = adapter.evaluate(
            batch_run=None,
            checkpoint=None,
            slice_payload={"chapter_text": "林七走进了旧仓库。", "chapter_slice": [1, 2]},
        )
        self.assertEqual(result["editor_findings"], ["节奏偏慢"])
        self.assertTrue(result["momentum_loss"])
        self.assertEqual(result["thinnest_character"], "沈砚")
        self.assertEqual(result["aggregate_recommendation"], "pause")

    def test_falls_back_on_provider_error(self) -> None:
        provider = FakeLLMProvider(json_errors=["network down"])
        adapter = LLMReaderPanelAdapter(provider)
        result = adapter.evaluate(
            batch_run=None,
            checkpoint=None,
            slice_payload={"chapter_text": "文本。", "chapter_slice": [1]},
        )
        self.assertIn("panel_fallback", result["risk_flags"][0])
        self.assertEqual(result["aggregate_recommendation"], "continue")

    def test_falls_back_on_missing_chapter_text(self) -> None:
        provider = FakeLLMProvider()
        adapter = LLMReaderPanelAdapter(provider)
        result = adapter.evaluate(
            batch_run=None,
            checkpoint=None,
            slice_payload={"chapter_slice": [1]},
        )
        self.assertIn("panel_fallback", result["risk_flags"][0])


if __name__ == "__main__":
    unittest.main()
