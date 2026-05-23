from __future__ import annotations

import json

from engine.schemas.artifact import ChapterFactRecord, TruthCommitReceiptRecord, TruthDeltaRecord


def dedupe_by_id(items: list[dict], key: str) -> list[dict]:
    latest: dict[str, dict] = {}
    for item in items:
        latest[item[key]] = item
    return list(latest.values())


def apply_truth_delta_to_snapshot_payloads(
    *,
    base_snapshot_payloads: dict,
    truth_delta: TruthDeltaRecord,
    chapter_no: int,
    snapshot_id: str,
) -> dict[str, dict]:
    canon = json.loads(json.dumps(base_snapshot_payloads["canon"], ensure_ascii=False))
    characters = json.loads(json.dumps(base_snapshot_payloads["characters"], ensure_ascii=False))
    hook_ledger = json.loads(json.dumps(base_snapshot_payloads["hook_ledger"], ensure_ascii=False))
    chapter_facts = json.loads(json.dumps(base_snapshot_payloads["chapter_facts"], ensure_ascii=False))

    canon["facts"].extend(item.model_dump(mode="json") for item in truth_delta.proposed_fact_additions)
    canon["facts"] = dedupe_by_id(canon["facts"], "fact_id")

    characters["characters"].extend(item.model_dump(mode="json") for item in truth_delta.proposed_character_updates)
    characters["characters"] = dedupe_by_id(characters["characters"], "character_id")
    characters["relationships"].extend(item for item in truth_delta.proposed_relationship_updates)
    characters["relationships"] = dedupe_by_id(characters["relationships"], "edge_id")

    hook_ledger["hooks"].extend(item.model_dump(mode="json") for item in truth_delta.proposed_hook_updates)
    hook_ledger["hooks"] = dedupe_by_id(hook_ledger["hooks"], "hook_id")

    chapter_facts["chapters"] = [
        entry for entry in chapter_facts.get("chapters", []) if entry.get("chapter_no") != chapter_no
    ]
    chapter_facts["chapters"].append(
        ChapterFactRecord(
            chapter_no=chapter_no,
            fact_ids=[item.fact_id for item in truth_delta.proposed_fact_additions],
            irreversible_fact_ids=truth_delta.chapter_irreversible_fact_ids,
            truth_delta_id=truth_delta.delta_id,
            committed_snapshot_id=snapshot_id,
            event_refs=[],
        ).model_dump(mode="json")
    )
    chapter_facts["chapters"].sort(key=lambda item: item["chapter_no"])

    return {
        "canon": canon,
        "characters": characters,
        "hook_ledger": hook_ledger,
        "chapter_facts": chapter_facts,
    }


def build_truth_snapshot_payload(
    *,
    snapshot_id: str,
    base_snapshot_id: str,
    chapter_no: int,
    run_id: str,
) -> dict:
    return {
        "snapshot_id": snapshot_id,
        "base_snapshot_id": base_snapshot_id,
        "committed_through_chapter": chapter_no,
        "created_by_run_id": run_id,
    }


def build_truth_commit_receipt(
    *,
    chapter_no: int,
    truth_delta: TruthDeltaRecord,
    truth_delta_artifact_id: str,
    snapshot_id: str,
) -> TruthCommitReceiptRecord:
    return TruthCommitReceiptRecord(
        receipt_id=f"truth-commit-{chapter_no:04d}",
        chapter_no=chapter_no,
        base_snapshot_id=truth_delta.base_snapshot_id,
        new_snapshot_id=snapshot_id,
        truth_delta_artifact_id=truth_delta_artifact_id,
        changed_entity_ids=[item.character_id for item in truth_delta.proposed_character_updates],
        changed_hook_ids=[item.hook_id for item in truth_delta.proposed_hook_updates],
        changed_fact_ids=[item.fact_id for item in truth_delta.proposed_fact_additions],
        affected_ledgers=["canon", "characters", "hook_ledger", "chapter_facts"],
        committed_from_chapter=chapter_no,
    )
