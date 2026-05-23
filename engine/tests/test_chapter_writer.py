from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.services.chapter_writer import (
    DEFAULT_WRITER_GENERATION_CONFIG,
    PlaceholderChapterWriter,
    RealChapterWriter,
)


class ChapterWriterTests(unittest.TestCase):
    def test_placeholder_writer_returns_text_for_initial_and_revision(self) -> None:
        writer = PlaceholderChapterWriter()
        initial = writer.generate_initial(chapter_no=1)
        revision = writer.generate_revision(chapter_no=1, revision_mode="surgical")
        self.assertIn("林七", initial)
        self.assertIn("修订稿", revision)

    def test_real_writer_uses_provider_and_accepts_wrapped_text(self) -> None:
        text = "```text\n" + ("林七在仓库里压低呼吸。" * 300) + "\n```"
        provider = FakeLLMProvider(text_responses=[text])
        writer = RealChapterWriter(provider=provider, generation_config=dict(DEFAULT_WRITER_GENERATION_CONFIG))
        result = writer.generate_initial(
            chapter_no=1,
            plan_payload={"guidance": "推进冲突"},
            compose_payload={"constraints": ["中文小说口吻"], "materials": ["仓库对峙"]},
            truth_context_slice={},
        )
        self.assertGreater(len(result), 100)
        self.assertEqual(provider.last_diagnostics["mode_used"], "fake_text")

    def test_real_writer_fails_on_provider_error(self) -> None:
        provider = FakeLLMProvider(text_errors=["network down"])
        writer = RealChapterWriter(provider=provider, generation_config=dict(DEFAULT_WRITER_GENERATION_CONFIG))
        with self.assertRaisesRegex(ValueError, "draft_generation_failed"):
            writer.generate_initial(
                chapter_no=1,
                plan_payload={},
                compose_payload={},
                truth_context_slice={},
            )

    def test_real_writer_fails_on_short_text(self) -> None:
        provider = FakeLLMProvider(text_responses=["林七走进仓库。"])
        writer = RealChapterWriter(provider=provider, generation_config=dict(DEFAULT_WRITER_GENERATION_CONFIG))
        with self.assertRaisesRegex(ValueError, "below_min_chinese_char_count"):
            writer.generate_initial(
                chapter_no=1,
                plan_payload={},
                compose_payload={},
                truth_context_slice={},
            )


if __name__ == "__main__":
    unittest.main()
