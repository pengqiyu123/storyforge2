import unittest

from engine.schemas.artifact import (
    CanonFactRecord,
    CharacterStateRecord,
    HookClueRecord,
    RelationshipEdgeRecord,
    TruthDeltaRecord,
)
from engine.truth.truth_snapshot_builder import apply_truth_delta_to_snapshot_payloads


def _base_snapshot() -> dict:
    return {
        "canon": {"facts": []},
        "characters": {"characters": [], "relationships": []},
        "hook_ledger": {"hooks": []},
        "chapter_facts": {"chapters": []},
    }


def _truth_delta(**overrides) -> TruthDeltaRecord:
    defaults = {
        "delta_id": "delta-1",
        "chapter_no": 1,
        "settlement_artifact_id": "settle-1",
        "draft_artifact_id": "draft-1",
        "base_snapshot_id": "snap-0000",
        "status": "reconciled",
    }
    defaults.update(overrides)
    return TruthDeltaRecord(**defaults)


class TruthSnapshotBuilderTests(unittest.TestCase):
    def test_relationship_updates_are_serialized_to_dict(self) -> None:
        delta = _truth_delta(
            proposed_relationship_updates=[
                RelationshipEdgeRecord(
                    edge_id="edge-1",
                    source_character_id="char-a",
                    target_character_id="char-b",
                    relation_type="ally",
                    status="active",
                    source_ref="draft-1",
                ),
            ],
        )
        result = apply_truth_delta_to_snapshot_payloads(
            base_snapshot_payloads=_base_snapshot(),
            truth_delta=delta,
            chapter_no=1,
            snapshot_id="snap-0001",
        )
        relationships = result["characters"]["relationships"]
        self.assertEqual(len(relationships), 1)
        self.assertIsInstance(relationships[0], dict)
        self.assertEqual(relationships[0]["edge_id"], "edge-1")

    def test_dedupe_relationships_by_edge_id(self) -> None:
        base = _base_snapshot()
        base["characters"]["relationships"] = [
            {"edge_id": "edge-1", "source_character_id": "char-a", "target_character_id": "char-b", "relation_type": "ally", "status": "active", "source_ref": "seed"},
        ]
        delta = _truth_delta(
            proposed_relationship_updates=[
                RelationshipEdgeRecord(
                    edge_id="edge-1",
                    source_character_id="char-a",
                    target_character_id="char-b",
                    relation_type="rival",
                    status="active",
                    source_ref="draft-1",
                ),
            ],
        )
        result = apply_truth_delta_to_snapshot_payloads(
            base_snapshot_payloads=base,
            truth_delta=delta,
            chapter_no=1,
            snapshot_id="snap-0001",
        )
        relationships = result["characters"]["relationships"]
        self.assertEqual(len(relationships), 1)
        self.assertEqual(relationships[0]["relation_type"], "rival")

    def test_all_update_types_produce_json_compatible_output(self) -> None:
        delta = _truth_delta(
            proposed_fact_additions=[
                CanonFactRecord(fact_id="f1", category="event", statement="event A", assertion_basis="explicit", source_ref="draft-1"),
            ],
            proposed_character_updates=[
                CharacterStateRecord(character_id="c1", display_name="角色甲", current_location="旧仓库", source_ref="draft-1"),
            ],
            proposed_hook_updates=[
                HookClueRecord(hook_id="h1", label="旧账册", kind="hook", status="open", introduced_in=1, source_ref="draft-1"),
            ],
            proposed_relationship_updates=[
                RelationshipEdgeRecord(edge_id="e1", source_character_id="c1", target_character_id="c2", relation_type="ally", status="active", source_ref="draft-1"),
            ],
        )
        result = apply_truth_delta_to_snapshot_payloads(
            base_snapshot_payloads=_base_snapshot(),
            truth_delta=delta,
            chapter_no=1,
            snapshot_id="snap-0001",
        )
        import json
        serialized = json.dumps(result, ensure_ascii=False)
        parsed = json.loads(serialized)
        self.assertEqual(parsed["characters"]["relationships"][0]["edge_id"], "e1")
        self.assertEqual(parsed["canon"]["facts"][0]["fact_id"], "f1")


if __name__ == "__main__":
    unittest.main()
