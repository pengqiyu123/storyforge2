# StoryForge2 Engine Contracts

This document is the current contract baseline for the `storyforge2` mainline.  
Its purpose is to lock terminology, public surface, and authoritative type groups before later phases continue.

This is a **contract reference**, not a feature roadmap.  
Roadmap and sequencing remain in [`PLAN.md`](../../PLAN.md).

## 1. Canonical Mainline

The single-chapter canonical workflow is:

`plan -> compose -> draft -> settle -> audit -> revise -> re-audit -> compare -> approve`

Supporting runtime chains:

- truth snapshot / truth delta / truth commit
- propagation debt / truth invalidation
- export / verify export integrity / micro revise export
- batch orchestration

No layer should introduce privileged shortcuts around this chain.

## 2. Chapter Stage Contract

Authoritative source:

- `storyforge2/engine/schemas/chapter.py`

Canonical stage enum:

- `planned`
- `composed`
- `drafted`
- `settled`
- `audited_passed`
- `audited_failed`
- `revising`
- `approved`
- `exported`
- `blocked`
- `human_review_required`
- `rolled_back`
- `invalidated`

Notes:

- `approved` is the frozen formal chapter state
- `exported` extends freeze semantics; it does not reopen chapter mutation
- `invalidated` means a previously consumed truth basis is stale and the chapter must re-enter from `planned`

## 3. Artifact Contract

Authoritative source:

- `storyforge2/engine/schemas/artifact.py`

Canonical artifact groups:

- Chapter workflow:
  - `plan`
  - `compose_context`
  - `draft`
  - `settlement`
  - `revision`
  - `revision_brief`
  - `comparison`
  - `approval_receipt`
- Quality gate:
  - `mechanical_gate`
  - `audit`
  - `gate_decision`
  - `chapter_quality`
- Truth:
  - `truth_snapshot`
  - `truth_delta`
  - `truth_reconcile`
  - `truth_commit_receipt`
  - `truth_projection`
  - `truth_invalidation`
  - `propagation_debt`
- Export:
  - `export_metadata`
  - `export_package`
  - `export_manifest`
  - `export_diff`
- Batch quality signals:
  - `adversarial_edit`
  - `reader_panel`
  - `style_signal`

Canonical naming rules:

- use `settlement`, not “settled draft” or “audit snapshot”
- use `gate_decision`, not “gate result” or “final audit verdict”
- use `chapter_quality`, not “quality summary” as a schema name
- use `truth_snapshot`, `truth_delta`, `truth_commit_receipt`
- use `propagation_debt`, not “stale dependency record”
- use `batch_run`, `batch_item`, `batch_checkpoint`

## 4. Truth Contract

Authoritative sources:

- `storyforge2/engine/schemas/artifact.py`
- JSON truth payloads under `books/<book-id>/state/truth/`

Canonical truth records:

- `TruthSnapshotRecord`
- `TruthDeltaRecord`
- `TruthCommitReceiptRecord`
- `TruthInvalidationRecord`
- `PropagationDebtRecord`
- supporting ledger records:
  - `CanonFactRecord`
  - `CharacterStateRecord`
  - `RelationshipEdgeRecord`
  - `HookClueRecord`
  - `ChapterFactRecord`

Truth contract rules:

- committed truth lives in JSON and is authoritative
- candidate truth must remain in `truth_delta` until approve-time commit
- truth snapshots are immutable historical state
- downstream invalidation depends on committed truth head changes

## 5. Quality Gate Contract

Authoritative source:

- `storyforge2/engine/schemas/artifact.py`

Canonical quality records:

- `MechanicalGateRecord`
- `AuditRecord`
- `GateDecisionRecord`
- `ChapterQualityRecord`

Quality contract rules:

- gate is dual-channel:
  - mechanical evaluator
  - isolated auditor
- `gate_decision` is the unified runtime decision object
- `chapter_quality` is the aggregate record used for comparison and later inspection

## 6. Batch Contract

Authoritative sources:

- `storyforge2/engine/schemas/batch.py`
- `storyforge2/engine/schemas/artifact.py` for signal records

Canonical batch records:

