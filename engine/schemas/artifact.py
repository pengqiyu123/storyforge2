from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactType(StrEnum):
    PLAN = "plan"
    COMPOSE_CONTEXT = "compose_context"
    DRAFT = "draft"
    SETTLEMENT = "settlement"
    AUDIT = "audit"
    REVISION = "revision"
    MECHANICAL_GATE = "mechanical_gate"
    GATE_DECISION = "gate_decision"
    CHAPTER_QUALITY = "chapter_quality"
    TRUTH_SNAPSHOT = "truth_snapshot"
    TRUTH_DELTA = "truth_delta"
    TRUTH_RECONCILE = "truth_reconcile"
    TRUTH_COMMIT_RECEIPT = "truth_commit_receipt"
    TRUTH_PROJECTION = "truth_projection"
    TRUTH_INVALIDATION = "truth_invalidation"
    PROPAGATION_DEBT = "propagation_debt"
    REVISION_BRIEF = "revision_brief"
    COMPARISON = "comparison"
    APPROVAL_RECEIPT = "approval_receipt"
    EXPORT_METADATA = "export_metadata"
    EXPORT_PACKAGE = "export_package"
    EXPORT_MANIFEST = "export_manifest"
    EXPORT_DIFF = "export_diff"
    ADVERSARIAL_EDIT = "adversarial_edit"
    READER_PANEL = "reader_panel"
    STYLE_SIGNAL = "style_signal"
    TRACE = "trace"
    GENERIC = "generic"


class ArtifactPayloadRef(BaseModel):
    relative_path: str = Field(min_length=1)


class ArtifactRecord(BaseModel):
    artifact_id: str = Field(min_length=1)
    artifact_type: ArtifactType
    book_id: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    produced_by_run_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    content_hash: str = Field(min_length=1)
    payload_ref: ArtifactPayloadRef


class AuditIssue(BaseModel):
    severity: str = Field(pattern="^(info|warning|critical)$")
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    suggestion: str | None = None


class AuditRecord(BaseModel):
    passed: bool
    critical_count: int = Field(default=0, ge=0)
    issues: list[AuditIssue] = Field(default_factory=list)
    recommended_mode: str = Field(default="accept", min_length=1)
    score_summary: dict[str, float] = Field(default_factory=dict)


class RuleSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RuleCategory(StrEnum):
    INTEGRITY = "integrity"
    STYLE = "style"
    META = "meta"
    STRUCTURE = "structure"
    AI_TELL = "ai_tell"


class GatePolicy(StrEnum):
    BLOCK = "block"
    WARN = "warn"


class RuleDefinition(BaseModel):
    rule_id: str = Field(min_length=1)
    severity: RuleSeverity
    category: RuleCategory
    policy: GatePolicy
    description: str = Field(min_length=1)
    threshold: float | int | str | None = None


class RuleResult(BaseModel):
    rule_id: str = Field(min_length=1)
    passed: bool
    message: str = Field(min_length=1)
    severity: RuleSeverity = Field(default=RuleSeverity.WARNING)
    category: RuleCategory = Field(default=RuleCategory.STYLE)
    blocking: bool = False
    observed: float | int | str | None = None
    threshold: float | int | str | None = None
    evidence: list[str] = Field(default_factory=list)


class MechanicalGateRecord(BaseModel):
    rule_results: list[RuleResult] = Field(default_factory=list)
    blocked: bool = False
    summary_counts: dict[str, int] = Field(default_factory=dict)
    blocking_rule_ids: list[str] = Field(default_factory=list)
    warning_rule_ids: list[str] = Field(default_factory=list)
    issue_counts_by_severity: dict[str, int] = Field(default_factory=dict)


class RevisionRecord(BaseModel):
    base_artifact_id: str = Field(min_length=1)
    candidate_artifact_id: str = Field(min_length=1)
    evaluation_result: str = Field(min_length=1)
    kept: bool
    rollback_reason: str | None = None


class ChapterPlanRecord(BaseModel):
    chapter_no: int = Field(ge=1)
    must_advance: list[str] = Field(default_factory=list)
    eligible_resolve: list[str] = Field(default_factory=list)
    must_not_do: list[str] = Field(default_factory=list)
    hook_target: str = Field(default="information_flip", min_length=1)
    guidance: str | None = None


