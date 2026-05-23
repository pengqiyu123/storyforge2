from __future__ import annotations

from engine.providers.llm_provider import LLMProvider


TRUTH_EXTRACT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "fact_assertions": {"type": "array"},
        "proposed_fact_updates": {"type": "array"},
        "character_updates": {"type": "array"},
        "relationship_updates": {"type": "array"},
        "hook_updates": {"type": "array"},
        "chapter_irreversible_facts": {"type": "array"},
        "notes": {"type": "array"},
    },
    "required": [
        "fact_assertions",
        "proposed_fact_updates",
        "character_updates",
        "relationship_updates",
        "hook_updates",
        "chapter_irreversible_facts",
        "notes",
    ],
}


class TruthExtractorAdapter:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def extract(
        self,
        *,
        book_id: str,
        chapter_no: int,
        draft_text: str,
        truth_snapshot: dict,
    ) -> dict:
        payload = self.provider.generate_json(
            "truth_extract",
            "你是中文小说真相提取器。只提取后续章节必须服从的事实，不要写解释。",
            {
                "book_id": book_id,
                "chapter_no": chapter_no,
                "draft_text": draft_text,
                "truth_snapshot": truth_snapshot,
            },
            TRUTH_EXTRACT_RESPONSE_SCHEMA,
        )
        payload = self._normalize_payload(payload, chapter_no=chapter_no)
        required = {
            "fact_assertions",
            "character_updates",
            "relationship_updates",
            "hook_updates",
            "chapter_irreversible_facts",
            "notes",
        }
        if "error" in payload or not required.issubset(payload):
            return {
                "fact_assertions": [],
                "proposed_fact_updates": [],
                "character_updates": [],
                "relationship_updates": [],
                "hook_updates": [],
                "chapter_irreversible_facts": [],
                "notes": [f"extraction_failed:{payload.get('error', 'invalid_payload')}"],
            }
        return payload

    def _normalize_payload(self, payload: dict, *, chapter_no: int) -> dict:
        if not isinstance(payload, dict) or "error" in payload:
            return payload
        structural_keys = {
            "fact_assertions",
            "proposed_fact_updates",
            "character_updates",
            "relationship_updates",
            "hook_updates",
            "chapter_irreversible_facts",
            "notes",
        }
        if not (structural_keys & set(payload)):
            return payload

        normalized = dict(payload)
        fact_assertions = []
        seen_fact_signatures: set[tuple[str, str]] = set()
        for index, item in enumerate(normalized.get("fact_assertions", []), start=1):
            if isinstance(item, dict):
                statement = str(item.get("statement", "")).strip()
                signature = (str(item.get("fact_id", "")).strip(), statement)
                if statement and signature not in seen_fact_signatures:
                    seen_fact_signatures.add(signature)
                    fact_assertions.append(item)
            elif isinstance(item, str) and item.strip():
                signature = ("", item.strip())
                if signature not in seen_fact_signatures:
                    seen_fact_signatures.add(signature)
                    fact_assertions.append(
                        {
                            "fact_id": f"fact-ch{chapter_no:04d}-{index}",
                            "category": "chapter_outcome",
                            "statement": item.strip(),
                            "hard": False,
                            "assertion_basis": "explicit",
                        }
                    )
        normalized["fact_assertions"] = fact_assertions

        proposed_fact_updates = []
        for item in normalized.get("proposed_fact_updates", []):
            if isinstance(item, dict):
                fact_payload = item.get("fact", item.get("add_fact"))
                if isinstance(fact_payload, dict):
                    statement = str(fact_payload.get("statement", "")).strip()
                    signature = (str(fact_payload.get("fact_id", "")).strip(), statement)
                    if statement and signature not in seen_fact_signatures:
                        seen_fact_signatures.add(signature)
                        proposed_fact_updates.append(fact_payload)
                elif isinstance(fact_payload, str) and fact_payload.strip():
                    signature = ("", fact_payload.strip())
                    if signature not in seen_fact_signatures:
                        seen_fact_signatures.add(signature)
                        proposed_fact_updates.append(
                            {
                                "category": "chapter_outcome",
                                "statement": fact_payload.strip(),
                                "hard": False,
                                "assertion_basis": "explicit",
                            }
                        )
                else:
                    proposed_fact_updates.append(item)
        normalized["proposed_fact_updates"] = proposed_fact_updates

        character_updates = []
        for index, item in enumerate(normalized.get("character_updates", []), start=1):
            if isinstance(item, dict) and "character_id" in item:
                character_updates.append(item)
                continue
            if isinstance(item, dict):
                character_name = item.get("character") or item.get("display_name") or f"角色{index}"
                updates = item.get("updates", [])
                location = None
                if isinstance(updates, list):
                    for update in updates:
                        if isinstance(update, str) and "出现在" in update:
                            location = update.split("出现在", 1)[-1].strip("。 ")
                            break
                character_updates.append(
                    {
                        "character_id": f"char-ch{chapter_no:04d}-{index}",
                        "display_name": character_name,
                        "status_tags": ["active"],
                        "current_location": location,
                        "known_fact_ids": [],
                    }
                )
        normalized["character_updates"] = character_updates

        hook_updates = []
        for index, item in enumerate(normalized.get("hook_updates", []), start=1):
            if isinstance(item, dict) and "hook_id" in item:
                hook_updates.append(item)
                continue
            if isinstance(item, dict):
                label = item.get("hook") or item.get("label") or f"hook-{index}"
                hook_updates.append(
                    {
                        "hook_id": f"hook-ch{chapter_no:04d}-{index}",
                        "label": label,
                        "kind": "hook",
                        "status": item.get("status", "open"),
                        "introduced_in": chapter_no,
                        "owner_entity_ids": [],
                        "source_fact_ids": [],
                    }
                )
        normalized["hook_updates"] = hook_updates

        normalized["relationship_updates"] = [
            item
            for item in normalized.get("relationship_updates", [])
            if isinstance(item, dict)
            and str(item.get("source_character_id", "")).strip()
            and str(item.get("target_character_id", "")).strip()
        ]
        normalized["chapter_irreversible_facts"] = [
            item for item in normalized.get("chapter_irreversible_facts", []) if isinstance(item, str) and item.strip()
        ]
        normalized["notes"] = [
            item for item in normalized.get("notes", []) if isinstance(item, str) and item.strip()
        ]
        return normalized
