from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
from typing import Any

from engine.schemas.artifact import (
    ArtifactPayloadRef,
    ArtifactRecord,
    BookExportProfileRecord,
    TruthIndexRecord,
)
from engine.schemas.batch import BatchCheckpointRecord, BatchItemRecord, BatchRunRecord
from engine.schemas.book import BookIndexRecord, BookRecord
from engine.schemas.chapter import ChapterStatusRecord
from engine.schemas.run import RunRecord


class JsonStateStore:
    """Authoritative JSON state store."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.books_root = self.root / "books"

    def ensure_book_dirs(self, book_id: str) -> dict[str, Path]:
        book_root = self.books_root / book_id
        paths = {
            "book_root": book_root,
            "story": book_root / "story",
            "chapters": book_root / "chapters",
            "exports": book_root / "exports",
            "state": book_root / "state",
            "batches": book_root / "state" / "batches",
            "batch_runs": book_root / "state" / "batches" / "runs",
            "batch_items": book_root / "state" / "batches" / "items",
            "truth": book_root / "state" / "truth",
            "truth_snapshots": book_root / "state" / "truth" / "snapshots",
            "truth_deltas": book_root / "state" / "truth" / "deltas",
            "artifacts": book_root / "artifacts",
            "runtime": book_root / "runtime",
        }
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        return paths

    def ensure_book_state_initialized(self, record: BookRecord) -> None:
        paths = self.ensure_book_dirs(record.book_id)
        self.write_json(paths["book_root"] / "book.json", record.model_dump(mode="json"))
        self.write_json(paths["state"] / "book_state.json", record.model_dump(mode="json"))
        if not (paths["state"] / "chapter_index.json").exists():
            self.write_json(
                paths["state"] / "chapter_index.json",
                BookIndexRecord(book_id=record.book_id).model_dump(mode="json"),
            )
        if not (paths["state"] / "artifacts_manifest.json").exists():
            self.write_json(paths["state"] / "artifacts_manifest.json", {"artifacts": []})
        if not (paths["state"] / "runs_manifest.json").exists():
            self.write_json(paths["state"] / "runs_manifest.json", {"runs": []})
        if not (paths["state"] / "invalidation_log.json").exists():
            self.write_json(paths["state"] / "invalidation_log.json", {"entries": []})
        if not (paths["state"] / "chapter_notes.json").exists():
            self.write_json(paths["state"] / "chapter_notes.json", {"chapters": {}})
        if not (paths["state"] / "export_profile.json").exists():
            self.write_json(
                paths["state"] / "export_profile.json",
                BookExportProfileRecord().model_dump(mode="json"),
            )
        if not (paths["state"] / "export_index.json").exists():
            self.write_json(paths["state"] / "export_index.json", {"entries": {}})
        if not (paths["batches"] / "index.json").exists():
            self.write_json(paths["batches"] / "index.json", {"runs": []})
        if not (paths["batches"] / "checkpoints.json").exists():
            self.write_json(paths["batches"] / "checkpoints.json", {"checkpoints": []})
        if not (paths["batches"] / "pause_log.json").exists():
            self.write_json(paths["batches"] / "pause_log.json", {"events": []})
        self.ensure_truth_initialized(record.book_id)

    def ensure_truth_initialized(self, book_id: str) -> None:
        paths = self.ensure_book_dirs(book_id)
        truth_dir = paths["truth"]
        truth_index_path = truth_dir / "truth_index.json"
        if truth_index_path.exists():
            return
        bootstrap_run_id = "truth-bootstrap"
        snapshot_id = "snapshot-0000"
        self.write_json(truth_dir / "canon.json", {"facts": []})
        self.write_json(truth_dir / "characters.json", {"characters": [], "relationships": []})
        self.write_json(truth_dir / "hook_ledger.json", {"hooks": []})
        self.write_json(truth_dir / "chapter_facts.json", {"chapters": []})
        self.write_json(truth_dir / "invalidations.json", {"entries": []})
        self.write_json(truth_dir / "propagation_debt.json", {"entries": []})
        self.save_truth_snapshot_bundle(
            book_id,
            snapshot_id=snapshot_id,
            snapshot_payload={
                "snapshot_id": snapshot_id,
                "base_snapshot_id": None,
                "committed_through_chapter": 0,
                "created_by_run_id": bootstrap_run_id,
            },
            canon={"facts": []},
            characters={"characters": [], "relationships": []},
            hook_ledger={"hooks": []},
            chapter_facts={"chapters": []},
        )
        self.write_json(
            truth_index_path,
            TruthIndexRecord(
                current_snapshot_id=snapshot_id,
                committed_through_chapter=0,
                latest_projection_version=1,
                latest_truth_commit_run_id=bootstrap_run_id,
            ).model_dump(mode="json"),
        )
        self._write_truth_markdown_projection(book_id)

    def save_book_record(self, record: BookRecord) -> None:
        paths = self.ensure_book_dirs(record.book_id)
        self.write_json(paths["book_root"] / "book.json", record.model_dump(mode="json"))
        self.write_json(paths["state"] / "book_state.json", record.model_dump(mode="json"))

    def load_book_record(self, book_id: str) -> BookRecord:
        path = self.ensure_book_dirs(book_id)["state"] / "book_state.json"
        return BookRecord.model_validate(self.read_json(path))

    def save_chapter_status(self, record: ChapterStatusRecord) -> None:
        paths = self.ensure_book_dirs(record.book_id)
        chapter_path = paths["state"] / f"chapter-{record.chapter_no:04d}.json"
        self.write_json(chapter_path, record.model_dump(mode="json"))
        index = self.load_book_index(record.book_id)
        if record.chapter_no not in index.chapters:
            index.chapters.append(record.chapter_no)
            index.chapters.sort()
        self.write_json(
            paths["state"] / "chapter_index.json",
            index.model_copy(update={"updated_at": datetime.now(timezone.utc)}).model_dump(mode="json"),
        )

    def load_chapter_status(self, book_id: str, chapter_no: int) -> ChapterStatusRecord:
        path = self.ensure_book_dirs(book_id)["state"] / f"chapter-{chapter_no:04d}.json"
        return ChapterStatusRecord.model_validate(self.read_json(path))

    def load_book_index(self, book_id: str) -> BookIndexRecord:
        path = self.ensure_book_dirs(book_id)["state"] / "chapter_index.json"
        return BookIndexRecord.model_validate(self.read_json(path))

    def save_run_record(self, record: RunRecord) -> Path:
        paths = self.ensure_book_dirs(record.book_id)
        chapter_runtime_dir = paths["runtime"] / f"chapter-{record.chapter_no:04d}"
        chapter_runtime_dir.mkdir(parents=True, exist_ok=True)
        path = chapter_runtime_dir / f"{record.run_id}.json"
        self.write_json(path, record.model_dump(mode="json"))
        manifest_path = paths["state"] / "runs_manifest.json"
        manifest = self.read_json(manifest_path)
        runs = [item for item in manifest.get("runs", []) if item["run_id"] != record.run_id]
        runs.append(
            {
                "run_id": record.run_id,
                "chapter_no": record.chapter_no,
                "relative_path": str(path.relative_to(paths["book_root"])).replace("\\", "/"),
                "status": record.status.value,
            }
        )
        runs.sort(key=lambda item: (item["chapter_no"], item["run_id"]))
        self.write_json(manifest_path, {"runs": runs})
        return path

    def save_batch_run_record(self, record: BatchRunRecord) -> Path:
        paths = self.ensure_book_dirs(record.book_id)
        path = paths["batch_runs"] / f"{record.batch_run_id}.json"
        self.write_json(path, record.model_dump(mode="json"))
        manifest_path = paths["batches"] / "index.json"
        manifest = self.read_json(manifest_path)
        runs = [item for item in manifest.get("runs", []) if item["batch_run_id"] != record.batch_run_id]
        runs.append(
            {
                "batch_run_id": record.batch_run_id,
                "relative_path": str(path.relative_to(paths["book_root"])).replace("\\", "/"),
                "status": record.status.value,
                "batch_mode": record.batch_mode.value,
            }
        )
        runs.sort(key=lambda item: item["batch_run_id"])
        self.write_json(manifest_path, {"runs": runs})
        return path

    def load_batch_run_record(self, book_id: str, batch_run_id: str) -> BatchRunRecord:
        path = self.ensure_book_dirs(book_id)["batch_runs"] / f"{batch_run_id}.json"
        return BatchRunRecord.model_validate(self.read_json(path))

    def list_batch_runs(self, book_id: str) -> list[BatchRunRecord]:
        manifest = self.read_json(self.ensure_book_dirs(book_id)["batches"] / "index.json")
        return [
            self.load_batch_run_record(book_id, item["batch_run_id"])
            for item in manifest.get("runs", [])
        ]

    def save_batch_items(self, book_id: str, batch_run_id: str, items: list[BatchItemRecord]) -> Path:
        path = self.ensure_book_dirs(book_id)["batch_items"] / f"{batch_run_id}.json"
        self.write_json(path, {"items": [item.model_dump(mode="json") for item in items]})
        return path

    def load_batch_items(self, book_id: str, batch_run_id: str) -> list[BatchItemRecord]:
        path = self.ensure_book_dirs(book_id)["batch_items"] / f"{batch_run_id}.json"
        payload = self.read_json(path)
        return [BatchItemRecord.model_validate(item) for item in payload.get("items", [])]

    def append_batch_checkpoint(self, book_id: str, checkpoint: BatchCheckpointRecord) -> None:
        path = self.ensure_book_dirs(book_id)["batches"] / "checkpoints.json"
        payload = self.read_json(path)
        checkpoints = [
            item
            for item in payload.get("checkpoints", [])
            if item.get("checkpoint_id") != checkpoint.checkpoint_id
        ]
        checkpoints.append(checkpoint.model_dump(mode="json"))
        checkpoints.sort(key=lambda item: (item["batch_run_id"], item["created_at"]))
        self.write_json(path, {"checkpoints": checkpoints})

    def list_batch_checkpoints(self, book_id: str, batch_run_id: str | None = None) -> list[BatchCheckpointRecord]:
        payload = self.read_json(self.ensure_book_dirs(book_id)["batches"] / "checkpoints.json")
        checkpoints = [BatchCheckpointRecord.model_validate(item) for item in payload.get("checkpoints", [])]
        if batch_run_id is not None:
            checkpoints = [item for item in checkpoints if item.batch_run_id == batch_run_id]
        return checkpoints

    def append_batch_pause_event(self, book_id: str, payload: dict[str, Any]) -> None:
        path = self.ensure_book_dirs(book_id)["batches"] / "pause_log.json"
        log = self.read_json(path)
        events = log.get("events", [])
        events.append(payload)
        self.write_json(path, {"events": events})

    def load_batch_pause_events(self, book_id: str) -> list[dict[str, Any]]:
        path = self.ensure_book_dirs(book_id)["batches"] / "pause_log.json"
        return self.read_json(path).get("events", [])

    def load_run_record(self, book_id: str, run_id: str, chapter_no: int) -> RunRecord:
        path = (
            self.ensure_book_dirs(book_id)["runtime"]
            / f"chapter-{chapter_no:04d}"
            / f"{run_id}.json"
        )
        return RunRecord.model_validate(self.read_json(path))

    def register_artifact(
        self,
        book_id: str,
        chapter_no: int,
        artifact_type: str,
        run_id: str,
        payload: dict[str, Any],
        artifact_id: str,
    ) -> ArtifactRecord:
        paths = self.ensure_book_dirs(book_id)
        chapter_artifact_dir = paths["artifacts"] / f"chapter-{chapter_no:04d}"
        chapter_artifact_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{artifact_id}.json"
        payload_path = chapter_artifact_dir / filename
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=self._json_default)
        payload_path.write_text(content, encoding="utf-8")
        payload_ref = ArtifactPayloadRef(
            relative_path=str(payload_path.relative_to(paths["book_root"])).replace("\\", "/")
        )
        record = ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            book_id=book_id,
            chapter_no=chapter_no,
            produced_by_run_id=run_id,
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            payload_ref=payload_ref,
        )
        manifest_path = paths["state"] / "artifacts_manifest.json"
        manifest = self.read_json(manifest_path)
        artifacts = [item for item in manifest.get("artifacts", []) if item.get("artifact_id") != record.artifact_id]
        artifacts.append(record.model_dump(mode="json"))
        self.write_json(manifest_path, {"artifacts": artifacts})
        return record

    def list_artifacts(self, book_id: str) -> list[ArtifactRecord]:
        manifest_path = self.ensure_book_dirs(book_id)["state"] / "artifacts_manifest.json"
        manifest = self.read_json(manifest_path)
        return [ArtifactRecord.model_validate(item) for item in manifest.get("artifacts", [])]

    def append_invalidation_entry(self, book_id: str, payload: dict[str, Any]) -> None:
        path = self.ensure_book_dirs(book_id)["state"] / "invalidation_log.json"
        log = self.read_json(path)
        entries = log.get("entries", [])
        entries.append(payload)
        self.write_json(path, {"entries": entries})

    def append_chapter_note(self, book_id: str, chapter_no: int, payload: dict[str, Any]) -> None:
        path = self.ensure_book_dirs(book_id)["state"] / "chapter_notes.json"
        notes = self.read_json(path)
        chapters = notes.get("chapters", {})
        chapter_key = f"{chapter_no:04d}"
        current = chapters.get(chapter_key, {})
        current.update(payload)
        chapters[chapter_key] = current
        self.write_json(path, {"chapters": chapters})

    def load_chapter_note(self, book_id: str, chapter_no: int) -> dict[str, Any]:
        path = self.ensure_book_dirs(book_id)["state"] / "chapter_notes.json"
        notes = self.read_json(path)
        return notes.get("chapters", {}).get(f"{chapter_no:04d}", {})

    def find_artifact(self, book_id: str, artifact_id: str) -> ArtifactRecord:
        for artifact in self.list_artifacts(book_id):
            if artifact.artifact_id == artifact_id:
                return artifact
        raise FileNotFoundError(f"artifact not found: {artifact_id}")

    def load_truth_index(self, book_id: str) -> TruthIndexRecord:
        path = self.ensure_book_dirs(book_id)["truth"] / "truth_index.json"
        return TruthIndexRecord.model_validate(self.read_json(path))

    def save_truth_index(self, book_id: str, payload: TruthIndexRecord) -> None:
        path = self.ensure_book_dirs(book_id)["truth"] / "truth_index.json"
        self.write_json(path, payload.model_dump(mode="json"))

    def load_truth_snapshot(self, book_id: str, snapshot_id: str) -> dict[str, Any]:
        path = self.ensure_book_dirs(book_id)["truth_snapshots"] / snapshot_id / "snapshot.json"
        return self.read_json(path)

    def save_truth_snapshot(self, book_id: str, snapshot_id: str, payload: dict[str, Any]) -> None:
        path = self.ensure_book_dirs(book_id)["truth_snapshots"] / snapshot_id / "snapshot.json"
        self.write_json(path, payload)

    def save_truth_snapshot_bundle(
        self,
        book_id: str,
        *,
        snapshot_id: str,
        snapshot_payload: dict[str, Any],
        canon: dict[str, Any],
        characters: dict[str, Any],
        hook_ledger: dict[str, Any],
        chapter_facts: dict[str, Any],
    ) -> None:
        paths = self.ensure_book_dirs(book_id)
        snapshot_dir = paths["truth_snapshots"] / snapshot_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        canon_path = snapshot_dir / "canon.json"
        character_path = snapshot_dir / "characters.json"
        hook_path = snapshot_dir / "hook_ledger.json"
        chapter_fact_path = snapshot_dir / "chapter_facts.json"
        self.write_json(canon_path, canon)
        self.write_json(character_path, characters)
        self.write_json(hook_path, hook_ledger)
        self.write_json(chapter_fact_path, chapter_facts)
        payload = dict(snapshot_payload)
        payload["canon_ref"] = str(canon_path.relative_to(paths["book_root"])).replace("\\", "/")
        payload["character_ref"] = str(character_path.relative_to(paths["book_root"])).replace("\\", "/")
        payload["hook_ref"] = str(hook_path.relative_to(paths["book_root"])).replace("\\", "/")
        payload["chapter_fact_ref"] = str(chapter_fact_path.relative_to(paths["book_root"])).replace("\\", "/")
        self.write_json(snapshot_dir / "snapshot.json", payload)

    def load_truth_snapshot_payloads(self, book_id: str, snapshot_id: str) -> dict[str, Any]:
        snapshot = self.load_truth_snapshot(book_id, snapshot_id)
        book_root = self.ensure_book_dirs(book_id)["book_root"]
        return {
            "snapshot": snapshot,
            "canon": self.read_json(book_root / snapshot["canon_ref"]),
            "characters": self.read_json(book_root / snapshot["character_ref"]),
            "hook_ledger": self.read_json(book_root / snapshot["hook_ref"]),
            "chapter_facts": self.read_json(book_root / snapshot["chapter_fact_ref"]),
        }

    def save_truth_delta(self, book_id: str, delta_id: str, payload: dict[str, Any]) -> Path:
        path = self.ensure_book_dirs(book_id)["truth_deltas"] / f"{delta_id}.json"
        self.write_json(path, payload)
        return path

    def load_truth_delta(self, book_id: str, delta_id: str) -> dict[str, Any]:
        path = self.ensure_book_dirs(book_id)["truth_deltas"] / f"{delta_id}.json"
        return self.read_json(path)

    def append_truth_invalidation(self, book_id: str, payload: dict[str, Any]) -> None:
        path = self.ensure_book_dirs(book_id)["truth"] / "invalidations.json"
        data = self.read_json(path)
        entries = data.get("entries", [])
        entries.append(payload)
        self.write_json(path, {"entries": entries})

    def append_propagation_debt(self, book_id: str, payload: dict[str, Any]) -> None:
        path = self.ensure_book_dirs(book_id)["truth"] / "propagation_debt.json"
        data = self.read_json(path)
        entries = [entry for entry in data.get("entries", []) if entry.get("debt_id") != payload.get("debt_id")]
        entries.append(payload)
        self.write_json(path, {"entries": entries})

    def load_propagation_debts(self, book_id: str) -> list[dict[str, Any]]:
        path = self.ensure_book_dirs(book_id)["truth"] / "propagation_debt.json"
        data = self.read_json(path)
        return data.get("entries", [])

    def resolve_propagation_debt(self, book_id: str, debt_id: str, resolved_at: str) -> None:
        path = self.ensure_book_dirs(book_id)["truth"] / "propagation_debt.json"
        data = self.read_json(path)
        entries = []
        for entry in data.get("entries", []):
            if entry.get("debt_id") == debt_id:
                updated = dict(entry)
                updated["status"] = "resolved"
                updated["resolved_at"] = resolved_at
                entries.append(updated)
            else:
                entries.append(entry)
        self.write_json(path, {"entries": entries})

    def load_truth_payloads(self, book_id: str) -> dict[str, Any]:
        truth_dir = self.ensure_book_dirs(book_id)["truth"]
        return {
            "truth_index": self.read_json(truth_dir / "truth_index.json"),
            "canon": self.read_json(truth_dir / "canon.json"),
            "characters": self.read_json(truth_dir / "characters.json"),
            "hook_ledger": self.read_json(truth_dir / "hook_ledger.json"),
            "chapter_facts": self.read_json(truth_dir / "chapter_facts.json"),
            "invalidations": self.read_json(truth_dir / "invalidations.json"),
            "propagation_debt": self.read_json(truth_dir / "propagation_debt.json"),
        }

    def write_truth_payloads(
        self,
        book_id: str,
        *,
        canon: dict[str, Any],
        characters: dict[str, Any],
        hook_ledger: dict[str, Any],
        chapter_facts: dict[str, Any],
    ) -> None:
        truth_dir = self.ensure_book_dirs(book_id)["truth"]
        self.write_json(truth_dir / "canon.json", canon)
        self.write_json(truth_dir / "characters.json", characters)
        self.write_json(truth_dir / "hook_ledger.json", hook_ledger)
        self.write_json(truth_dir / "chapter_facts.json", chapter_facts)

    def write_truth_projection(self, book_id: str) -> None:
        self._write_truth_markdown_projection(book_id)

    def load_export_profile(self, book_id: str) -> BookExportProfileRecord:
        path = self.ensure_book_dirs(book_id)["state"] / "export_profile.json"
        return BookExportProfileRecord.model_validate(self.read_json(path))

    def save_export_profile(self, book_id: str, payload: BookExportProfileRecord) -> None:
        path = self.ensure_book_dirs(book_id)["state"] / "export_profile.json"
        self.write_json(path, payload.model_dump(mode="json"))

    def update_export_index(self, book_id: str, chapter_no: int, platform: str, payload: dict[str, Any]) -> None:
        path = self.ensure_book_dirs(book_id)["state"] / "export_index.json"
        data = self.read_json(path)
        entries = data.get("entries", {})
        entries[f"{chapter_no:04d}:{platform}"] = payload
        self.write_json(path, {"entries": entries})

    def load_export_index(self, book_id: str) -> dict[str, Any]:
        path = self.ensure_book_dirs(book_id)["state"] / "export_index.json"
        return self.read_json(path)

    def build_export_dir(self, book_id: str, platform: str, chapter_no: int, version: int) -> Path:
        export_dir = self.ensure_book_dirs(book_id)["exports"] / platform / f"chapter-{chapter_no:04d}" / f"v{version:03d}"
        export_dir.mkdir(parents=True, exist_ok=True)
        return export_dir

    def _write_truth_markdown_projection(self, book_id: str) -> None:
        paths = self.ensure_book_dirs(book_id)
        truth = self.load_truth_payloads(book_id)
        canon_lines = ["# Canon", ""]
        for fact in truth["canon"].get("facts", []):
            canon_lines.append(f"- [{fact['category']}] {fact['statement']}")
        (paths["story"] / "canon.md").write_text("\n".join(canon_lines) + "\n", encoding="utf-8")

        character_lines = ["# Character State", ""]
        for character in truth["characters"].get("characters", []):
            tags = ", ".join(character.get("status_tags", [])) or "none"
            location = character.get("current_location") or "unknown"
            character_lines.append(f"- {character['display_name']}: tags={tags}; location={location}")
        (paths["story"] / "character_state.md").write_text("\n".join(character_lines) + "\n", encoding="utf-8")

        hook_lines = ["# Hook / Clue Ledger", ""]
        for hook in truth["hook_ledger"].get("hooks", []):
            hook_lines.append(f"- {hook['hook_id']} [{hook['kind']}/{hook['status']}] {hook['label']}")
        (paths["story"] / "hook_clue_ledger.md").write_text("\n".join(hook_lines) + "\n", encoding="utf-8")

        fact_lines = ["# Chapter Facts", ""]
        for chapter in truth["chapter_facts"].get("chapters", []):
            fact_lines.append(
                f"- Chapter {chapter['chapter_no']}: facts={len(chapter.get('fact_ids', []))}; irreversible={len(chapter.get('irreversible_fact_ids', []))}"
            )
        (paths["story"] / "chapter_facts.md").write_text("\n".join(fact_lines) + "\n", encoding="utf-8")

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=self._json_default)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
            suffix=".tmp",
        ) as handle:
            handle.write(content)
            temp_name = handle.name
        os.replace(temp_name, path)

    def read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")

    @classmethod
    def filter_runtime_refs_for_reset(cls, refs: dict[str, str]) -> dict[str, str]:
        return {key: value for key, value in refs.items() if key in cls.STATIC_CHAPTER_REF_KEYS}
    STATIC_CHAPTER_REF_KEYS = {"plan"}