class ChapterComposeRecord(BaseModel):
    chapter_no: int = Field(ge=1)
    source_refs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    assembled_summary: str = Field(default="Assembled chapter context.", min_length=1)
    truth_snapshot_id: str | None = None
    truth_context_refs: dict[str, str] = Field(default_factory=dict)
    truth_context_slice: dict[str, object] = Field(default_factory=dict)
    truth_dependency_manifest: dict[str, object] | None = None


class ChapterSettlementRecord(BaseModel):
    draft_artifact_id: str = Field(min_length=1)
    text_hash: str = Field(min_length=1)
    word_count: int = Field(ge=0)
    paragraph_count: int = Field(ge=0)
    sentence_count: int = Field(ge=0)
    baseline_gate_ref: str | None = None
    audit_input_refs: dict[str, str] = Field(default_factory=dict)
    candidate_signature: str = Field(min_length=1)
    base_truth_snapshot_id: str | None = None


class GateDecisionRecord(BaseModel):
    passed: bool
    overall_score: float = Field(ge=0)
    critical_count: int = Field(default=0, ge=0)
    blocked_by_mechanical: bool = False
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    source_refs: dict[str, str] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)


class ChapterQualityRecord(BaseModel):
    chapter_no: int = Field(ge=1)
    revision_round: int = Field(ge=0)
    settlement_artifact_id: str = Field(min_length=1)
    candidate_signature: str = Field(min_length=1)
    mechanical_gate_artifact_id: str = Field(min_length=1)
    audit_artifact_id: str = Field(min_length=1)
    gate_decision_artifact_id: str = Field(min_length=1)
    baseline_gate_artifact_id: str | None = None
    overall_score: float = Field(ge=0)
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    critical_count: int = Field(default=0, ge=0)
    blocked_by_mechanical: bool = False
    blocking_rule_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    decision_status: str = Field(min_length=1)
    truth_snapshot_artifact_id: str | None = None
    truth_delta_artifact_id: str | None = None
    truth_conflict_count: int = Field(default=0, ge=0)
    base_truth_snapshot_id: str | None = None
    propagation_debt_artifact_id: str | None = None


class FactAssertionBasis(StrEnum):
    OBSERVED = "observed"
    EXPLICIT = "explicit"
    DERIVED = "derived"


class TruthConflictRecord(BaseModel):
    conflict_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    severity: str = Field(pattern="^(warning|blocking)$")
    message: str = Field(min_length=1)
    source_ref: str | None = None


class CanonFactRecord(BaseModel):
    fact_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    hard: bool = False
    assertion_basis: FactAssertionBasis
    source_ref: str = Field(min_length=1)


class RelationshipEdgeRecord(BaseModel):
    edge_id: str = Field(min_length=1)
    source_character_id: str = Field(min_length=1)
    target_character_id: str = Field(min_length=1)
    relation_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)


class CharacterStateRecord(BaseModel):
    character_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    status_tags: list[str] = Field(default_factory=list)
    current_location: str | None = None
    relationship_refs: list[str] = Field(default_factory=list)
    known_fact_ids: list[str] = Field(default_factory=list)
    last_updated_chapter: int = Field(default=0, ge=0)
    source_ref: str = Field(min_length=1)


class HookClueRecord(BaseModel):
    hook_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    kind: str = Field(pattern="^(hook|clue)$")
    status: str = Field(pattern="^(open|advanced|resolved|invalidated)$")
    introduced_in: int = Field(ge=0)
    resolved_in: int | None = None
    owner_entity_ids: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)
    scene_id: str | None = None


class ChapterFactRecord(BaseModel):
    chapter_no: int = Field(ge=1)
    fact_ids: list[str] = Field(default_factory=list)
    irreversible_fact_ids: list[str] = Field(default_factory=list)
    truth_delta_id: str | None = None
    committed_snapshot_id: str | None = None
    event_refs: list[str] = Field(default_factory=list)


class TruthSnapshotRecord(BaseModel):
    snapshot_id: str = Field(min_length=1)
    base_snapshot_id: str | None = None
    committed_through_chapter: int = Field(default=0, ge=0)
    canon_ref: str = Field(min_length=1)
    character_ref: str = Field(min_length=1)
    hook_ref: str = Field(min_length=1)
    chapter_fact_ref: str = Field(min_length=1)
    created_by_run_id: str = Field(min_length=1)


