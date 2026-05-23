from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from engine.schemas.artifact import ArtifactRecord
from engine.schemas.book import BookRecord
from engine.schemas.chapter import ChapterStatusRecord
from engine.schemas.run import RunRecord


class SQLiteReadModelStore:
    """Per-book SQLite read model that can be rebuilt from JSON canonical state."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS book (
                    book_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    language TEXT NOT NULL,
                    target_chapters INTEGER NOT NULL,
                    completed_chapters INTEGER NOT NULL,
                    engine_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chapter_status (
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    revision_round INTEGER NOT NULL,
                    blocked_reason TEXT,
                    invalidated_by TEXT,
                    current_artifact_refs_json TEXT NOT NULL,
                    last_run_id TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (book_id, chapter_no)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    stage_action TEXT NOT NULL,
                    actor_role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_refs_json TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    artifact_type TEXT NOT NULL,
                    produced_by_run_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chapter_quality (
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    gate_decision_artifact_id TEXT PRIMARY KEY,
                    settlement_artifact_id TEXT NOT NULL,
                    overall_score REAL NOT NULL,
                    critical_count INTEGER NOT NULL,
                    blocked_by_mechanical INTEGER NOT NULL,
                    blocking_rule_ids_json TEXT NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    decision_status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS truth_head (
                    book_id TEXT PRIMARY KEY,
                    current_snapshot_id TEXT NOT NULL,
                    committed_through_chapter INTEGER NOT NULL,
                    latest_truth_commit_run_id TEXT NOT NULL,
                    latest_projection_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS truth_deltas (
                    delta_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    base_snapshot_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    conflict_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS propagation_debts (
                    debt_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    trigger_chapter_no INTEGER NOT NULL,
                    stale_snapshot_id TEXT NOT NULL,
                    current_snapshot_id TEXT NOT NULL,
                    source_truth_commit_receipt_id TEXT NOT NULL,
                    dependency_scope TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    blocking INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    dependency_hits_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT
                );
                CREATE TABLE IF NOT EXISTS truth_invalidations (
                    debt_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    trigger_chapter_no INTEGER NOT NULL,
                    stale_snapshot_id TEXT NOT NULL,
                    current_snapshot_id TEXT NOT NULL,
                    source_truth_commit_receipt_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chapter_exports (
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    latest_manifest_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    chapter_file_sha256 TEXT NOT NULL,
                    chapter_semantic_sha256 TEXT NOT NULL,
                    integrity_status TEXT NOT NULL,
                    exported_at TEXT NOT NULL,
                    PRIMARY KEY (book_id, chapter_no, platform)
                );
                CREATE TABLE IF NOT EXISTS batch_runs (
                    batch_run_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    batch_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chapter_range_json TEXT NOT NULL,
                    current_phase TEXT,
                    frontier_chapter_no INTEGER NOT NULL,
                    pause_reason_codes_json TEXT NOT NULL,
                    last_checkpoint_id TEXT,
                    total_items INTEGER NOT NULL,
                    completed_items INTEGER NOT NULL,
                    failed_items INTEGER NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS batch_items (
                    item_id TEXT PRIMARY KEY,
                    batch_run_id TEXT NOT NULL,
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    depends_on_snapshot_id TEXT,
                    depends_on_frontier INTEGER,
                    run_id TEXT,
                    output_refs_json TEXT NOT NULL,
                    error_summary TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batch_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    batch_run_id TEXT NOT NULL,
                    book_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    frontier_chapter_no INTEGER NOT NULL,
                    truth_head_snapshot_id TEXT NOT NULL,
                    open_blockers_json TEXT NOT NULL,
                    panel_summary_refs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reader_panel_signals (
                    artifact_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    batch_run_id TEXT NOT NULL,
                    chapter_no INTEGER,
                    panel_scope TEXT NOT NULL,
                    aggregate_recommendation TEXT NOT NULL,
                    risk_flags_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS adversarial_edits (
                    artifact_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    chapter_no INTEGER NOT NULL,
                    source_settlement_artifact_id TEXT NOT NULL,
                    candidate_settlement_artifact_id TEXT NOT NULL,
                    kept INTEGER NOT NULL,
                    decision_reason_codes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS style_signals (
                    artifact_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL,
                    batch_run_id TEXT,
                    chapter_no INTEGER,
                    drift_score REAL NOT NULL,
                    dominant_drift_axes_json TEXT NOT NULL,
                    recommended_action TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def upsert_book(self, record: BookRecord) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO book (
                    book_id, title, platform, language, target_chapters,
                    completed_chapters, engine_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(book_id) DO UPDATE SET
                    title=excluded.title,
                    platform=excluded.platform,
                    language=excluded.language,
                    target_chapters=excluded.target_chapters,
                    completed_chapters=excluded.completed_chapters,
                    engine_version=excluded.engine_version,
                    updated_at=excluded.updated_at
                """,
                (
                    record.book_id,
                    record.title,
                    record.platform,
                    record.language,
                    record.target_chapters,
                    record.completed_chapters,
                    record.engine_version,
                    record.updated_at.isoformat(),
                ),
            )
            conn.commit()

    def upsert_chapter_status(self, record: ChapterStatusRecord) -> None:
        import json

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO chapter_status (
                    book_id, chapter_no, stage, revision_round, blocked_reason,
                    invalidated_by, current_artifact_refs_json, last_run_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(book_id, chapter_no) DO UPDATE SET
                    stage=excluded.stage,
                    revision_round=excluded.revision_round,
                    blocked_reason=excluded.blocked_reason,
                    invalidated_by=excluded.invalidated_by,
                    current_artifact_refs_json=excluded.current_artifact_refs_json,
                    last_run_id=excluded.last_run_id,
                    updated_at=excluded.updated_at
                """,
                (
                    record.book_id,
                    record.chapter_no,
                    record.stage.value,
                    record.revision_round,
                    record.blocked_reason,
                    record.invalidated_by,
                    json.dumps(record.current_artifact_refs, ensure_ascii=False, sort_keys=True),
                    record.last_run_id,
                    record.updated_at.isoformat(),
                ),
            )
            conn.commit()

    def upsert_run(self, record: RunRecord) -> None:
        import json

        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, book_id, chapter_no, stage_action, actor_role,
                    status, output_refs_json, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    output_refs_json=excluded.output_refs_json,
                    finished_at=excluded.finished_at
                """,
                (
                    record.run_id,
                    record.book_id,
                    record.chapter_no,
                    record.stage_action.value,
                    record.actor_role,
                    record.status.value,
                    json.dumps(record.output_refs, ensure_ascii=False),
                    record.finished_at.isoformat() if record.finished_at else None,
                ),
            )
            conn.commit()

    def upsert_artifact(self, record: ArtifactRecord) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, book_id, chapter_no, artifact_type,
                    produced_by_run_id, relative_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO NOTHING
                """,
                (
                    record.artifact_id,
                    record.book_id,
                    record.chapter_no,
                    record.artifact_type.value,
                    record.produced_by_run_id,
                    record.payload_ref.relative_path,
                    record.created_at.isoformat(),
                ),
            )
            conn.commit()

    def fetch_chapter_status(self, book_id: str, chapter_no: int) -> dict | None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT *
                FROM chapter_status
                WHERE book_id = ? AND chapter_no = ?
                """,
                (book_id, chapter_no),
            ).fetchone()
            return dict(row) if row else None

    def upsert_chapter_quality(
        self,
        *,
        book_id: str,
        chapter_no: int,
        gate_decision_artifact_id: str,
        settlement_artifact_id: str,
        overall_score: float,
        critical_count: int,
        blocked_by_mechanical: bool,
        blocking_rule_ids_json: str,
        reason_codes_json: str,
        decision_status: str,
        created_at: str,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO chapter_quality (
                    book_id, chapter_no, gate_decision_artifact_id, settlement_artifact_id,
                    overall_score, critical_count, blocked_by_mechanical, blocking_rule_ids_json,
                    reason_codes_json, decision_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gate_decision_artifact_id) DO UPDATE SET
                    settlement_artifact_id=excluded.settlement_artifact_id,
                    overall_score=excluded.overall_score,
                    critical_count=excluded.critical_count,
                    blocked_by_mechanical=excluded.blocked_by_mechanical,
                    blocking_rule_ids_json=excluded.blocking_rule_ids_json,
                    reason_codes_json=excluded.reason_codes_json,
                    decision_status=excluded.decision_status,
                    created_at=excluded.created_at
                """,
                (
                    book_id,
                    chapter_no,
                    gate_decision_artifact_id,
                    settlement_artifact_id,
                    overall_score,
                    critical_count,
                    1 if blocked_by_mechanical else 0,
                    blocking_rule_ids_json,
                    reason_codes_json,
                    decision_status,
                    created_at,
                ),
            )
            conn.commit()

    def upsert_truth_head(
        self,
        *,
        book_id: str,
        current_snapshot_id: str,
        committed_through_chapter: int,
        latest_truth_commit_run_id: str,
        latest_projection_version: int,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO truth_head (
                    book_id, current_snapshot_id, committed_through_chapter,
                    latest_truth_commit_run_id, latest_projection_version
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(book_id) DO UPDATE SET
                    current_snapshot_id=excluded.current_snapshot_id,
                    committed_through_chapter=excluded.committed_through_chapter,
                    latest_truth_commit_run_id=excluded.latest_truth_commit_run_id,
                    latest_projection_version=excluded.latest_projection_version
                """,
                (
                    book_id,
                    current_snapshot_id,
                    committed_through_chapter,
                    latest_truth_commit_run_id,
                    latest_projection_version,
                ),
            )
            conn.commit()

    def upsert_truth_delta(
        self,
        *,
        delta_id: str,
        book_id: str,
        chapter_no: int,
        base_snapshot_id: str,
        status: str,
        conflict_count: int,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO truth_deltas (
                    delta_id, book_id, chapter_no, base_snapshot_id, status, conflict_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(delta_id) DO UPDATE SET
                    status=excluded.status,
                    conflict_count=excluded.conflict_count
                """,
                (
                    delta_id,
                    book_id,
                    chapter_no,
                    base_snapshot_id,
                    status,
                    conflict_count,
                ),
            )
            conn.commit()

    def upsert_propagation_debt(
        self,
        *,
        debt_id: str,
        book_id: str,
        chapter_no: int,
        trigger_chapter_no: int,
        stale_snapshot_id: str,
        current_snapshot_id: str,
        source_truth_commit_receipt_id: str,
        dependency_scope: str,
        reason_code: str,
        blocking: bool,
        status: str,
        dependency_hits_json: str,
        created_at: str,
        resolved_at: str | None,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO propagation_debts (
                    debt_id, book_id, chapter_no, trigger_chapter_no, stale_snapshot_id,
                    current_snapshot_id, source_truth_commit_receipt_id, dependency_scope,
                    reason_code, blocking, status, dependency_hits_json, created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(debt_id) DO UPDATE SET
                    status=excluded.status,
                    dependency_hits_json=excluded.dependency_hits_json,
                    resolved_at=excluded.resolved_at
                """,
                (
                    debt_id,
                    book_id,
                    chapter_no,
                    trigger_chapter_no,
                    stale_snapshot_id,
                    current_snapshot_id,
                    source_truth_commit_receipt_id,
                    dependency_scope,
                    reason_code,
                    1 if blocking else 0,
                    status,
                    dependency_hits_json,
                    created_at,
                    resolved_at,
                ),
            )
            conn.commit()

    def upsert_truth_invalidation(
        self,
        *,
        debt_id: str,
        book_id: str,
        chapter_no: int,
        trigger_chapter_no: int,
        stale_snapshot_id: str,
        current_snapshot_id: str,
        source_truth_commit_receipt_id: str,
        created_at: str,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO truth_invalidations (
                    debt_id, book_id, chapter_no, trigger_chapter_no, stale_snapshot_id,
                    current_snapshot_id, source_truth_commit_receipt_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(debt_id) DO UPDATE SET
                    current_snapshot_id=excluded.current_snapshot_id,
                    created_at=excluded.created_at
                """,
                (
                    debt_id,
                    book_id,
                    chapter_no,
                    trigger_chapter_no,
                    stale_snapshot_id,
                    current_snapshot_id,
                    source_truth_commit_receipt_id,
                    created_at,
                ),
            )
            conn.commit()

    def upsert_chapter_export(
        self,
        *,
        book_id: str,
        chapter_no: int,
        platform: str,
        latest_manifest_id: str,
        version: int,
        chapter_file_sha256: str,
        chapter_semantic_sha256: str,
        integrity_status: str,
        exported_at: str,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO chapter_exports (
                    book_id, chapter_no, platform, latest_manifest_id, version,
                    chapter_file_sha256, chapter_semantic_sha256, integrity_status, exported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(book_id, chapter_no, platform) DO UPDATE SET
                    latest_manifest_id=excluded.latest_manifest_id,
                    version=excluded.version,
                    chapter_file_sha256=excluded.chapter_file_sha256,
                    chapter_semantic_sha256=excluded.chapter_semantic_sha256,
                    integrity_status=excluded.integrity_status,
                    exported_at=excluded.exported_at
                """,
                (
                    book_id,
                    chapter_no,
                    platform,
                    latest_manifest_id,
                    version,
                    chapter_file_sha256,
                    chapter_semantic_sha256,
                    integrity_status,
                    exported_at,
                ),
            )
            conn.commit()

    def upsert_batch_run(
        self,
        *,
        batch_run_id: str,
        book_id: str,
        batch_mode: str,
        status: str,
        chapter_range_json: str,
        current_phase: str | None,
        frontier_chapter_no: int,
        pause_reason_codes_json: str,
        last_checkpoint_id: str | None,
        total_items: int,
        completed_items: int,
        failed_items: int,
        started_at: str | None,
        finished_at: str | None,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO batch_runs (
                    batch_run_id, book_id, batch_mode, status, chapter_range_json,
                    current_phase, frontier_chapter_no, pause_reason_codes_json,
                    last_checkpoint_id, total_items, completed_items, failed_items,
                    started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_run_id) DO UPDATE SET
                    status=excluded.status,
                    current_phase=excluded.current_phase,
                    frontier_chapter_no=excluded.frontier_chapter_no,
                    pause_reason_codes_json=excluded.pause_reason_codes_json,
                    last_checkpoint_id=excluded.last_checkpoint_id,
                    total_items=excluded.total_items,
                    completed_items=excluded.completed_items,
                    failed_items=excluded.failed_items,
                    started_at=excluded.started_at,
                    finished_at=excluded.finished_at
                """,
                (
                    batch_run_id,
                    book_id,
                    batch_mode,
                    status,
                    chapter_range_json,
                    current_phase,
                    frontier_chapter_no,
                    pause_reason_codes_json,
                    last_checkpoint_id,
                    total_items,
                    completed_items,
                    failed_items,
                    started_at,
                    finished_at,
                ),
            )
            conn.commit()

    def upsert_batch_item(
        self,
        *,
        item_id: str,
        batch_run_id: str,
        book_id: str,
        chapter_no: int,
        phase: str,
        attempt: int,
        status: str,
        depends_on_snapshot_id: str | None,
        depends_on_frontier: int | None,
        run_id: str | None,
        output_refs_json: str,
        error_summary: str | None,
        updated_at: str,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO batch_items (
                    item_id, batch_run_id, book_id, chapter_no, phase, attempt, status,
                    depends_on_snapshot_id, depends_on_frontier, run_id, output_refs_json,
                    error_summary, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    attempt=excluded.attempt,
                    status=excluded.status,
                    run_id=excluded.run_id,
                    output_refs_json=excluded.output_refs_json,
                    error_summary=excluded.error_summary,
                    updated_at=excluded.updated_at
                """,
                (
                    item_id,
                    batch_run_id,
                    book_id,
                    chapter_no,
                    phase,
                    attempt,
                    status,
                    depends_on_snapshot_id,
                    depends_on_frontier,
                    run_id,
                    output_refs_json,
                    error_summary,
                    updated_at,
                ),
            )
            conn.commit()

    def upsert_batch_checkpoint(
        self,
        *,
        checkpoint_id: str,
        batch_run_id: str,
        book_id: str,
        phase: str,
        frontier_chapter_no: int,
        truth_head_snapshot_id: str,
        open_blockers_json: str,
        panel_summary_refs_json: str,
        created_at: str,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO batch_checkpoints (
                    checkpoint_id, batch_run_id, book_id, phase, frontier_chapter_no,
                    truth_head_snapshot_id, open_blockers_json, panel_summary_refs_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO UPDATE SET
                    open_blockers_json=excluded.open_blockers_json,
                    panel_summary_refs_json=excluded.panel_summary_refs_json
                """,
                (
                    checkpoint_id,
                    batch_run_id,
                    book_id,
                    phase,
                    frontier_chapter_no,
                    truth_head_snapshot_id,
                    open_blockers_json,
                    panel_summary_refs_json,
                    created_at,
                ),
            )
            conn.commit()

    def upsert_reader_panel_signal(
        self,
        *,
        artifact_id: str,
        book_id: str,
        batch_run_id: str,
        chapter_no: int | None,
        panel_scope: str,
        aggregate_recommendation: str,
        risk_flags_json: str,
        created_at: str,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO reader_panel_signals (
                    artifact_id, book_id, batch_run_id, chapter_no, panel_scope,
                    aggregate_recommendation, risk_flags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    aggregate_recommendation=excluded.aggregate_recommendation,
                    risk_flags_json=excluded.risk_flags_json
                """,
                (
                    artifact_id,
                    book_id,
                    batch_run_id,
                    chapter_no,
                    panel_scope,
                    aggregate_recommendation,
                    risk_flags_json,
                    created_at,
                ),
            )
            conn.commit()

    def upsert_adversarial_edit(
        self,
        *,
        artifact_id: str,
        book_id: str,
        chapter_no: int,
        source_settlement_artifact_id: str,
        candidate_settlement_artifact_id: str,
        kept: bool,
        decision_reason_codes_json: str,
        created_at: str,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO adversarial_edits (
                    artifact_id, book_id, chapter_no, source_settlement_artifact_id,
                    candidate_settlement_artifact_id, kept, decision_reason_codes_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    kept=excluded.kept,
                    decision_reason_codes_json=excluded.decision_reason_codes_json
                """,
                (
                    artifact_id,
                    book_id,
                    chapter_no,
                    source_settlement_artifact_id,
                    candidate_settlement_artifact_id,
                    1 if kept else 0,
                    decision_reason_codes_json,
                    created_at,
                ),
            )
            conn.commit()

    def upsert_style_signal(
        self,
        *,
        artifact_id: str,
        book_id: str,
        batch_run_id: str | None,
        chapter_no: int | None,
        drift_score: float,
        dominant_drift_axes_json: str,
        recommended_action: str,
        created_at: str,
    ) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                """
                INSERT INTO style_signals (
                    artifact_id, book_id, batch_run_id, chapter_no, drift_score,
                    dominant_drift_axes_json, recommended_action, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(artifact_id) DO UPDATE SET
                    drift_score=excluded.drift_score,
                    dominant_drift_axes_json=excluded.dominant_drift_axes_json,
                    recommended_action=excluded.recommended_action
                """,
                (
                    artifact_id,
                    book_id,
                    batch_run_id,
                    chapter_no,
                    drift_score,
                    dominant_drift_axes_json,
                    recommended_action,
                    created_at,
                ),
            )
            conn.commit()
