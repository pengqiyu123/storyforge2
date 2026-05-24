from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.services.gate_runner import GateRunner
from engine.services.style_signal import StyleSignalAdapter
from engine.services.workspace_bridge import WorkspaceBridgeService
from engine.truth.truth_extractor_adapter import TruthExtractorAdapter


class WorkspaceBridgeTests(unittest.TestCase):
    def test_diagnose_workspace_chapter_reads_real_markdown_and_returns_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            book_dir = root / "我是路人甲"
            chapter_dir = book_dir / "chapters"
            chapter_dir.mkdir(parents=True, exist_ok=True)
            chapter_path = chapter_dir / "0001-剧本不对.md"
            chapter_path.write_text(
                "第一章 剧本不对\n\n"
                + ("林默沿着墙根往前走，心里先一阵发紧。雨像细针一样扎在手背上。"
                   "“你别过来。”他低声说，脚下却没停。") * 80,
                encoding="utf-8",
            )

            provider = FakeLLMProvider(
                json_responses=[
                    {
                        "fact_assertions": ["林默在雨夜前行。"],
                        "proposed_fact_updates": [],
                        "character_updates": [],
                        "relationship_updates": [],
                        "hook_updates": [],
                        "chapter_irreversible_facts": [],
                        "notes": [],
                    }
                ]
            )
            service = WorkspaceBridgeService(
                gate_runner=GateRunner(),
                style_signal=StyleSignalAdapter(drift_threshold=0.01),
                truth_extractor=TruthExtractorAdapter(provider),
            )

            result = service.diagnose_workspace_chapter(
                book_dir=book_dir,
                chapter_path=chapter_path,
                baseline_text="林默走到路口，抬头看了一眼。雨像雾一样压下来。" * 50,
            )

            self.assertEqual(result["chapter_no"], 1)
            self.assertIn("mechanical_gate", result)
            self.assertIn("gate_decision", result)
            self.assertIn("truth_probe", result)
            self.assertIsNotNone(result["style_signal"])


if __name__ == "__main__":
    unittest.main()
