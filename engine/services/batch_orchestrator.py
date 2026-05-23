from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from engine.schemas.artifact import ReaderPanelRecord, StyleSignalRecord
from engine.schemas.batch import (
    BatchCheckpointRecord,
    BatchItemRecord,
    BatchItemStatus,
    BatchMode,
    BatchRunRecord,
    BatchRunStatus,
)
from .story_engine import StoryEngineService, utc_now


@dataclass(slots=True)
class NoOpStyleSignalAdapter:
    def evaluate(self, *, batch_run: BatchRunRecord, checkpoint: BatchCheckpointRecord, slice_payload: dict) -> StyleSignalRecord | None:
        return None


@dataclass(slots=True)
class DefaultReaderPanelAdapter:
    def evaluate(self, *, batch_run: BatchRunRecord, checkpoint: BatchCheckpointRecord, slice_payload: dict) -> dict:
        return {
            "panel_scope": "checkpoint",
            "editor_findings": [],
            "genre_reader_findings": [],
            "writer_findings": [],
            "first_reader_findings": [],
            "momentum_loss": False,
            "earned_ending": True,
            "cut_candidate": [],
            "missing_scene": [],
            "thinnest_character": None,
            "aggregate_recommendation": "continue",
            "risk_flags": [],
        }


class BatchOrchestratorService:
    def __init__(
        self,
        root: str | Path,
        *,
        engine: StoryEngineService | None = None,
        reader_panel: object | None = None,
        style_adapter: object | None = None,
    ) -> None:
        self.root = Path(root)
        self.engine = engine or StoryEngineService(self.root)
        self.reader_panel = reader_panel or DefaultReaderPanelAdapter()
        self.style_adapter = style_adapter or NoOpStyleSignalAdapter()
        self.repo = self.engine.repo

    def create_batch_run(
        self,
        book_id: str,
        chapter_range: list[int],
        batch_mode: str,
        config: dict | None = None,
    ) -> BatchRunRecord:
        chapter_range = self._normalize_chapter_range(chapter_range)
        config = config or {}
        mode = BatchMode(batch_mode)
        phase_plan = self._phase_plan(mode)
        record = BatchRunRecord(
            batch_run_id=f"batch-{uuid4().hex[:10]}",
            book_id=book_id,
            chapter_range=sorted(chapter_range),
            batch_mode=mode,
            phase_plan=phase_plan,
            current_phase=phase_plan[0] if phase_plan else None,
            forward_write_window=int(config.get("forward_write_window", 2)),
            checkpoint_interval=int(config.get("checkpoint_interval", 3)),
            total_items=len(phase_plan) * len(chapter_range),
        )
        items = self._build_items(record)
        self.repo.save_batch_run(record)
        self.repo.save_batch_items(book_id, record.batch_run_id, items)
        return record

    def start_batch_run(self, batch_run_id: str) -> BatchRunRecord:
        record = self._load_run_by_id(batch_run_id)
        if record.status not in {BatchRunStatus.QUEUED, BatchRunStatus.PAUSED}:
            return record
        return self._execute_run(record, is_resume=False)

    def resume_batch_run(self, batch_run_id: str) -> BatchRunRecord:
        record = self._load_run_by_id(batch_run_id)
        if record.status != BatchRunStatus.PAUSED:
            raise ValueError("resume_batch_run requires paused batch run")
        return self._execute_run(record, is_resume=True)

    def retry_batch_item(self, batch_run_id: str, item_id: str) -> BatchRunRecord:
        record = self._load_run_by_id(batch_run_id)
        items = self.repo.load_batch_items(record.book_id, batch_run_id)
        updated: list[BatchItemRecord] = []
        found = False
        for item in items:
            if item.item_id == item_id:
                updated.append(
                    item.model_copy(
                        update={
                            "status": BatchItemStatus.QUEUED,
                            "attempt": item.attempt + 1,
                            "error_summary": None,
                            "updated_at": utc_now(),
                        }
                    )
                )
                found = True
            else:
                updated.append(item)
        if not found:
            raise FileNotFoundError(f"batch item not found: {item_id}")
        self.repo.save_batch_items(record.book_id, batch_run_id, updated)
        return self.resume_batch_run(batch_run_id)

    def get_batch_run(self, batch_run_id: str) -> dict:
        record = self._load_run_by_id(batch_run_id)
        items = self.repo.load_batch_items(record.book_id, record.batch_run_id)
        checkpoints = self.repo.list_batch_checkpoints(record.book_id, record.batch_run_id)
        return {
            "batch_run": record,
            "items": items,
            "checkpoints": checkpoints,
        }

    def list_batch_runs(self, book_id: str, status: str | None = None) -> list[BatchRunRecord]:
        runs = self.repo.list_batch_runs(book_id)
        if status is not None:
            runs = [run for run in runs if run.status.value == status]
        return runs

    def run_checkpoint_review(self, batch_run_id: str, checkpoint_id: str | None = None) -> dict:
        record = self._load_run_by_id(batch_run_id)
        checkpoints = self.repo.list_batch_checkpoints(record.book_id, record.batch_run_id)
        if not checkpoints:
            raise ValueError("run_checkpoint_review requires at least one checkpoint")
        checkpoint = checkpoints[-1] if checkpoint_id is None else next(
            item for item in checkpoints if item.checkpoint_id == checkpoint_id
        )
        chapter_slice = [
            chapter_no
            for chapter_no in record.chapter_range
            if chapter_no <= checkpoint.frontier_chapter_no
        ]
        slice_payload = {
            "chapter_slice": chapter_slice,
            "truth_head_snapshot_id": checkpoint.truth_head_snapshot_id,
        }
        panel_raw = self.reader_panel.evaluate(
            batch_run=record,
            checkpoint=checkpoint,
            slice_payload=slice_payload,
        )
        panel = ReaderPanelRecord(
            chapter_slice=chapter_slice,
            batch_run_id=record.batch_run_id,  # type: ignore[arg-type]
            **panel_raw,
        )
        run = self.engine.start_run(record.book_id, chapter_slice[-1] if chapter_slice else 1, "generic", "reader_panel", [])
        panel_payload = panel.model_dump(mode="json")
        panel_payload["batch_run_id"] = record.batch_run_id
        panel_id = self.engine.register_artifact(
            record.book_id,
            chapter_slice[-1] if chapter_slice else record.chapter_range[0],
            "reader_panel",
            panel_payload,
            run.run_id,
        )
        self.repo.save_reader_panel_signal(
            book_id=record.book_id,
            batch_run_id=record.batch_run_id,
            artifact_id=panel_id,
            payload=panel,
            created_at=utc_now().isoformat(),
        )
        panel_refs = list(checkpoint.panel_summary_refs)
        panel_refs.append(panel_id)
        updated_checkpoint = checkpoint.model_copy(update={"panel_summary_refs": panel_refs})
        self.repo.append_batch_checkpoint(record.book_id, updated_checkpoint)

        style_signal = self.style_adapter.evaluate(
            batch_run=record,
            checkpoint=updated_checkpoint,
            slice_payload=slice_payload,
        )
        style_signal_id: str | None = None
        if style_signal is not None:
            style_payload = style_signal.model_dump(mode="json")
            style_payload["batch_run_id"] = record.batch_run_id
            style_signal_id = self.engine.register_artifact(
                record.book_id,
                chapter_slice[-1] if chapter_slice else record.chapter_range[0],
                "style_signal",
                style_payload,
                run.run_id,
            )
            self.repo.save_style_signal(
                book_id=record.book_id,
                batch_run_id=record.batch_run_id,
                artifact_id=style_signal_id,
                payload=style_signal,
                created_at=utc_now().isoformat(),
            )

        pause_reasons = list(record.pause_reason_codes)
        status = BatchRunStatus.COMPLETED
        if self._reader_panel_requires_pause(panel):
            if "reader_panel_escalation" not in pause_reasons:
                pause_reasons.append("reader_panel_escalation")
            status = BatchRunStatus.PAUSED
            self.repo.append_batch_pause_event(
                record.book_id,
                {
                    "batch_run_id": record.batch_run_id,
                    "checkpoint_id": updated_checkpoint.checkpoint_id,
                    "reason_codes": pause_reasons,
                    "created_at": utc_now().isoformat(),
                },
            )
        updated_run = record.model_copy(
            update={
                "status": status,
                "pause_reason_codes": pause_reasons,
                "finished_at": utc_now() if status == BatchRunStatus.COMPLETED else None,
            }
        )
        self.repo.save_batch_run(updated_run)
        self.engine.finish_run(
            record.book_id,
            chapter_slice[-1] if chapter_slice else record.chapter_range[0],
            run.run_id,
            "succeeded",
            [panel_id, *( [style_signal_id] if style_signal_id else [] )],
            None,
        )
        return {
            "batch_run": updated_run,
            "checkpoint": updated_checkpoint,
            "reader_panel_artifact_id": panel_id,
            "style_signal_artifact_id": style_signal_id,
        }

    def run_adversarial_edit(self, book_id: str, chapter_no: int, source_settlement_id: str, instruction: str | None = None) -> dict:
        from engine.services.adversarial_editor import AdversarialEditor

        instruction = instruction or "compress"
        status = self.engine.repo.load_chapter_status(book_id, chapter_no)
        if status.stage in {"approved", "exported"}:
            raise ValueError("adversarial edit not allowed on frozen chapters")
        source_payload = self.engine._load_artifact_payload(book_id, source_settlement_id)
        draft_artifact_id = source_payload.get("draft_artifact_id", "")
        if not draft_artifact_id:
            raise ValueError("settlement artifact has no draft_artifact_id")
        draft_payload = self.engine._load_artifact_payload(book_id, draft_artifact_id)
        source_text = draft_payload.get("text", "")
        original_quality = self.engine._load_artifact_payload(
            book_id,
            status.current_artifact_refs.get("chapter_quality", ""),
        ) if status.current_artifact_refs.get("chapter_quality") else None
        original_score = float(original_quality.get("overall_score", 0.0)) if original_quality else 0.0
        editor = AdversarialEditor(self.engine.gate_runner.auditor.provider if hasattr(self.engine.gate_runner.auditor, "provider") else None)
        candidate_text = editor.generate_candidate(source_text, instruction)
        run = self.engine.start_run(book_id, chapter_no, "adversarial_edit", "adversarial_editor", [source_settlement_id])
        candidate_draft_id = self.engine.register_artifact(
            book_id, chapter_no, "draft",
            {"text": candidate_text, "mode": "adversarial", "instruction": instruction, "chapter_no": chapter_no},
            run.run_id,
        )
        from engine.utils.chinese_text import count_chinese_chars as _ccc
        record = AdversarialEditor.decide(
            original_overall_score=original_score,
            candidate_overall_score=original_score,
            source_settlement_artifact_id=source_settlement_id,
            candidate_draft_artifact_id=candidate_draft_id,
            candidate_settlement_artifact_id=source_settlement_id,
            edit_instruction=instruction,
            original_char_count=_ccc(source_text),
            candidate_char_count=_ccc(candidate_text),
        )
        artifact_id = self.engine.register_artifact(
            book_id, chapter_no, "adversarial_edit",
            record.model_dump(mode="json"),
            run.run_id,
        )
        self.engine.finish_run(book_id, chapter_no, run.run_id, "succeeded", [artifact_id], None)
        return {"adversarial_edit_artifact_id": artifact_id, "kept": record.kept, "decision_reason_codes": record.decision_reason_codes}

    def _execute_run(self, record: BatchRunRecord, *, is_resume: bool) -> BatchRunRecord:
        started = record.model_copy(
            update={
                "status": BatchRunStatus.RUNNING,
                "started_at": record.started_at or utc_now(),
                "finished_at": None,
                "pause_reason_codes": [],
            }
        )
        self.repo.save_batch_run(started)
        items = self.repo.load_batch_items(record.book_id, record.batch_run_id)
        processed_prepare = 0
        for item in items:
            if item.status == BatchItemStatus.SUCCEEDED:
                continue
            if item.status == BatchItemStatus.FAILED and not is_resume:
                continue
            readiness_error = self._check_item_readiness(started, item)
            if readiness_error:
                return self._pause_run(started, items, readiness_error)
            if started.batch_mode == BatchMode.PREPARE and item.phase == "plan":
                if processed_prepare >= started.forward_write_window:
                    return self._pause_run(started, items, "forward_window_limit")
            updated_item = item.model_copy(update={"status": BatchItemStatus.RUNNING, "updated_at": utc_now()})
            items = self._replace_item(items, updated_item)
            self.repo.save_batch_items(record.book_id, record.batch_run_id, items)
            try:
                outputs = self._execute_item(started, updated_item)
            except Exception as exc:
                failed = updated_item.model_copy(
                    update={
                        "status": BatchItemStatus.FAILED,
                        "error_summary": str(exc),
                        "updated_at": utc_now(),
                    }
                )
                items = self._replace_item(items, failed)
                self.repo.save_batch_items(record.book_id, record.batch_run_id, items)
                return self._pause_run(started, items, "chapter_failed")
            if item.phase == "audit":
                chapter_status = self.engine.get_chapter_status(record.book_id, item.chapter_no)["status"]
                if chapter_status.stage.value != "audited_passed":
                    succeeded = updated_item.model_copy(
                        update={
                            "status": BatchItemStatus.FAILED,
                            "output_refs": outputs,
                            "error_summary": "audit did not pass gate",
                            "updated_at": utc_now(),
                        }
                    )
                    items = self._replace_item(items, succeeded)
                    self.repo.save_batch_items(record.book_id, record.batch_run_id, items)
                    return self._pause_run(started, items, "chapter_failed")
            succeeded = updated_item.model_copy(
                update={
                    "status": BatchItemStatus.SUCCEEDED,
                    "output_refs": outputs,
                    "updated_at": utc_now(),
                }
            )
            items = self._replace_item(items, succeeded)
            self.repo.save_batch_items(record.book_id, record.batch_run_id, items)
            if started.batch_mode == BatchMode.PREPARE and item.phase == "settle":
                processed_prepare += 1
                started = self._write_checkpoint_if_needed(started, item.chapter_no, processed_prepare)
            elif item.phase == "approve":
                started = self._write_checkpoint_if_needed(started, item.chapter_no, item.chapter_no)
        completed = started.model_copy(
            update={
                "status": BatchRunStatus.COMPLETED,
                "completed_items": sum(1 for item in items if item.status == BatchItemStatus.SUCCEEDED),
                "failed_items": sum(1 for item in items if item.status == BatchItemStatus.FAILED),
                "frontier_chapter_no": max(
                    [item.chapter_no for item in items if item.status == BatchItemStatus.SUCCEEDED] or [0]
                ),
                "finished_at": utc_now(),
            }
        )
        self.repo.save_batch_run(completed)
        return completed

    def _execute_item(self, record: BatchRunRecord, item: BatchItemRecord) -> list[str]:
        chapter_no = item.chapter_no
        if item.phase == "plan":
            artifact_id = self.engine.plan_chapter(record.book_id, chapter_no, guidance=f"batch plan for chapter {chapter_no}")
            return [artifact_id]
        if item.phase == "compose":
            artifact_id = self.engine.compose_chapter(record.book_id, chapter_no)
            return [artifact_id]
        if item.phase == "draft":
            artifact_id = self.engine.write_chapter_draft(record.book_id, chapter_no, mode="initial")
            return [artifact_id]
        if item.phase == "settle":
            status = self.engine.get_chapter_status(record.book_id, chapter_no)["status"]
            draft_id = status.current_artifact_refs["draft"]
            settled = self.engine.settle_chapter(record.book_id, chapter_no, draft_id)
            return [settled.current_artifact_refs["settlement"]]
        if item.phase == "audit":
            status = self.engine.get_chapter_status(record.book_id, chapter_no)["status"]
            decision = self.engine.audit_chapter(record.book_id, chapter_no, status.current_artifact_refs["settlement"])
            return [decision["gate_decision_artifact_id"]]
        if item.phase == "approve":
            approved = self.engine.approve_chapter(record.book_id, chapter_no)
            return [approved.current_artifact_refs["approval_receipt"]]
        raise ValueError(f"unsupported batch phase: {item.phase}")

    def _check_item_readiness(self, record: BatchRunRecord, item: BatchItemRecord) -> str | None:
        status = self.engine.get_chapter_status(record.book_id, item.chapter_no)["status"]
        freshness = self.engine.get_chapter_truth_freshness(record.book_id, item.chapter_no)
        if not freshness["is_fresh"]:
            return "stale_truth_head"
        if status.stage.value == "invalidated":
            return "stale_truth_head"
        if status.stage.value in {"blocked", "human_review_required"}:
            return "chapter_blocked" if status.stage.value == "blocked" else "human_review_required"
        if item.phase == "plan" and status.stage.value != "planned":
            return "chapter_not_ready"
        if item.phase == "compose" and status.stage.value != "planned":
            return "chapter_not_ready"
        if item.phase == "draft" and status.stage.value != "composed":
            return "chapter_not_ready"
        if item.phase == "settle" and status.stage.value not in {"drafted", "revising"}:
            return "chapter_not_ready"
        if item.phase == "audit" and status.stage.value != "settled":
            return "chapter_not_ready"
        if item.phase == "approve" and status.stage.value != "audited_passed":
            return "chapter_not_ready"
        return None

    def _pause_run(self, record: BatchRunRecord, items: list[BatchItemRecord], reason_code: str) -> BatchRunRecord:
        paused = record.model_copy(
            update={
                "status": BatchRunStatus.PAUSED,
                "pause_reason_codes": [reason_code],
                "completed_items": sum(1 for item in items if item.status == BatchItemStatus.SUCCEEDED),
                "failed_items": sum(1 for item in items if item.status == BatchItemStatus.FAILED),
                "frontier_chapter_no": max(
                    [item.chapter_no for item in items if item.status == BatchItemStatus.SUCCEEDED] or [0]
                ),
            }
        )
        self.repo.save_batch_run(paused)
        self.repo.append_batch_pause_event(
            record.book_id,
            {
                "batch_run_id": record.batch_run_id,
                "reason_codes": [reason_code],
                "created_at": utc_now().isoformat(),
            },
        )
        return paused

    def _write_checkpoint_if_needed(self, record: BatchRunRecord, chapter_no: int, completed_prepare_count: int) -> BatchRunRecord:
        should_checkpoint = (
            completed_prepare_count % record.checkpoint_interval == 0
            or chapter_no == record.chapter_range[-1]
        )
        if not should_checkpoint:
            return record
        truth_head = self.engine.get_truth_head(record.book_id)
        checkpoint = BatchCheckpointRecord(
            checkpoint_id=f"checkpoint-{uuid4().hex[:8]}",
            batch_run_id=record.batch_run_id,
            phase=record.current_phase or record.phase_plan[0],
            frontier_chapter_no=chapter_no,
            truth_head_snapshot_id=truth_head["truth_index"]["current_snapshot_id"],
            open_blockers=[],
            panel_summary_refs=[],
        )
        self.repo.append_batch_checkpoint(record.book_id, checkpoint)
        updated = record.model_copy(
            update={
                "last_checkpoint_id": checkpoint.checkpoint_id,
                "frontier_chapter_no": chapter_no,
                "completed_items": record.completed_items + 1,
            }
        )
        self.repo.save_batch_run(updated)
        return updated

    def _build_items(self, record: BatchRunRecord) -> list[BatchItemRecord]:
        items: list[BatchItemRecord] = []
        if record.batch_mode == BatchMode.PREPARE:
            for chapter_no in record.chapter_range:
                for phase in record.phase_plan:
                    items.append(
                        BatchItemRecord(
                            item_id=f"{record.batch_run_id}-{phase}-{chapter_no:04d}",
                            batch_run_id=record.batch_run_id,
                            chapter_no=chapter_no,
                            phase=phase,
                            depends_on_snapshot_id=self.engine.get_truth_head(record.book_id)["truth_index"]["current_snapshot_id"],
                            depends_on_frontier=chapter_no - 1 if chapter_no > min(record.chapter_range) else None,
                        )
                    )
            return items
        for phase in record.phase_plan:
            for chapter_no in record.chapter_range:
                items.append(
                    BatchItemRecord(
                        item_id=f"{record.batch_run_id}-{phase}-{chapter_no:04d}",
                        batch_run_id=record.batch_run_id,
                        chapter_no=chapter_no,
                        phase=phase,
                        depends_on_snapshot_id=self.engine.get_truth_head(record.book_id)["truth_index"]["current_snapshot_id"],
                        depends_on_frontier=chapter_no - 1 if chapter_no > min(record.chapter_range) else None,
                    )
                )
        return items

    def _load_run_by_id(self, batch_run_id: str) -> BatchRunRecord:
        for book_dir in (self.root / "books").iterdir():
            if not book_dir.is_dir():
                continue
            try:
                return self.repo.load_batch_run(book_dir.name, batch_run_id)
            except FileNotFoundError:
                continue
        raise FileNotFoundError(f"batch run not found: {batch_run_id}")

    @staticmethod
    def _replace_item(items: list[BatchItemRecord], replacement: BatchItemRecord) -> list[BatchItemRecord]:
        return [replacement if item.item_id == replacement.item_id else item for item in items]

    @staticmethod
    def _normalize_chapter_range(chapter_range: list[int]) -> list[int]:
        if not chapter_range:
            raise ValueError("chapter_range cannot be empty")
        if len(chapter_range) == 2 and chapter_range[1] >= chapter_range[0]:
            return list(range(chapter_range[0], chapter_range[1] + 1))
        expected = list(range(min(chapter_range), max(chapter_range) + 1))
        if sorted(chapter_range) != expected:
            raise ValueError("chapter_range must be continuous")
        return sorted(chapter_range)

    @staticmethod
    def _phase_plan(mode: BatchMode) -> list[str]:
        if mode == BatchMode.PREPARE:
            return ["plan", "compose", "draft", "settle"]
        if mode == BatchMode.AUDIT:
            return ["audit"]
        if mode == BatchMode.APPROVE:
            return ["approve"]
        if mode == BatchMode.CHECKPOINT_REVIEW:
            return []
        raise ValueError(f"unsupported batch mode: {mode}")

    @staticmethod
    def _reader_panel_requires_pause(panel: ReaderPanelRecord) -> bool:
        risk_flags = set(panel.risk_flags)
        return (
            "momentum_loss" in risk_flags
            or not panel.earned_ending
            or bool(panel.missing_scene)
            or panel.aggregate_recommendation == "pause_for_review"
        )
