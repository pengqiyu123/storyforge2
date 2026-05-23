from __future__ import annotations

from datetime import datetime, timezone

from engine.schemas.chapter import ChapterStage, ChapterStatusRecord


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChapterStateTransitionError(ValueError):
    """Raised when a chapter attempts an illegal state transition."""


class MissingRunContextError(ValueError):
    """Raised when a guarded transition lacks required runtime context."""


ALLOWED_TRANSITIONS: dict[ChapterStage, set[ChapterStage]] = {
    ChapterStage.PLANNED: {ChapterStage.COMPOSED, ChapterStage.BLOCKED, ChapterStage.INVALIDATED},
    ChapterStage.COMPOSED: {ChapterStage.DRAFTED, ChapterStage.BLOCKED, ChapterStage.INVALIDATED},
    ChapterStage.DRAFTED: {ChapterStage.SETTLED, ChapterStage.BLOCKED, ChapterStage.INVALIDATED},
    ChapterStage.SETTLED: {
        ChapterStage.AUDITED_PASSED,
        ChapterStage.AUDITED_FAILED,
        ChapterStage.BLOCKED,
        ChapterStage.INVALIDATED,
    },
    ChapterStage.AUDITED_PASSED: {
        ChapterStage.APPROVED,
        ChapterStage.ROLLED_BACK,
        ChapterStage.BLOCKED,
        ChapterStage.INVALIDATED,
    },
    ChapterStage.AUDITED_FAILED: {
        ChapterStage.REVISING,
        ChapterStage.ROLLED_BACK,
        ChapterStage.HUMAN_REVIEW_REQUIRED,
        ChapterStage.BLOCKED,
        ChapterStage.INVALIDATED,
    },
    ChapterStage.REVISING: {
        ChapterStage.SETTLED,
        ChapterStage.ROLLED_BACK,
        ChapterStage.HUMAN_REVIEW_REQUIRED,
        ChapterStage.BLOCKED,
        ChapterStage.INVALIDATED,
    },
    ChapterStage.ROLLED_BACK: {
        ChapterStage.REVISING,
        ChapterStage.SETTLED,
        ChapterStage.HUMAN_REVIEW_REQUIRED,
    },
    ChapterStage.APPROVED: {ChapterStage.EXPORTED, ChapterStage.INVALIDATED},
    ChapterStage.EXPORTED: {ChapterStage.INVALIDATED},
    ChapterStage.BLOCKED: {
        ChapterStage.PLANNED,
        ChapterStage.COMPOSED,
        ChapterStage.SETTLED,
        ChapterStage.INVALIDATED,
    },
    ChapterStage.HUMAN_REVIEW_REQUIRED: {ChapterStage.REVISING, ChapterStage.INVALIDATED},
    ChapterStage.INVALIDATED: {ChapterStage.PLANNED},
}


def assert_transition_allowed(current: ChapterStage, target: ChapterStage) -> None:
    if current not in ALLOWED_TRANSITIONS:
        current_value = current.value if isinstance(current, ChapterStage) else str(current)
        raise ChapterStateTransitionError(f"unknown chapter stage: {current_value}")
    allowed = ALLOWED_TRANSITIONS[current]
    if target not in allowed:
        current_value = current.value if isinstance(current, ChapterStage) else str(current)
        target_value = target.value if isinstance(target, ChapterStage) else str(target)
        raise ChapterStateTransitionError(
            f"illegal chapter transition: {current_value} -> {target_value}"
        )


def assert_required_artifact_refs(target: ChapterStage, artifact_refs: dict[str, str]) -> None:
    if target == ChapterStage.SETTLED and "draft" not in artifact_refs:
        raise MissingRunContextError("settled transition requires a draft artifact ref")
    if target in {ChapterStage.AUDITED_PASSED, ChapterStage.AUDITED_FAILED} and "audit" not in artifact_refs:
        raise MissingRunContextError("audit result transition requires an audit artifact ref")
    if target == ChapterStage.APPROVED and "audit" not in artifact_refs:
        raise MissingRunContextError("approved transition requires latest audit artifact ref")


def next_status(
    record: ChapterStatusRecord,
    target: ChapterStage,
    *,
    run_id: str,
    artifact_refs: dict[str, str] | None = None,
    blocked_reason: str | None = None,
    invalidated_by: str | None = None,
) -> ChapterStatusRecord:
    if not run_id:
        raise MissingRunContextError("state transition requires run_id")
    assert_transition_allowed(record.stage, target)
    artifact_refs = artifact_refs or {}
    assert_required_artifact_refs(target, artifact_refs)
    revision_round = record.revision_round + 1 if target == ChapterStage.REVISING else record.revision_round
    merged_refs = dict(record.current_artifact_refs)
    merged_refs.update(artifact_refs)
    return record.model_copy(
        update={
            "stage": target,
            "revision_round": revision_round,
            "blocked_reason": blocked_reason if target == ChapterStage.BLOCKED else None,
            "invalidated_by": invalidated_by if target == ChapterStage.INVALIDATED else None,
            "current_artifact_refs": merged_refs,
            "last_run_id": run_id,
            "updated_at": utc_now(),
        }
    )
