from __future__ import annotations

from engine.schemas.artifact import (
    CanonFactRecord,
    CharacterStateRecord,
    HookClueRecord,
    TruthConflictRecord,
)


ALLOWED_HOOK_TRANSITIONS = {
    "open": {"open", "advanced", "resolved", "invalidated"},
    "advanced": {"advanced", "resolved", "invalidated"},
    "resolved": {"resolved"},
    "invalidated": {"invalidated"},
}


def detect_truth_conflicts(
    *,
    chapter_no: int,
    draft_artifact_id: str,
    fact_additions: list[CanonFactRecord],
    fact_updates: list[CanonFactRecord],
    character_updates: list[CharacterStateRecord],
    hook_updates: list[HookClueRecord],
    irreversible_fact_ids: list[str],
    truth_snapshot: dict,
) -> list[TruthConflictRecord]:
    conflicts: list[TruthConflictRecord] = []
    conflicts.extend(
        detect_canon_conflicts(
            chapter_no=chapter_no,
            draft_artifact_id=draft_artifact_id,
            fact_updates=fact_updates,
            truth_snapshot=truth_snapshot,
        )
    )
    conflicts.extend(
        detect_character_conflicts(
            chapter_no=chapter_no,
            draft_artifact_id=draft_artifact_id,
            fact_additions=fact_additions,
            fact_updates=fact_updates,
            character_updates=character_updates,
            truth_snapshot=truth_snapshot,
        )
    )
    conflicts.extend(
        detect_hook_conflicts(
            chapter_no=chapter_no,
            draft_artifact_id=draft_artifact_id,
            hook_updates=hook_updates,
            truth_snapshot=truth_snapshot,
        )
    )
    conflicts.extend(
        detect_irreversible_fact_conflicts(
            chapter_no=chapter_no,
            draft_artifact_id=draft_artifact_id,
            fact_additions=fact_additions,
            fact_updates=fact_updates,
            irreversible_fact_ids=irreversible_fact_ids,
            truth_snapshot=truth_snapshot,
        )
    )
    return conflicts


def detect_canon_conflicts(
    *,
    chapter_no: int,
    draft_artifact_id: str,
    fact_updates: list[CanonFactRecord],
    truth_snapshot: dict,
) -> list[TruthConflictRecord]:
    existing_hard_facts = {
        fact["fact_id"]: fact
        for fact in truth_snapshot["canon"].get("facts", [])
        if fact.get("fact_id") and fact.get("hard")
    }
    conflicts: list[TruthConflictRecord] = []
    for fact_update in fact_updates:
        existing = existing_hard_facts.get(fact_update.fact_id)
        if not existing:
            continue
        if fact_update.statement != existing.get("statement") or fact_update.category != existing.get("category"):
            conflicts.append(
                TruthConflictRecord(
                    conflict_id=f"conflict-canon-{fact_update.fact_id}-{chapter_no}",
                    category="canon_conflict",
                    severity="blocking",
                    message=f"硬 canon 事实 {fact_update.fact_id} 被候选稿改写",
                    source_ref=draft_artifact_id,
                )
            )
    return conflicts


def detect_character_conflicts(
    *,
    chapter_no: int,
    draft_artifact_id: str,
    fact_additions: list[CanonFactRecord],
    fact_updates: list[CanonFactRecord],
    character_updates: list[CharacterStateRecord],
    truth_snapshot: dict,
) -> list[TruthConflictRecord]:
    existing_characters = {
        item["character_id"]: item
        for item in truth_snapshot["characters"].get("characters", [])
        if item.get("character_id")
    }
    known_fact_ids = {
        fact.get("fact_id")
        for fact in truth_snapshot["canon"].get("facts", [])
        if fact.get("fact_id")
    }
    known_fact_ids.update(item.fact_id for item in fact_additions)
    known_fact_ids.update(item.fact_id for item in fact_updates)
    conflicts: list[TruthConflictRecord] = []
    for character in character_updates:
        previous = existing_characters.get(character.character_id, {})
        if (
            previous.get("current_location")
            and character.current_location
            and previous.get("current_location") != character.current_location
        ):
            conflicts.append(
                TruthConflictRecord(
                    conflict_id=f"conflict-location-{character.character_id}-{chapter_no}",
                    category="character_location_conflict",
                    severity="blocking",
                    message=f"角色 {character.character_id} 的当前位置与已提交真相冲突",
                    source_ref=draft_artifact_id,
                )
            )
        unknown_fact_refs = [fact_id for fact_id in character.known_fact_ids if fact_id not in known_fact_ids]
        if unknown_fact_refs:
            conflicts.append(
                TruthConflictRecord(
                    conflict_id=f"conflict-knowledge-{character.character_id}-{chapter_no}",
                    category="knowledge_boundary_conflict",
                    severity="blocking",
                    message=f"角色 {character.character_id} 引用了未提交事实",
                    source_ref=draft_artifact_id,
                )
            )
    return conflicts


def detect_hook_conflicts(
    *,
    chapter_no: int,
    draft_artifact_id: str,
    hook_updates: list[HookClueRecord],
    truth_snapshot: dict,
) -> list[TruthConflictRecord]:
    existing_hooks = {
        item["hook_id"]: item
        for item in truth_snapshot["hook_ledger"].get("hooks", [])
        if item.get("hook_id")
    }
    conflicts: list[TruthConflictRecord] = []
    for hook in hook_updates:
        previous = existing_hooks.get(hook.hook_id, {})
        previous_status = previous.get("status")
        if previous_status and hook.status not in ALLOWED_HOOK_TRANSITIONS.get(previous_status, {previous_status}):
            conflicts.append(
                TruthConflictRecord(
                    conflict_id=f"conflict-hook-{hook.hook_id}-{chapter_no}",
                    category="hook_status_conflict",
                    severity="blocking",
                    message=f"hook {hook.hook_id} 状态从 {previous_status} 非法跳转到 {hook.status}",
                    source_ref=draft_artifact_id,
                )
            )
    return conflicts


def detect_irreversible_fact_conflicts(
    *,
    chapter_no: int,
    draft_artifact_id: str,
    fact_additions: list[CanonFactRecord],
    fact_updates: list[CanonFactRecord],
    irreversible_fact_ids: list[str],
    truth_snapshot: dict,
) -> list[TruthConflictRecord]:
    existing_irreversible: set[str] = set()
    for chapter_entry in truth_snapshot["chapter_facts"].get("chapters", []):
        existing_irreversible.update(chapter_entry.get("irreversible_fact_ids", []))
    added_or_updated_fact_ids = {fact.fact_id for fact in fact_additions} | {fact.fact_id for fact in fact_updates}
    conflicts: list[TruthConflictRecord] = []
    for fact_id in irreversible_fact_ids:
        if fact_id in existing_irreversible and fact_id not in added_or_updated_fact_ids:
            conflicts.append(
                TruthConflictRecord(
                    conflict_id=f"conflict-irreversible-{fact_id}-{chapter_no}",
                    category="irreversible_fact_conflict",
                    severity="blocking",
                    message=f"不可回退事实 {fact_id} 与现有真相链冲突",
                    source_ref=draft_artifact_id,
                )
            )
    return conflicts
