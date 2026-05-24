# PLAN6 B-Track Sequential Validation Summary

## Sample

- canonical book id: `plan6-seq-run`
- execution mode: sequential `chapter-full-cycle`
- provider mode: real writer / real auditor / real truth extractor

## Final Result Snapshot

| Chapter | Final Outcome | Final Stage | Notes |
|---|---|---|---|
| 1 | `exported` | `exported` | clean pass |
| 2 | `exported` | `exported` | clean pass |
| 3 | `exported` | `exported` | clean pass |
| 4 | `exported` | `exported` | clean pass |
| 5 | `exported` | `exported` | passed after reconcile and commit fixes |

## What Was Fixed During B-Track

The sequential run exposed and validated three real multi-chapter blockers:

1. stale truth auto-recovery
   - downstream chapters could be invalidated after upstream truth commit
   - batch/readiness handling was fixed so stale chapters can be reset and continue safely

2. over-strict `character_location_conflict`
   - normal cross-chapter movement was being treated as blocking
   - severity was relaxed to `warning`, while other truth conflict categories stayed blocking

3. truth commit relationship-edge type mismatch
   - relationship updates reached commit as `RelationshipEdgeRecord`
   - snapshot builder expected dict-like items during dedupe
   - commit path was fixed so relationship edges serialize consistently before payload merge

## What This Means

B-track has now demonstrated:

- a stable sequential truth/export chain through chapter 5
- no system-level stale truth stop in the 1-5 chapter run
- no unresolved truth blocking conflict left in the validated sample

This is no longer a “single chapter smoke” result.
It is a **real 5-chapter sequential production sample**.

## Immediate Next Focus

Do not jump to UI yet.

The next useful questions are now:

1. Does the sequential chain stay stable through chapter 6-10?
2. Does `compare -> keep / rollback` distribution remain healthy as chapter count grows?
3. Does `style_drift_axes` start accumulating after chapter 5?
4. Do new truth blocking conflicts appear again once entity/hook graph gets denser?

Only after that should C-track product surface planning become the main priority.
