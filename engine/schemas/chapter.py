from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChapterStage(StrEnum):
    PLANNED = "planned"
    COMPOSED = "composed"
    DRAFTED = "drafted"
    SETTLED = "settled"
    AUDITED_PASSED = "audited_passed"
    AUDITED_FAILED = "audited_failed"
    REVISING = "revising"
    APPROVED = "approved"
    EXPORTED = "exported"
    BLOCKED = "blocked"
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    ROLLED_BACK = "rolled_back"
    INVALIDATED = "invalidated"


class ChapterStatusRecord(BaseModel):
    book_id: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    stage: ChapterStage = Field(default=ChapterStage.PLANNED)
    revision_round: int = Field(default=0, ge=0)
    blocked_reason: str | None = None
    invalidated_by: str | None = None
    current_artifact_refs: dict[str, str] = Field(default_factory=dict)
    last_run_id: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)

