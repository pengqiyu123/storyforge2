from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class IntentAction(StrEnum):
    CONTINUE_CHAPTER = "continue_chapter"
    RE_AUDIT_CHAPTER = "re_audit_chapter"
    ROLLBACK_CHAPTER = "rollback_chapter"
    APPROVE_CHAPTER = "approve_chapter"
    EXPORT_CHAPTER = "export_chapter"
    RESUME_BATCH = "resume_batch"
    QUERY_TRUTH = "query_truth"
    QUERY_GATE_FAILURE = "query_gate_failure"
    QUERY_CHAPTER_QUALITY = "query_chapter_quality"


class ParsedIntent(BaseModel):
    action: IntentAction
    book_id: str = Field(min_length=1)
    chapter_no: int | None = None
    parameters: dict = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class IntentCheckResult(BaseModel):
    allowed: bool
    blockers: list[str] = Field(default_factory=list)


class IntentExecResult(BaseModel):
    success: bool
    action: IntentAction
    result_refs: list[str] = Field(default_factory=list)
    message: str = ""
