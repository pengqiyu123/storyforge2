from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from engine.schemas.artifact import (
    CanonFactRecord,
    ChapterComposeRecord,
    ChapterFactRecord,
    ChapterSettlementRecord,
    CharacterStateRecord,
    FactAssertionBasis,
    HookClueRecord,
    PropagationDebtRecord,
    TruthCommitReceiptRecord,
    TruthConflictRecord,
    TruthDeltaRecord,
    TruthIndexRecord,
    TruthInvalidationRecord,
)
from engine.schemas.chapter import ChapterStage, ChapterStatusRecord
from engine.state_machine import next_status
from engine.truth import (
    apply_truth_delta_to_snapshot_payloads,
    build_truth_commit_receipt,
    build_truth_snapshot_payload,
    dedupe_by_id,
    detect_truth_conflicts,
)


@dataclass(slots=True)
class TruthService:
    repo: object
    register_artifact: Callable[[str, int, str, dict, str], str]
    start_run: Callable[[str, int, str, str, list[str] | None], object]
    finish_run: Callable[[str, int, str, str, list[str], str | None], object]
    load_artifact_payload: Callable[[str, str], dict]
    artifact_record: Callable[[str, str], object]
    truth_extractor: object
    utc_now: Callable[[], object]

    def get_truth_head(self, book_id: str) -> dict:
        truth = self.repo.json.load_truth_payloads(book_id)
        snapshot_id = truth["truth_index"]["current_snapshot_id"]
        snapshot = self.repo.json.load_truth_snapshot(book_id, snapshot_id)
        return {"truth_index": truth["truth_index"], "truth_snapshot": snapshot}

    def list_propagation_debts(self, book_id: str, status: str | None = None) -> list[dict]:
        entries = self.repo.json.load_propagation_debts(book_id)
        if status is not None:
            entries = [entry for entry in entries if entry.get("status") == status]
        return entries

    def get_chapter_truth_freshness(self, book_id: str, chapter_no: int) -> dict:
        truth_index = self.repo.json.load_truth_index(book_id)
        basis_snapshot_id = self._get_chapter_truth_basis(book_id, chapter_no)
        open_debts = [
            entry
            for entry in self.repo.json.load_propagation_debts(book_id)
            if entry.get("chapter_no") == chapter_no and entry.get("status") == "open" and entry.get("blocking", True)
        ]
        return {
            "chapter_no": chapter_no,
            "basis_snapshot_id": basis_snapshot_id,
            "current_snapshot_id": truth_index.current_snapshot_id,
            "is_fresh": basis_snapshot_id in {None, truth_index.current_snapshot_id} and not open_debts,
            "open_blocking_debts": open_debts,
        }

    def reset_invalidated_chapter(self, book_id: str, chapter_no: int) -> ChapterStatusRecord:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        if status.stage != ChapterStage.INVALIDATED:
            raise ValueError("reset_invalidated_chapter requires invalidated stage")
        run = self.start_run(book_id, chapter_no, "init", "system", [])
        refreshed = next_status(status, ChapterStage.PLANNED, run_id=run.run_id, artifact_refs={})
        refreshed = refreshed.model_copy(
            update={
                "current_artifact_refs": self.repo.json.filter_runtime_refs_for_reset(status.current_artifact_refs),
                "invalidated_by": None,
                "blocked_reason": None,
                "last_run_id": run.run_id,
                "updated_at": self.utc_now(),
            }
        )
        self.repo.save_chapter_status(refreshed)
        for debt in self.repo.json.load_propagation_debts(book_id):
            if debt.get("chapter_no") == chapter_no and debt.get("status") == "open":
                self.repo.json.resolve_propagation_debt(book_id, debt["debt_id"], self.utc_now().isoformat())
        self.finish_run(book_id, chapter_no, run.run_id, "succeeded", [], None)
        return refreshed

    def invalidate_downstream(self, book_id: str, from_artifact: str) -> dict:
        receipt = TruthCommitReceiptRecord.model_validate(self.load_artifact_payload(book_id, from_artifact))
        run = self.start_run(book_id, receipt.committed_from_chapter, "invalidate", "system", [from_artifact])
        created_refs = self._detect_propagation_debts(book_id, from_artifact, run.run_id)
        debts = [
            entry
            for entry in self.repo.json.load_propagation_debts(book_id)
            if entry.get("source_truth_commit_receipt_id") == from_artifact
        ]
        self.finish_run(book_id, receipt.committed_from_chapter, run.run_id, "succeeded", created_refs, None)
        return {
            "invalidated_chapter_nos": sorted({int(entry["chapter_no"]) for entry in debts}),
            "propagation_debt_ids": [entry["debt_id"] for entry in debts],
            "created_refs": created_refs,
        }

    def _get_chapter_truth_basis(self, book_id: str, chapter_no: int) -> str | None:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        truth_commit_receipt_id = status.current_artifact_refs.get("truth_commit_receipt")
        if truth_commit_receipt_id:
            try:
                receipt_payload = TruthCommitReceiptRecord.model_validate(
                    self.load_artifact_payload(book_id, truth_commit_receipt_id)
                )
            except FileNotFoundError:
                return "__missing_truth_commit_receipt__"
            return receipt_payload.new_snapshot_id
        settlement_id = status.current_artifact_refs.get("settlement")
        if settlement_id:
            try:
                settlement_payload = ChapterSettlementRecord.model_validate(self.load_artifact_payload(book_id, settlement_id))
            except FileNotFoundError:
                return "__missing_settlement__"
            if settlement_payload.base_truth_snapshot_id:
                return settlement_payload.base_truth_snapshot_id
        compose_id = status.current_artifact_refs.get("compose")
        if compose_id:
            try:
                compose_payload = ChapterComposeRecord.model_validate(self.load_artifact_payload(book_id, compose_id))
            except FileNotFoundError:
                return "__missing_compose__"
            if compose_payload.truth_snapshot_id:
                return compose_payload.truth_snapshot_id
        return None

    def _assert_truth_fresh_for_action(self, book_id: str, chapter_no: int, action: str) -> None:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        if status.stage == ChapterStage.INVALIDATED:
            raise ValueError(f"chapter {chapter_no} is invalidated and cannot {action}")
        truth_index = self.repo.json.load_truth_index(book_id)
        basis_snapshot_id = self._get_chapter_truth_basis(book_id, chapter_no)
        open_debts = [
            entry
            for entry in self.repo.json.load_propagation_debts(book_id)
            if entry.get("chapter_no") == chapter_no and entry.get("status") == "open" and entry.get("blocking", True)
        ]
        if open_debts:
            raise ValueError(
                f"chapter {chapter_no} is stale against truth head {truth_index.current_snapshot_id}; re-compose from current truth before continuing"
            )
        if basis_snapshot_id is not None and basis_snapshot_id != truth_index.current_snapshot_id:
            raise ValueError(
                f"chapter {chapter_no} is stale against truth head {truth_index.current_snapshot_id}; re-compose from current truth before continuing"
            )

    def _detect_propagation_debts(self, book_id: str, truth_receipt_id: str, run_id: str) -> list[str]:
        receipt = TruthCommitReceiptRecord.model_validate(self.load_artifact_payload(book_id, truth_receipt_id))
        index = self.repo.json.load_book_index(book_id)
        truth_index = self.repo.json.load_truth_index(book_id)
        created_refs: list[str] = []
        invalidated_chapters: list[int] = []
        debt_ids: list[str] = []
        for chapter_no in index.chapters:
            if chapter_no <= receipt.committed_from_chapter:
                continue
            status = self.repo.load_chapter_status(book_id, chapter_no)
            if status.stage in {ChapterStage.PLANNED, ChapterStage.INVALIDATED}:
                continue
            basis_snapshot_id = self._get_chapter_truth_basis(book_id, chapter_no)
            if basis_snapshot_id is None or basis_snapshot_id == truth_index.current_snapshot_id:
                continue
            debt = PropagationDebtRecord(
                debt_id=f"debt-{chapter_no:04d}-{uuid4().hex[:8]}",
                chapter_no=chapter_no,
                trigger_chapter_no=receipt.committed_from_chapter,
                stale_snapshot_id=basis_snapshot_id,
                current_snapshot_id=truth_index.current_snapshot_id,
                source_truth_commit_receipt_id=truth_receipt_id,
                source_truth_delta_artifact_id=receipt.truth_delta_artifact_id,
                dependency_scope="snapshot_only",
                reason_code="stale_truth_head",
                blocking=True,
                status="open",
                dependency_hits={},
            )
            created_refs.extend(self._apply_propagation_invalidation(book_id, chapter_no, debt, run_id))
            invalidated_chapters.append(chapter_no)
            debt_ids.append(debt.debt_id)
        if invalidated_chapters or debt_ids:
            updated = receipt.model_copy(
                update={
                    "invalidated_chapter_nos": invalidated_chapters,
                    "propagation_debt_ids": debt_ids,
                }
            )
            receipt_record = self.artifact_record(book_id, truth_receipt_id)
            book_root = self.repo.json.ensure_book_dirs(book_id)["book_root"]
            self.repo.json.write_json(book_root / receipt_record.payload_ref.relative_path, updated.model_dump(mode="json"))
        return created_refs

    def _apply_propagation_invalidation(
        self,
        book_id: str,
        chapter_no: int,
        debt: PropagationDebtRecord,
        run_id: str,
    ) -> list[str]:
        debt_id = self.register_artifact(
            book_id,
            chapter_no,
            "propagation_debt",
            debt.model_dump(mode="json"),
            run_id,
        )
        self.repo.json.append_propagation_debt(book_id, debt.model_dump(mode="json"))
        invalidation = TruthInvalidationRecord(
            chapter_no=chapter_no,
            trigger_chapter_no=debt.trigger_chapter_no,
            source_snapshot_id=debt.stale_snapshot_id,
            superseded_by_snapshot_id=debt.current_snapshot_id,
            stale_snapshot_id=debt.stale_snapshot_id,
            current_snapshot_id=debt.current_snapshot_id,
            source_truth_commit_receipt_id=debt.source_truth_commit_receipt_id,
            debt_id=debt.debt_id,
            reason="upstream truth head advanced",
            affected_refs=[],
            created_by_run_id=run_id,
        )
        invalidation_id = self.register_artifact(
            book_id,
            chapter_no,
            "truth_invalidation",
            invalidation.model_dump(mode="json"),
            run_id,
        )
        self.repo.json.append_truth_invalidation(book_id, invalidation.model_dump(mode="json"))
        status = self.repo.load_chapter_status(book_id, chapter_no)
        invalidated = next_status(
            status,
            ChapterStage.INVALIDATED,
            run_id=run_id,
            artifact_refs={"propagation_debt": debt_id, "truth_invalidation": invalidation_id},
            invalidated_by=debt.source_truth_commit_receipt_id,
        )
        self.repo.save_chapter_status(invalidated)
        self.repo.json.append_invalidation_entry(
            book_id,
            {
                "chapter_no": chapter_no,
                "source_ref": debt.source_truth_commit_receipt_id,
                "reason": debt.reason_code,
                "run_id": run_id,
                "created_at": self.utc_now().isoformat(),
            },
        )
        self.repo._read_model(book_id).upsert_propagation_debt(
            debt_id=debt.debt_id,
            book_id=book_id,
            chapter_no=chapter_no,
            trigger_chapter_no=debt.trigger_chapter_no,
            stale_snapshot_id=debt.stale_snapshot_id,
            current_snapshot_id=debt.current_snapshot_id,
            source_truth_commit_receipt_id=debt.source_truth_commit_receipt_id,
            dependency_scope=debt.dependency_scope,
            reason_code=debt.reason_code,
            blocking=debt.blocking,
            status=debt.status,
            dependency_hits_json=json.dumps(debt.dependency_hits, ensure_ascii=False),
            created_at=debt.created_at.isoformat(),
            resolved_at=debt.resolved_at.isoformat() if debt.resolved_at else None,
        )
        self.repo._read_model(book_id).upsert_truth_invalidation(
            debt_id=debt.debt_id,
            book_id=book_id,
            chapter_no=chapter_no,
            trigger_chapter_no=debt.trigger_chapter_no,
            stale_snapshot_id=debt.stale_snapshot_id,
            current_snapshot_id=debt.current_snapshot_id,
            source_truth_commit_receipt_id=debt.source_truth_commit_receipt_id,
            created_at=self.utc_now().isoformat(),
        )
        return [debt_id, invalidation_id]

    def _build_truth_context(self, book_id: str) -> dict:
        truth_index = self.repo.json.load_truth_index(book_id)
        snapshot = self.repo.json.load_truth_snapshot(book_id, truth_index.current_snapshot_id)
        return {
            "truth_snapshot_id": snapshot["snapshot_id"],
            "truth_context_refs": {
                "canon": snapshot["canon_ref"],
                "characters": snapshot["character_ref"],
                "hook_ledger": snapshot["hook_ref"],
                "chapter_facts": snapshot["chapter_fact_ref"],
            },
            "truth_context_slice": {
                "canon": self.repo.json.read_json(self.repo.json.ensure_book_dirs(book_id)["book_root"] / snapshot["canon_ref"]),
                "characters": self.repo.json.read_json(
                    self.repo.json.ensure_book_dirs(book_id)["book_root"] / snapshot["character_ref"]
                ),
                "hook_ledger": self.repo.json.read_json(
                    self.repo.json.ensure_book_dirs(book_id)["book_root"] / snapshot["hook_ref"]
                ),
                "chapter_facts": self.repo.json.read_json(
                    self.repo.json.ensure_book_dirs(book_id)["book_root"] / snapshot["chapter_fact_ref"]
                ),
            },
        }

    def _build_truth_delta(
        self,
        *,
        book_id: str,
        chapter_no: int,
        settlement_artifact_id: str,
        draft_artifact_id: str,
        base_snapshot_id: str,
        draft_text: str,
    ) -> TruthDeltaRecord:
        truth_snapshot = self.repo.json.load_truth_snapshot_payloads(book_id, base_snapshot_id)
        extracted = self.truth_extractor.extract(
            book_id=book_id,
            chapter_no=chapter_no,
            draft_text=draft_text,
            truth_snapshot=truth_snapshot,
        )
        if (
            extracted["fact_assertions"]
            or extracted.get("proposed_fact_updates")
            or extracted["character_updates"]
            or extracted["hook_updates"]
        ):
            return self._truth_delta_from_extracted(
                chapter_no=chapter_no,
                settlement_artifact_id=settlement_artifact_id,
                draft_artifact_id=draft_artifact_id,
                base_snapshot_id=base_snapshot_id,
                extracted=extracted,
                truth_snapshot=truth_snapshot,
            )
        return self._build_truth_delta_fallback(
            chapter_no=chapter_no,
            settlement_artifact_id=settlement_artifact_id,
            draft_artifact_id=draft_artifact_id,
            base_snapshot_id=base_snapshot_id,
            draft_text=draft_text,
            notes=list(extracted.get("notes", [])),
        )

    def _build_truth_delta_fallback(
        self,
        *,
        chapter_no: int,
        settlement_artifact_id: str,
        draft_artifact_id: str,
        base_snapshot_id: str,
        draft_text: str,
        notes: list[str],
    ) -> TruthDeltaRecord:
        normalized = draft_text.strip()
        fact = CanonFactRecord(
            fact_id=f"fact-ch{chapter_no:04d}-1",
            category="chapter_outcome",
            statement=normalized,
            hard=False,
            assertion_basis="explicit",
            source_ref=draft_artifact_id,
        )
        hook = HookClueRecord(
            hook_id=f"hook-ch{chapter_no:04d}",
            label=f"chapter-{chapter_no}-hook",
            kind="hook",
            status="advanced",
            introduced_in=chapter_no,
            resolved_in=None,
            owner_entity_ids=[f"chapter:{chapter_no}"],
            source_fact_ids=[fact.fact_id],
            scene_id=None,
        )
        character = CharacterStateRecord(
            character_id=f"chapter-{chapter_no}-lead",
            display_name=f"Chapter {chapter_no} Lead",
            status_tags=["active"],
            current_location=f"chapter-{chapter_no}",
            relationship_refs=[],
            known_fact_ids=[fact.fact_id],
            last_updated_chapter=chapter_no,
            source_ref=draft_artifact_id,
        )
        conflicts: list[TruthConflictRecord] = []
        if "truth_conflict" in normalized.lower():
            conflicts.append(
                TruthConflictRecord(
                    conflict_id=f"conflict-ch{chapter_no:04d}-1",
                    category="canon_conflict",
                    severity="blocking",
                    message="draft introduces a truth conflict marker",
                    source_ref=draft_artifact_id,
                )
            )
        return TruthDeltaRecord(
            delta_id=f"truth-delta-{chapter_no:04d}-{uuid4().hex[:8]}",
            chapter_no=chapter_no,
            settlement_artifact_id=settlement_artifact_id,
            draft_artifact_id=draft_artifact_id,
            base_snapshot_id=base_snapshot_id,
            proposed_fact_additions=[fact],
            proposed_fact_updates=[],
            proposed_hook_updates=[hook],
            proposed_character_updates=[character],
            proposed_relationship_updates=[],
            chapter_irreversible_fact_ids=[],
            conflicts=conflicts,
            status="reconciled" if not conflicts else "rejected",
            notes=notes,
        )

    def _truth_delta_from_extracted(
        self,
        *,
        chapter_no: int,
        settlement_artifact_id: str,
        draft_artifact_id: str,
        base_snapshot_id: str,
        extracted: dict,
        truth_snapshot: dict,
    ) -> TruthDeltaRecord:
        def _normalize_fact_payload(raw: object, index: int, *, source_ref: str) -> CanonFactRecord:
            payload = raw if isinstance(raw, dict) else {}
            fact_id = payload.get("fact_id") or f"fact-ch{chapter_no:04d}-{index}"
            basis_raw = payload.get("assertion_basis", "explicit")
            try:
                basis = FactAssertionBasis(basis_raw)
            except ValueError:
                basis = FactAssertionBasis.EXPLICIT
            return CanonFactRecord(
                fact_id=fact_id,
                category=payload.get("category", "chapter_outcome"),
                statement=str(payload.get("statement", "")).strip() or f"第{chapter_no}章事实{index}",
                hard=bool(payload.get("hard", False)),
                assertion_basis=basis,
                source_ref=source_ref,
            )

        existing_hard_facts = {
            fact["fact_id"]: fact
            for fact in truth_snapshot["canon"].get("facts", [])
            if fact.get("fact_id") and fact.get("hard")
        }
        fact_additions: list[CanonFactRecord] = []
        fact_updates: list[CanonFactRecord] = []
        character_updates: list[CharacterStateRecord] = []
        relationship_updates: list = []
        hook_updates: list[HookClueRecord] = []
        conflicts: list[TruthConflictRecord] = []

        for index, fact_payload in enumerate(extracted.get("fact_assertions", []), start=1):
            normalized_fact = _normalize_fact_payload(fact_payload, index, source_ref=draft_artifact_id)
            target_collection = fact_updates if normalized_fact.fact_id in existing_hard_facts else fact_additions
            target_collection.append(normalized_fact)

        for index, fact_payload in enumerate(extracted.get("proposed_fact_updates", []), start=1):
            normalized_fact = _normalize_fact_payload(
                fact_payload,
                index + len(fact_additions),
                source_ref=draft_artifact_id,
            )
            fact_updates.append(normalized_fact)

        for update in extracted.get("character_updates", []):
            character_id = update.get("character_id") or f"character-ch{chapter_no:04d}-{len(character_updates) + 1}"
            previous = {
                item["character_id"]: item
                for item in truth_snapshot["characters"].get("characters", [])
                if item.get("character_id")
            }.get(character_id, {})
            merged_known = list(dict.fromkeys([*(previous.get("known_fact_ids", []) or []), *(update.get("known_fact_ids", []) or [])]))
            character_updates.append(
                CharacterStateRecord(
                    character_id=character_id,
                    display_name=update.get("display_name") or previous.get("display_name") or character_id,
                    status_tags=list(dict.fromkeys(update.get("status_tags", []) or previous.get("status_tags", []) or [])),
                    current_location=update.get("current_location", previous.get("current_location")),
                    relationship_refs=list(dict.fromkeys(update.get("relationship_refs", []) or previous.get("relationship_refs", []) or [])),
                    known_fact_ids=merged_known,
                    last_updated_chapter=chapter_no,
                    source_ref=draft_artifact_id,
                )
            )

        for index, relationship in enumerate(extracted.get("relationship_updates", []), start=1):
            relationship_updates.append(
                {
                    "edge_id": relationship.get("edge_id") or f"rel-ch{chapter_no:04d}-{index}",
                    "source_character_id": relationship.get("source_character_id", ""),
                    "target_character_id": relationship.get("target_character_id", ""),
                    "relation_type": relationship.get("relation_type", "unknown"),
                    "status": relationship.get("status", "active"),
                    "source_ref": draft_artifact_id,
                }
            )
        for index, hook_payload in enumerate(extracted.get("hook_updates", []), start=1):
            hook_id = hook_payload.get("hook_id") or f"hook-ch{chapter_no:04d}-{index}"
            previous = {
                item["hook_id"]: item
                for item in truth_snapshot["hook_ledger"].get("hooks", [])
                if item.get("hook_id")
            }.get(hook_id, {})
            new_status = hook_payload.get("status", previous.get("status", "open"))
            hook_updates.append(
                HookClueRecord(
                    hook_id=hook_id,
                    label=hook_payload.get("label") or previous.get("label") or hook_id,
                    kind=hook_payload.get("kind", previous.get("kind", "hook")),
                    status=new_status,
                    introduced_in=int(hook_payload.get("introduced_in", previous.get("introduced_in", chapter_no))),
                    resolved_in=hook_payload.get("resolved_in", previous.get("resolved_in")),
                    owner_entity_ids=hook_payload.get("owner_entity_ids", previous.get("owner_entity_ids", [])) or [],
                    source_fact_ids=hook_payload.get("source_fact_ids", previous.get("source_fact_ids", [])) or [],
                    scene_id=hook_payload.get("scene_id", previous.get("scene_id")),
                )
            )

        irreversible_fact_ids = list(dict.fromkeys(extracted.get("chapter_irreversible_facts", [])))
        conflicts = detect_truth_conflicts(
            chapter_no=chapter_no,
            draft_artifact_id=draft_artifact_id,
            fact_additions=fact_additions,
            fact_updates=fact_updates,
            character_updates=character_updates,
            hook_updates=hook_updates,
            irreversible_fact_ids=irreversible_fact_ids,
            truth_snapshot=truth_snapshot,
        )

        return TruthDeltaRecord(
            delta_id=f"truth-delta-{chapter_no:04d}-{uuid4().hex[:8]}",
            chapter_no=chapter_no,
            settlement_artifact_id=settlement_artifact_id,
            draft_artifact_id=draft_artifact_id,
            base_snapshot_id=base_snapshot_id,
            proposed_fact_additions=fact_additions,
            proposed_fact_updates=fact_updates,
            proposed_hook_updates=hook_updates,
            proposed_character_updates=character_updates,
            proposed_relationship_updates=relationship_updates,
            chapter_irreversible_fact_ids=irreversible_fact_ids,
            conflicts=conflicts,
            status="reconciled" if not conflicts else "rejected",
            notes=list(extracted.get("notes", [])),
        )

    def _commit_truth_from_chapter(
        self,
        book_id: str,
        chapter_no: int,
        status: ChapterStatusRecord,
        run_id: str,
    ) -> str:
        truth_delta_id = status.current_artifact_refs.get("truth_delta")
        if not truth_delta_id:
            raise ValueError("approve_chapter requires truth_delta artifact")
        truth_delta = TruthDeltaRecord.model_validate(self.load_artifact_payload(book_id, truth_delta_id))
        truth_index = self.repo.json.load_truth_index(book_id)
        if truth_delta.base_snapshot_id != truth_index.current_snapshot_id:
            raise ValueError("truth delta is stale against current truth head")
        base_snapshot_payloads = self.repo.json.load_truth_snapshot_payloads(book_id, truth_index.current_snapshot_id)
        snapshot_id = f"snapshot-{chapter_no:04d}"
        payloads = apply_truth_delta_to_snapshot_payloads(
            base_snapshot_payloads=base_snapshot_payloads,
            truth_delta=truth_delta,
            chapter_no=chapter_no,
            snapshot_id=snapshot_id,
        )
        self.repo.json.write_truth_payloads(
            book_id,
            canon=payloads["canon"],
            characters=payloads["characters"],
            hook_ledger=payloads["hook_ledger"],
            chapter_facts=payloads["chapter_facts"],
        )
        self.repo.json.save_truth_snapshot_bundle(
            book_id,
            snapshot_id=snapshot_id,
            snapshot_payload=build_truth_snapshot_payload(
                snapshot_id=snapshot_id,
                base_snapshot_id=truth_index.current_snapshot_id,
                chapter_no=chapter_no,
                run_id=run_id,
            ),
            canon=payloads["canon"],
            characters=payloads["characters"],
            hook_ledger=payloads["hook_ledger"],
            chapter_facts=payloads["chapter_facts"],
        )
        updated_index = TruthIndexRecord(
            current_snapshot_id=snapshot_id,
            committed_through_chapter=chapter_no,
            latest_projection_version=truth_index.latest_projection_version,
            latest_truth_commit_run_id=run_id,
        )
        self.repo.json.save_truth_index(book_id, updated_index)
        self.repo.json.write_truth_projection(book_id)
        receipt = build_truth_commit_receipt(
            chapter_no=chapter_no,
            truth_delta=truth_delta,
            truth_delta_artifact_id=truth_delta_id,
            snapshot_id=snapshot_id,
        )
        receipt = receipt.model_copy(update={"receipt_id": f"truth-commit-{chapter_no:04d}-{uuid4().hex[:8]}"})
        receipt_id = self.register_artifact(
            book_id,
            chapter_no,
            "truth_commit_receipt",
            receipt.model_dump(mode="json"),
            run_id,
        )
        self.repo._read_model(book_id).upsert_truth_head(
            book_id=book_id,
            current_snapshot_id=updated_index.current_snapshot_id,
            committed_through_chapter=updated_index.committed_through_chapter,
            latest_truth_commit_run_id=updated_index.latest_truth_commit_run_id,
            latest_projection_version=updated_index.latest_projection_version,
        )
        self.repo._read_model(book_id).upsert_truth_delta(
            delta_id=truth_delta.delta_id,
            book_id=book_id,
            chapter_no=truth_delta.chapter_no,
            base_snapshot_id=truth_delta.base_snapshot_id,
            status="committed",
            conflict_count=len(truth_delta.conflicts),
        )
        return receipt_id
