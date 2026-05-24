from __future__ import annotations

import unittest

from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.truth.truth_extractor_adapter import TruthExtractorAdapter


class TruthExtractorAdapterTests(unittest.TestCase):
    def test_fake_provider_returns_structured_truth_delta_payload(self) -> None:
        provider = FakeLLMProvider(
            json_responses=[
                {
                    "fact_assertions": [
                        {"category": "chapter_outcome", "statement": "林七拿到了旧账册", "hard": False}
                    ],
                    "proposed_fact_updates": [],
                    "character_updates": [
                        {
                            "character_id": "char-linqi",
                            "display_name": "林七",
                            "status_tags": ["active"],
                            "current_location": "旧仓库",
                            "known_fact_ids": ["fact-1"],
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
                            "source_fact_ids": ["fact-1"],
                        }
                    ],
                    "chapter_irreversible_facts": ["fact-1"],
                    "notes": [],
                }
            ]
        )
        adapter = TruthExtractorAdapter(provider)
        result = adapter.extract(
            book_id="book-a",
            chapter_no=1,
            draft_text="林七拿到了旧账册。",
            truth_snapshot={"snapshot_id": "snapshot-0000"},
        )
        self.assertEqual(result["character_updates"][0]["character_id"], "char-linqi")
        self.assertEqual(result["hook_updates"][0]["status"], "advanced")
        self.assertEqual(result["chapter_irreversible_facts"], ["fact-1"])
        self.assertEqual(result["proposed_fact_updates"], [])

    def test_invalid_payload_returns_extraction_failed_note(self) -> None:
        provider = FakeLLMProvider(json_responses=[{"unexpected": True}])
        adapter = TruthExtractorAdapter(provider)
        result = adapter.extract(
            book_id="book-a",
            chapter_no=1,
            draft_text="林七拿到了旧账册。",
            truth_snapshot={"snapshot_id": "snapshot-0000"},
        )
        self.assertEqual(result["fact_assertions"], [])
        self.assertEqual(result["proposed_fact_updates"], [])
        self.assertTrue(any("extraction_failed" in note for note in result["notes"]))

    def test_weak_real_provider_payload_is_normalized_for_truth_delta(self) -> None:
        provider = FakeLLMProvider(
            json_responses=[
                {
                    "fact_assertions": ["林七拿到了旧账册", "林七拿到了旧账册"],
                    "proposed_fact_updates": [
                        {"add_fact": "沈砚知道账册少了一页"},
                        {"fact": {"fact_id": "fact-2", "category": "clue", "statement": "仓库二层有人埋伏"}},
                    ],
                    "character_updates": [
                        {"character": "林七", "updates": ["出现在旧仓库", "保持警觉"]},
                    ],
                    "relationship_updates": [
                        {"source_character_id": "char-linqi", "target_character_id": "char-shenyan", "relation_type": "opposed"},
                        {"source_character_id": "", "target_character_id": "char-shenyan", "relation_type": "invalid"},
                    ],
                    "hook_updates": [
                        {"hook": "旧账册缺页", "status": "open"},
                    ],
                    "chapter_irreversible_facts": ["fact-2", ""],
                    "notes": ["extract_ok"],
                }
            ]
        )
        adapter = TruthExtractorAdapter(provider)
        result = adapter.extract(
            book_id="book-a",
            chapter_no=1,
            draft_text="林七拿到了旧账册，并发现少了一页。",
            truth_snapshot={"snapshot_id": "snapshot-0000"},
        )
        self.assertEqual(len(result["fact_assertions"]), 1)
        self.assertEqual(len(result["proposed_fact_updates"]), 2)
        self.assertEqual(result["character_updates"][0]["display_name"], "林七")
        self.assertEqual(result["character_updates"][0]["current_location"], "旧仓库")
        self.assertEqual(len(result["relationship_updates"]), 1)
        self.assertEqual(result["hook_updates"][0]["status"], "open")
        self.assertEqual(result["chapter_irreversible_facts"], ["fact-2"])

    def test_hook_status_variants_are_normalized(self) -> None:
        provider = FakeLLMProvider(
            json_responses=[
                {
                    "fact_assertions": [],
                    "proposed_fact_updates": [],
                    "character_updates": [],
                    "relationship_updates": [],
                    "hook_updates": [
                        {"hook": "旧账册缺页", "status": "partially_resolved"},
                        {"hook_id": "hook-2", "label": "黑伞男人身份", "kind": "hook", "status": "closed"},
                    ],
                    "chapter_irreversible_facts": [],
                    "notes": [],
                }
            ]
        )
        adapter = TruthExtractorAdapter(provider)
        result = adapter.extract(
            book_id="book-a",
            chapter_no=2,
            draft_text="林七逐步拼出线索。",
            truth_snapshot={"snapshot_id": "snapshot-0001"},
        )
        self.assertEqual(result["hook_updates"][0]["status"], "advanced")
        self.assertEqual(result["hook_updates"][1]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
