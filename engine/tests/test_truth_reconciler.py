import unittest

from engine.schemas.artifact import CanonFactRecord, CharacterStateRecord, HookClueRecord
from engine.truth.truth_reconciler import detect_truth_conflicts


def _snapshot() -> dict:
    return {
        "canon": {
            "facts": [
                {
                    "fact_id": "canon-law-1",
                    "category": "world_rule",
                    "statement": "城南夜禁后不准通行",
                    "hard": True,
                },
                {
                    "fact_id": "fact-1",
                    "category": "scene",
                    "statement": "林七进入旧仓库",
                    "hard": False,
                },
            ]
        },
        "characters": {
            "characters": [
                {
                    "character_id": "char-linqi",
                    "display_name": "林七",
                    "current_location": "旧仓库",
                    "known_fact_ids": ["fact-1"],
                }
            ]
        },
        "hook_ledger": {
            "hooks": [
                {
                    "hook_id": "hook-ledger",
                    "label": "旧账册",
                    "kind": "hook",
                    "status": "advanced",
                }
            ]
        },
        "chapter_facts": {
            "chapters": [
                {
                    "chapter_no": 1,
                    "irreversible_fact_ids": ["fact-1"],
                }
            ]
        },
    }


class TruthReconcilerTests(unittest.TestCase):
    def test_detects_hard_canon_conflict(self) -> None:
        conflicts = detect_truth_conflicts(
            chapter_no=2,
            draft_artifact_id="draft-1",
            fact_additions=[],
            fact_updates=[
                CanonFactRecord(
                    fact_id="canon-law-1",
                    category="world_rule",
                    statement="城南夜禁后仍可随意通行",
                    hard=True,
                    assertion_basis="explicit",
                    source_ref="draft-1",
                )
            ],
            character_updates=[],
            hook_updates=[],
            irreversible_fact_ids=[],
            truth_snapshot=_snapshot(),
        )
        self.assertEqual([item.category for item in conflicts], ["canon_conflict"])

    def test_detects_character_location_conflict(self) -> None:
        conflicts = detect_truth_conflicts(
            chapter_no=2,
            draft_artifact_id="draft-1",
            fact_additions=[],
            fact_updates=[],
            character_updates=[
                CharacterStateRecord(
                    character_id="char-linqi",
                    display_name="林七",
                    status_tags=[],
                    current_location="码头",
                    relationship_refs=[],
                    known_fact_ids=["fact-1"],
                    last_updated_chapter=2,
                    source_ref="draft-1",
                )
            ],
            hook_updates=[],
            irreversible_fact_ids=[],
            truth_snapshot=_snapshot(),
        )
        self.assertEqual([item.category for item in conflicts], ["character_location_conflict"])

    def test_detects_knowledge_boundary_conflict(self) -> None:
        conflicts = detect_truth_conflicts(
            chapter_no=2,
            draft_artifact_id="draft-1",
            fact_additions=[],
            fact_updates=[],
            character_updates=[
                CharacterStateRecord(
                    character_id="char-linqi",
                    display_name="林七",
                    status_tags=[],
                    current_location="旧仓库",
                    relationship_refs=[],
                    known_fact_ids=["fact-1", "unknown-fact"],
                    last_updated_chapter=2,
                    source_ref="draft-1",
                )
            ],
            hook_updates=[],
            irreversible_fact_ids=[],
            truth_snapshot=_snapshot(),
        )
        self.assertEqual([item.category for item in conflicts], ["knowledge_boundary_conflict"])

    def test_detects_illegal_hook_transition(self) -> None:
        conflicts = detect_truth_conflicts(
            chapter_no=2,
            draft_artifact_id="draft-1",
            fact_additions=[],
            fact_updates=[],
            character_updates=[],
            hook_updates=[
                HookClueRecord(
                    hook_id="hook-ledger",
                    label="旧账册",
                    kind="hook",
                    status="open",
                    introduced_in=1,
                    resolved_in=None,
                    owner_entity_ids=[],
                    source_fact_ids=[],
                    scene_id=None,
                )
            ],
            irreversible_fact_ids=[],
            truth_snapshot=_snapshot(),
        )
        self.assertEqual([item.category for item in conflicts], ["hook_status_conflict"])

    def test_detects_irreversible_fact_conflict(self) -> None:
        conflicts = detect_truth_conflicts(
            chapter_no=2,
            draft_artifact_id="draft-1",
            fact_additions=[],
            fact_updates=[],
            character_updates=[],
            hook_updates=[],
            irreversible_fact_ids=["fact-1"],
            truth_snapshot=_snapshot(),
        )
        self.assertEqual([item.category for item in conflicts], ["irreversible_fact_conflict"])
