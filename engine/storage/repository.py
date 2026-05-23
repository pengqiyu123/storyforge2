from __future__ import annotations

from pathlib import Path
import json

from engine.schemas.artifact import (
    AdversarialEditRecord,
    ArtifactRecord,
    ChapterQualityRecord,
    ExportManifestRecord,
    PropagationDebtRecord,
    ReaderPanelRecord,
    StyleSignalRecord,
    TruthDeltaRecord,
    TruthInvalidationRecord,
)
from engine.schemas.batch import BatchCheckpointRecord, BatchItemRecord, BatchRunRecord
from engine.schemas.book import BookRecord
from engine.schemas.chapter import ChapterStatusRecord
from engine.schemas.run import RunRecord
from engine.storage.json_state_store import JsonStateStore
from engine.storage.sqlite_read_model import SQLiteReadModelStore


class StoryForgeRepository:
    """Coordinates JSON canonical state with SQLite read model."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.json = JsonStateStore(self.root)

    def _read_model(self, book_id: str) -> SQLiteReadModelStore:
        db_path = self.json.ensure_book_dirs(book_id)["book_root"] / "memory.db"
        store = SQLiteReadModelStore(db_path)
        store.initialize()
        return store

    def create_book(self, record: BookRecord) -> None:
        self.json.ensure_book_state_initialized(record)
        read_model = self._read_model(record.book_id)
        read_model.upsert_book(record)
        truth_index = self.json.load_truth_index(record.book_id)
        read_model.upsert_truth_head(
            book_id=record.book_id,
            current_snapshot_id=truth_index.current_snapshot_id,
            committed_through_chapter=truth_index.committed_through_chapter,
            latest_truth_commit_run_id=truth_index.latest_truth_commit_run_id,
            latest_projection_version=truth_index.latest_projection_version,
        )

    def save_book(self, record: BookRecord) -> None:
        self.json.save_book_record(record)
        self._read_model(record.book_id).upsert_book(record)

    def load_book(self, book_id: str) -> BookRecord:
        return self.json.load_book_record(book_id)

    def save_chapter_status(self, record: ChapterStatusRecord) -> None:
        self.json.save_chapter_status(record)
        self._read_model(record.book_id).upsert_chapter_status(record)

    def load_chapter_status(self, book_id: str, chapter_no: int) -> ChapterStatusRecord:
        return self.json.load_chapter_status(book_id, chapter_no)

    def save_run(self, record: RunRecord) -> None:
        self.json.save_run_record(record)
        self._read_model(record.book_id).upsert_run(record)

    def save_artifact(self, record: ArtifactRecord) -> None:
        self._read_model(record.book_id).upsert_artifact(record)

    def save_batch_run(self, record: BatchRunRecord) -> None:
        self.json.save_batch_run_record(record)
        import json

        self._read_model(record.book_id).upsert_batch_run(
            batch_run_id=record.batch_run_id,
            book_id=record.book_id,
            batch_mode=record.batch_mode.value,
            status=record.status.value,
            chapter_range_json=json.dumps(record.chapter_range, ensure_ascii=False),
            current_phase=record.current_phase,
            frontier_chapter_no=record.frontier_chapter_no,
            pause_reason_codes_json=json.dumps(record.pause_reason_codes, ensure_ascii=False),
            last_checkpoint_id=record.last_checkpoint_id,
            total_items=record.total_items,
            completed_items=record.completed_items,
            failed_items=record.failed_items,
            started_at=record.started_at.isoformat() if record.started_at else None,
            finished_at=record.finished_at.isoformat() if record.finished_at else None,
        )

    def load_batch_run(self, book_id: str, batch_run_id: str) -> BatchRunRecord:
        return self.json.load_batch_run_record(book_id, batch_run_id)

    def list_batch_runs(self, book_id: str) -> list[BatchRunRecord]:
        return self.json.list_batch_runs(book_id)

    def save_batch_items(self, book_id: str, batch_run_id: str, items: list[BatchItemRecord]) -> None:
        import json

        self.json.save_batch_items(book_id, batch_run_id, items)
        read_model = self._read_model(book_id)
        for item in items:
            read_model.upsert_batch_item(
                item_id=item.item_id,
                batch_run_id=item.batch_run_id,
                book_id=book_id,
                chapter_no=item.chapter_no,
                phase=item.phase,
                attempt=item.attempt,
                status=item.status.value,
                depends_on_snapshot_id=item.depends_on_snapshot_id,
                depends_on_frontier=item.depends_on_frontier,
                run_id=item.run_id,
                output_refs_json=json.dumps(item.output_refs, ensure_ascii=False),
                error_summary=item.error_summary,
                updated_at=item.updated_at.isoformat(),
            )

    def load_batch_items(self, book_id: str, batch_run_id: str) -> list[BatchItemRecord]:
        return self.json.load_batch_items(book_id, batch_run_id)

    def append_batch_checkpoint(self, book_id: str, checkpoint: BatchCheckpointRecord) -> None:
        import json

        self.json.append_batch_checkpoint(book_id, checkpoint)
        self._read_model(book_id).upsert_batch_checkpoint(
            checkpoint_id=checkpoint.checkpoint_id,
            batch_run_id=checkpoint.batch_run_id,
            book_id=book_id,
            phase=checkpoint.phase,
            frontier_chapter_no=checkpoint.frontier_chapter_no,
            truth_head_snapshot_id=checkpoint.truth_head_snapshot_id,
            open_blockers_json=json.dumps(checkpoint.open_blockers, ensure_ascii=False),
            panel_summary_refs_json=json.dumps(checkpoint.panel_summary_refs, ensure_ascii=False),
            created_at=checkpoint.created_at.isoformat(),
        )

    def list_batch_checkpoints(self, book_id: str, batch_run_id: str | None = None) -> list[BatchCheckpointRecord]:
        return self.json.list_batch_checkpoints(book_id, batch_run_id)

    def append_batch_pause_event(self, book_id: str, payload: dict) -> None:
        self.json.append_batch_pause_event(book_id, payload)

    def save_reader_panel_signal(
        self,
        *,
        book_id: str,
        batch_run_id: str,
        artifact_id: str,
        payload: ReaderPanelRecord,
        created_at: str,
    ) -> None:
        import json

        self._read_model(book_id).upsert_reader_panel_signal(
            artifact_id=artifact_id,
            book_id=book_id,
            batch_run_id=batch_run_id,
            chapter_no=payload.chapter_no,
            panel_scope=payload.panel_scope,
            aggregate_recommendation=payload.aggregate_recommendation,
            risk_flags_json=json.dumps(payload.risk_flags, ensure_ascii=False),
            created_at=created_at,
        )

    def save_adversarial_edit_signal(
        self,
        *,
        book_id: str,
        artifact_id: str,
        payload: AdversarialEditRecord,
        created_at: str,
        chapter_no: int,
    ) -> None:
        import json

        self._read_model(book_id).upsert_adversarial_edit(
            artifact_id=artifact_id,
            book_id=book_id,
            chapter_no=chapter_no,
            source_settlement_artifact_id=payload.source_settlement_artifact_id,
            candidate_settlement_artifact_id=payload.candidate_settlement_artifact_id,
            kept=payload.kept,
            decision_reason_codes_json=json.dumps(payload.decision_reason_codes, ensure_ascii=False),
            created_at=created_at,
        )

    def save_style_signal(
        self,
        *,
        book_id: str,
        batch_run_id: str | None,
        artifact_id: str,
        payload: StyleSignalRecord,
        created_at: str,
    ) -> None:
        import json

        self._read_model(book_id).upsert_style_signal(
            artifact_id=artifact_id,
            book_id=book_id,
            batch_run_id=batch_run_id,
            chapter_no=payload.chapter_no,
            drift_score=payload.drift_score,
            dominant_drift_axes_json=json.dumps(payload.dominant_drift_axes, ensure_ascii=False),
            recommended_action=payload.recommended_action,
            created_at=created_at,
        )

    def rebuild_read_model(self, book_id: str) -> None:
        read_model = self._read_model(book_id)
        book = self.json.load_book_record(book_id)
        read_model.initialize()
        read_model.upsert_book(book)
        truth_index = self.json.load_truth_index(book_id)
        read_model.upsert_truth_head(
            book_id=book_id,
            current_snapshot_id=truth_index.current_snapshot_id,
            committed_through_chapter=truth_index.committed_through_chapter,
            latest_truth_commit_run_id=truth_index.latest_truth_commit_run_id,
            latest_projection_version=truth_index.latest_projection_version,
        )
        index = self.json.load_book_index(book_id)
        for chapter_no in index.chapters:
            read_model.upsert_chapter_status(self.json.load_chapter_status(book_id, chapter_no))
        for artifact in self.json.list_artifacts(book_id):
            read_model.upsert_artifact(artifact)
            if artifact.artifact_type.value == "chapter_quality":
                book_root = self.json.ensure_book_dirs(book_id)["book_root"]
                payload = self.json.read_json(book_root / artifact.payload_ref.relative_path)
                quality = ChapterQualityRecord.model_validate(payload)
                read_model.upsert_chapter_quality(
                    book_id=book_id,
                    chapter_no=quality.chapter_no,
                    gate_decision_artifact_id=quality.gate_decision_artifact_id,
                    settlement_artifact_id=quality.settlement_artifact_id,
                    overall_score=quality.overall_score,
                    critical_count=quality.critical_count,
                    blocked_by_mechanical=quality.blocked_by_mechanical,
                    blocking_rule_ids_json=json.dumps(quality.blocking_rule_ids, ensure_ascii=False),
                    reason_codes_json=json.dumps(quality.reason_codes, ensure_ascii=False),
                    decision_status=quality.decision_status,
                    created_at=artifact.created_at.isoformat(),
                )
            if artifact.artifact_type.value == "truth_delta":
                book_root = self.json.ensure_book_dirs(book_id)["book_root"]
                payload = self.json.read_json(book_root / artifact.payload_ref.relative_path)
                delta = TruthDeltaRecord.model_validate(payload)
                read_model.upsert_truth_delta(
                    delta_id=delta.delta_id,
                    book_id=book_id,
                    chapter_no=delta.chapter_no,
                    base_snapshot_id=delta.base_snapshot_id,
                    status=delta.status,
                    conflict_count=len(delta.conflicts),
                )
            if artifact.artifact_type.value == "propagation_debt":
                book_root = self.json.ensure_book_dirs(book_id)["book_root"]
                payload = self.json.read_json(book_root / artifact.payload_ref.relative_path)
                debt = PropagationDebtRecord.model_validate(payload)
                read_model.upsert_propagation_debt(
                    debt_id=debt.debt_id,
                    book_id=book_id,
                    chapter_no=debt.chapter_no,
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
            if artifact.artifact_type.value == "truth_invalidation":
                book_root = self.json.ensure_book_dirs(book_id)["book_root"]
                payload = self.json.read_json(book_root / artifact.payload_ref.relative_path)
                invalidation = TruthInvalidationRecord.model_validate(payload)
                read_model.upsert_truth_invalidation(
                    debt_id=invalidation.debt_id,
                    book_id=book_id,
                    chapter_no=invalidation.chapter_no,
                    trigger_chapter_no=invalidation.trigger_chapter_no,
                    stale_snapshot_id=invalidation.stale_snapshot_id,
                    current_snapshot_id=invalidation.current_snapshot_id,
                    source_truth_commit_receipt_id=invalidation.source_truth_commit_receipt_id,
                    created_at=artifact.created_at.isoformat(),
                )
            if artifact.artifact_type.value == "export_manifest":
                book_root = self.json.ensure_book_dirs(book_id)["book_root"]
                payload = self.json.read_json(book_root / artifact.payload_ref.relative_path)
                manifest = ExportManifestRecord.model_validate(payload)
                read_model.upsert_chapter_export(
                    book_id=book_id,
                    chapter_no=manifest.chapter_no,
                    platform=manifest.platform,
                    latest_manifest_id=artifact.artifact_id,
                    version=manifest.version,
                    chapter_file_sha256=manifest.chapter_file_sha256,
                    chapter_semantic_sha256=manifest.chapter_semantic_sha256,
                    integrity_status=manifest.integrity_status,
                    exported_at=manifest.exported_at.isoformat(),
                )
            if artifact.artifact_type.value == "reader_panel":
                book_root = self.json.ensure_book_dirs(book_id)["book_root"]
                payload = self.json.read_json(book_root / artifact.payload_ref.relative_path)
                panel = ReaderPanelRecord.model_validate(payload)
                batch_run_id = payload.get("batch_run_id", "")
                read_model.upsert_reader_panel_signal(
                    artifact_id=artifact.artifact_id,
                    book_id=book_id,
                    batch_run_id=batch_run_id,
                    chapter_no=panel.chapter_no,
                    panel_scope=panel.panel_scope,
                    aggregate_recommendation=panel.aggregate_recommendation,
                    risk_flags_json=json.dumps(panel.risk_flags, ensure_ascii=False),
                    created_at=artifact.created_at.isoformat(),
                )
            if artifact.artifact_type.value == "style_signal":
                book_root = self.json.ensure_book_dirs(book_id)["book_root"]
                payload = self.json.read_json(book_root / artifact.payload_ref.relative_path)
                signal = StyleSignalRecord.model_validate(payload)
                batch_run_id = payload.get("batch_run_id", "")
                read_model.upsert_style_signal(
                    artifact_id=artifact.artifact_id,
                    book_id=book_id,
                    batch_run_id=batch_run_id,
                    chapter_no=signal.chapter_no,
                    drift_score=signal.drift_score,
                    dominant_drift_axes_json=json.dumps(signal.dominant_drift_axes, ensure_ascii=False),
                    recommended_action=signal.recommended_action,
                    created_at=artifact.created_at.isoformat(),
                )
            if artifact.artifact_type.value == "adversarial_edit":
                book_root = self.json.ensure_book_dirs(book_id)["book_root"]
                payload = self.json.read_json(book_root / artifact.payload_ref.relative_path)
                edit = AdversarialEditRecord.model_validate(payload)
                read_model.upsert_adversarial_edit(
                    artifact_id=artifact.artifact_id,
                    book_id=book_id,
                    chapter_no=artifact.chapter_no,
                    source_settlement_artifact_id=edit.source_settlement_artifact_id,
                    candidate_settlement_artifact_id=edit.candidate_settlement_artifact_id,
                    kept=edit.kept,
                    decision_reason_codes_json=json.dumps(edit.decision_reason_codes, ensure_ascii=False),
                    created_at=artifact.created_at.isoformat(),
                )
        runs_manifest = self.json.read_json(self.json.ensure_book_dirs(book_id)["state"] / "runs_manifest.json")
        for run_item in runs_manifest.get("runs", []):
            read_model.upsert_run(
                self.json.load_run_record(book_id, run_item["run_id"], run_item["chapter_no"])
            )
        for batch_run in self.json.list_batch_runs(book_id):
            read_model.upsert_batch_run(
                batch_run_id=batch_run.batch_run_id,
                book_id=batch_run.book_id,
                batch_mode=batch_run.batch_mode.value,
                status=batch_run.status.value,
                chapter_range_json=json.dumps(batch_run.chapter_range, ensure_ascii=False),
                current_phase=batch_run.current_phase,
                frontier_chapter_no=batch_run.frontier_chapter_no,
                pause_reason_codes_json=json.dumps(batch_run.pause_reason_codes, ensure_ascii=False),
                last_checkpoint_id=batch_run.last_checkpoint_id,
                total_items=batch_run.total_items,
                completed_items=batch_run.completed_items,
                failed_items=batch_run.failed_items,
                started_at=batch_run.started_at.isoformat() if batch_run.started_at else None,
                finished_at=batch_run.finished_at.isoformat() if batch_run.finished_at else None,
            )
            for item in self.json.load_batch_items(book_id, batch_run.batch_run_id):
                read_model.upsert_batch_item(
                    item_id=item.item_id,
                    batch_run_id=item.batch_run_id,
                    book_id=book_id,
                    chapter_no=item.chapter_no,
                    phase=item.phase,
                    attempt=item.attempt,
                    status=item.status.value,
                    depends_on_snapshot_id=item.depends_on_snapshot_id,
                    depends_on_frontier=item.depends_on_frontier,
                    run_id=item.run_id,
                    output_refs_json=json.dumps(item.output_refs, ensure_ascii=False),
                    error_summary=item.error_summary,
                    updated_at=item.updated_at.isoformat(),
                )
        for checkpoint in self.json.list_batch_checkpoints(book_id):
            read_model.upsert_batch_checkpoint(
                checkpoint_id=checkpoint.checkpoint_id,
                batch_run_id=checkpoint.batch_run_id,
                book_id=book_id,
                phase=checkpoint.phase,
                frontier_chapter_no=checkpoint.frontier_chapter_no,
                truth_head_snapshot_id=checkpoint.truth_head_snapshot_id,
                open_blockers_json=json.dumps(checkpoint.open_blockers, ensure_ascii=False),
                panel_summary_refs_json=json.dumps(checkpoint.panel_summary_refs, ensure_ascii=False),
                created_at=checkpoint.created_at.isoformat(),
            )
