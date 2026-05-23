from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunAction(StrEnum):
    INIT = "init"
    PLAN = "plan"
    COMPOSE = "compose"
    WRITE = "write"
    SETTLE = "settle"
    AUDIT = "audit"
    REVISE = "revise"
    COMPARE = "compare"
    APPROVE = "approve"
    EXPORT = "export"
    ROLLBACK = "rollback"
    INVALIDATE = "invalidate"
    BLOCK = "block"
    GENERIC = "generic"


class RunStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunRecord(BaseModel):
    run_id: str = Field(min_length=1)
    book_id: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    stage_action: RunAction
    actor_role: str = Field(min_length=1)
    status: RunStatus = Field(default=RunStatus.STARTED)
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    error_summary: str | None = None
    read_model_stale: bool = False