class TruthDeltaRecord(BaseModel):
    delta_id: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    settlement_artifact_id: str = Field(min_length=1)
    draft_artifact_id: str = Field(min_length=1)
    base_snapshot_id: str = Field(min_length=1)
    proposed_fact_additions: list[CanonFactRecord] = Field(default_factory=list)
    proposed_fact_updates: list[CanonFactRecord] = Field(default_factory=list)
    proposed_hook_updates: list[HookClueRecord] = Field(default_factory=list)
    proposed_character_updates: list[CharacterStateRecord] = Field(default_factory=list)
    proposed_relationship_updates: list[RelationshipEdgeRecord] = Field(default_factory=list)
    chapter_irreversible_fact_ids: list[str] = Field(default_factory=list)
    conflicts: list[TruthConflictRecord] = Field(default_factory=list)
    status: str = Field(pattern="^(proposed|reconciled|committed|rejected|stale)$")
    notes: list[str] = Field(default_factory=list)


class TruthCommitReceiptRecord(BaseModel):
    receipt_id: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    base_snapshot_id: str = Field(min_length=1)
    new_snapshot_id: str = Field(min_length=1)
    truth_delta_artifact_id: str = Field(min_length=1)
    changed_entity_ids: list[str] = Field(default_factory=list)
    changed_hook_ids: list[str] = Field(default_factory=list)
    changed_fact_ids: list[str] = Field(default_factory=list)
    affected_ledgers: list[str] = Field(default_factory=list)
    committed_from_chapter: int = Field(ge=1)
    invalidated_chapter_nos: list[int] = Field(default_factory=list)
    propagation_debt_ids: list[str] = Field(default_factory=list)


class TruthInvalidationRecord(BaseModel):
    chapter_no: int = Field(ge=1)
    trigger_chapter_no: int = Field(ge=1)
    source_snapshot_id: str = Field(min_length=1)
    superseded_by_snapshot_id: str = Field(min_length=1)
    stale_snapshot_id: str = Field(min_length=1)
    current_snapshot_id: str = Field(min_length=1)
    source_truth_commit_receipt_id: str = Field(min_length=1)
    debt_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    affected_refs: list[str] = Field(default_factory=list)
    created_by_run_id: str = Field(min_length=1)


class PropagationDebtRecord(BaseModel):
    debt_id: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    trigger_chapter_no: int = Field(ge=1)
    stale_snapshot_id: str = Field(min_length=1)
    current_snapshot_id: str = Field(min_length=1)
    source_truth_commit_receipt_id: str = Field(min_length=1)
    source_truth_delta_artifact_id: str | None = None
    dependency_scope: str = Field(pattern="^(snapshot_only|targeted)$")
    reason_code: str = Field(min_length=1)
    blocking: bool = True
    status: str = Field(pattern="^(open|resolved|waived)$")
    dependency_hits: dict[str, list[str]] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class TruthIndexRecord(BaseModel):
    current_snapshot_id: str = Field(min_length=1)
    committed_through_chapter: int = Field(default=0, ge=0)
    latest_projection_version: int = Field(default=1, ge=1)
    latest_truth_commit_run_id: str = Field(min_length=1)


class EventTupleRecord(BaseModel):
    event_id: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    order_index: int = Field(ge=0)
    setting_id: str | None = None
    participant_ids: list[str] = Field(default_factory=list)
    action: str = Field(min_length=1)
    conflict: str | None = None
    twist: str | None = None
    outcome_fact_ids: list[str] = Field(default_factory=list)


class PlatformExportMetadataRecord(BaseModel):
    platform: str = Field(min_length=1)
    chapter_title: str | None = None
    volume_title: str | None = None
    summary: str | None = None
    keywords: list[str] = Field(default_factory=list)
    chapter_tags: list[str] = Field(default_factory=list)
    category: str | None = None
    subcategories: list[str] = Field(default_factory=list)
    serial_status: str | None = None
    language: str | None = None
    book_title_override: str | None = None
    intro: str | None = None
    custom_fields: dict[str, object] = Field(default_factory=dict)


class ExportPackageRecord(BaseModel):
    export_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    version: int = Field(ge=1)
    package_root: str = Field(min_length=1)
    chapter_file: str = Field(min_length=1)
    metadata_file: str = Field(min_length=1)
    manifest_file: str = Field(min_length=1)
    package_hash: str = Field(min_length=1)


