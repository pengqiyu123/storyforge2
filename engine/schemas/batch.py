from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BatchMode(StrEnum):
    PREPARE = "prepare_batch"
    AUDIT = "audit_batch"
    APPROVE = "approve_batch"
    CHECKPOINT_REVIEW = "checkpoint_review"


class BatchRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class BatchItemStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class BatchRunRecord(BaseModel):
    batch_run_id: str = Field(min_length=1)
    book_id: str = Field(min_length=1)
    chapter_range: list[int] = Field(min_length=1)
    batch_mode: BatchMode
    phase_plan: list[str] = Field(default_factory=list)
    status: BatchRunStatus = Field(default=BatchRunStatus.QUEUED)
    forward_write_window: int = Field(default=2, ge=1)
    checkpoint_interval: int = Field(default=3, ge=1)
    current_phase: str | None = None
    frontier_chapter_no: int = Field(default=0, ge=0)
    pause_reason_codes: list[str] = Field(default_factory=list)
    last_checkpoint_id: str | None = None
    total_items: int = Field(default=0, ge=0)
    completed_items: int = Field(default=0, ge=0)
    failed_items: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class BatchItemRecord(BaseModel):
    item_id: str = Field(min_length=1)
    batch_run_id: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    phase: str = Field(min_length=1)
    attempt: int = Field(default=1, ge=1)
    status: BatchItemStatus = Field(default=BatchItemStatus.QUEUED)
    depends_on_snapshot_id: str | None = None
    depends_on_frontier: int | None = Field(default=None, ge=1)
    run_id: str | None = None
    output_refs: list[str] = Field(default_factory=list)
    error_summary: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class BatchCheckpointRecord(BaseModel):
    checkpoint_id: str = Field(min_length=1)
    batch_run_id: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    frontier_chapter_no: int = Field(ge=0)
    truth_head_snapshot_id: str = Field(min_length=1)
    open_blockers: list[str] = Field(default_factory=list)
    panel_summary_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class BatchSummaryRecord(BaseModel):
    batch_run_id: str = Field(min_length=1)
    completed_items: int = Field(default=0, ge=0)
    failed_items: int = Field(default=0, ge=0)
    paused: bool = False
    reason_codes: list[str] = Field(default_factory=list)