- `BatchRunRecord`
- `BatchItemRecord`
- `BatchCheckpointRecord`
- `BatchSummaryRecord`

Supporting batch signal records:

- `ReaderPanelRecord`
- `AdversarialEditRecord`
- `StyleSignalRecord`

Batch contract rules:

- batch scheduling must delegate lifecycle actions back to `StoryEngineService`
- batch may orchestrate stages, but does not own chapter truth
- checkpoint is the canonical batch recovery marker

## 7. Public Surface Matrix

Public surface is defined as:

- CLI commands in `storyforge2/engine/cli/main.py`
- public methods on `StoryEngineService`
- public methods on `BatchOrchestratorService`

Internal helpers are:

- any `_...` method
- low-level implementation helpers not intended as product-facing contracts

### 7.1 Formal CLI Surface

Current debug CLI commands:

- `create-book`
- `init-chapter`
- `show-book`
- `show-chapter`
- `transition`
- `rebuild-db`
- `batch-create`
- `batch-start`
- `batch-status`
- `batch-resume`
- `batch-retry-item`
- `batch-checkpoint-review`

CLI interpretation:

- all listed commands are currently exposed
- `transition` is a low-level debug/ops command, not a preferred product entry

### 7.2 StoryEngineService Public Surface

Current public methods intended as formal service surface:

- `create_book`
- `get_book`
- `list_batch_runs`
- `get_truth_head`
- `init_chapter`
- `get_chapter_status`
- `list_propagation_debts`
- `get_chapter_truth_freshness`
- `reset_invalidated_chapter`
- `invalidate_downstream`
- `transition_chapter`
- `rollback_chapter`
- `rebuild_read_model`
- `plan_chapter`
- `compose_chapter`
- `write_chapter_draft`
- `settle_chapter`
- `audit_chapter`
- `revise_chapter`
- `compare_candidate`
- `approve_chapter`
- `set_export_profile`
- `set_chapter_export_metadata`
- `export_chapter`
- `verify_export_integrity`
- `micro_revise_exported_chapter`

Public but low-level/operational:

- `start_run`
- `register_artifact`
- `finish_run`
- `mark_invalidated`
- `mark_blocked`

Interpretation:

- chapter lifecycle, truth inspection, invalidation reset, export, and rebuild are all formally available at service level
- low-level run/artifact helpers exist but should not be used as the primary product workflow surface

### 7.3 BatchOrchestratorService Public Surface

Current formal batch methods:

- `create_batch_run`
- `start_batch_run`
- `resume_batch_run`
- `retry_batch_item`
- `get_batch_run`
- `list_batch_runs`
- `run_checkpoint_review`

Experimental:

- `run_adversarial_edit`

Interpretation:

- `run_adversarial_edit` exists as API shape but is not yet implemented as a stable capability

### 7.4 Current Compatibility Seams

The following `StoryEngineService` attributes/helpers remain intentionally available as transitional compatibility seams:

- `service.repo`
- `service.gate_runner`
- `service.truth_extractor`
- `service._load_artifact_payload(...)`
- `service._optional_artifact_payload(...)`

Interpretation:

- these seams remain allowed for current tests, batch orchestration, and intent plumbing
- they are compatibility surfaces, not preferred extension points for new product behavior
- future cleanup may narrow them only after callers are migrated

## 8. Foundation Baseline

Current baseline test command:

```powershell
cd storyforge2
python -m unittest discover -s engine/tests -p "test_*.py"
```

Current baseline status at planning time:

- engine suite passes from the `storyforge2` root
- the suite covers:
  - lifecycle and state transitions
  - service guards and single-chapter loop
  - Chinese text helpers
  - LLM provider behavior
  - gate runner behavior
  - truth extractor behavior
  - propagation debt behavior
  - export pipeline behavior
  - batch orchestration behavior
  - storage roundtrip behavior

## 9. Foundation Interpretation

For the current “foundation hardening” phase:

- Phase 0 infrastructure is considered already landed in usable form
- foundation work means:
  - lock contracts
  - preserve naming stability
  - keep tests reproducible
  - clear phase-1 runtime hard bugs without architecture drift

Until this contract changes deliberately, later work should build on these names and surfaces instead of introducing parallel vocabularies.