class ExportManifestRecord(BaseModel):
    export_id: str = Field(min_length=1)
    book_id: str = Field(min_length=1)
    chapter_no: int = Field(ge=1)
    platform: str = Field(min_length=1)
    version: int = Field(ge=1)
    source_stage: str = Field(min_length=1)
    approval_receipt_artifact_id: str = Field(min_length=1)
    truth_commit_receipt_artifact_id: str = Field(min_length=1)
    draft_artifact_id: str = Field(min_length=1)
    settlement_artifact_id: str = Field(min_length=1)
    chapter_file_path: str = Field(min_length=1)
    chapter_file_sha256: str = Field(min_length=1)
    chapter_semantic_sha256: str = Field(min_length=1)
    metadata_artifact_id: str = Field(min_length=1)
    package_artifact_id: str = Field(min_length=1)
    previous_export_manifest_id: str | None = None
    revision_kind: str = Field(pattern="^(initial|metadata_only|surface_text)$")
    exported_at: datetime = Field(default_factory=utc_now)
    integrity_status: str = Field(pattern="^(ok|tampered)$")


class ExportDiffRecord(BaseModel):
    before_sha256: str = Field(min_length=1)
    after_sha256: str = Field(min_length=1)
    before_semantic_sha256: str = Field(min_length=1)
    after_semantic_sha256: str = Field(min_length=1)
    changed_line_count: int = Field(ge=0)
    diff_excerpt: list[str] = Field(default_factory=list)
    classification: str = Field(pattern="^(surface_text|semantic_change)$")


class BookExportProfileRecord(BaseModel):
    defaults: dict[str, PlatformExportMetadataRecord] = Field(default_factory=dict)
    chapter_overrides: dict[str, dict[str, PlatformExportMetadataRecord]] = Field(default_factory=dict)


class RevisionBriefRecord(BaseModel):
    gate_decision_artifact_id: str = Field(min_length=1)
    fix_targets: list[str] = Field(default_factory=list)
    must_not_touch: list[str] = Field(default_factory=list)
    risk_points: list[str] = Field(default_factory=list)
    mode: str = Field(default="standard", min_length=1)


class AdversarialEditRecord(BaseModel):
    source_settlement_artifact_id: str = Field(min_length=1)
    edit_instruction: str = Field(min_length=1)
    candidate_draft_artifact_id: str = Field(min_length=1)
    candidate_settlement_artifact_id: str = Field(min_length=1)
    reduction_stats: dict[str, int] = Field(default_factory=dict)
    kept: bool = False
    decision_reason_codes: list[str] = Field(default_factory=list)


class ReaderPanelRecord(BaseModel):
    chapter_no: int | None = Field(default=None, ge=1)
    chapter_slice: list[int] = Field(default_factory=list)
    panel_scope: str = Field(min_length=1)
    editor_findings: list[str] = Field(default_factory=list)
    genre_reader_findings: list[str] = Field(default_factory=list)
    writer_findings: list[str] = Field(default_factory=list)
    first_reader_findings: list[str] = Field(default_factory=list)
    momentum_loss: bool = False
    earned_ending: bool = True
    cut_candidate: list[str] = Field(default_factory=list)
    missing_scene: list[str] = Field(default_factory=list)
    thinnest_character: str | None = None
    aggregate_recommendation: str = Field(min_length=1)
    risk_flags: list[str] = Field(default_factory=list)


class StyleSignalRecord(BaseModel):
    chapter_no: int | None = Field(default=None, ge=1)
    reference_profile_ref: str | None = None
    drift_score: float = Field(default=0.0, ge=0.0)
    dominant_drift_axes: list[str] = Field(default_factory=list)
    recommended_action: str = Field(default="noop", min_length=1)


class ComparisonRecord(BaseModel):
    baseline_settlement_id: str = Field(min_length=1)
    candidate_settlement_id: str = Field(min_length=1)
    baseline_gate_id: str = Field(min_length=1)
    candidate_gate_id: str = Field(min_length=1)
    delta_overall: float
    delta_by_dimension: dict[str, float] = Field(default_factory=dict)
    critical_delta: int
    decision: str = Field(pattern="^(keep|rollback)$")
    reason_codes: list[str] = Field(default_factory=list)


class ApprovalReceiptRecord(BaseModel):
    draft_artifact_id: str = Field(min_length=1)
    published_path: str = Field(min_length=1)
    chapter_file_sha256: str = Field(min_length=1)
    chapter_semantic_sha256: str = Field(min_length=1)
    settlement_artifact_id: str = Field(min_length=1)
    truth_commit_receipt_artifact_id: str = Field(min_length=1)
