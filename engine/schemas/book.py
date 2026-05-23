from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BookRecord(BaseModel):
    book_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    platform: str = Field(default="tomato", min_length=1)
    language: str = Field(default="zh", min_length=1)
    target_chapters: int = Field(default=12, ge=1)
    completed_chapters: int = Field(default=0, ge=0)
    engine_version: str = Field(default="0.1.0", min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BookIndexRecord(BaseModel):
    book_id: str = Field(min_length=1)
    chapters: list[int] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)

