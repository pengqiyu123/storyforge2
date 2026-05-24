# Workspace Control-Plane vs StoryForge2 Mapping

## Summary

This document maps `storyforge/workspace/writing-control-plane.md` to the current `storyforge2` chapter lifecycle.

Purpose:

- identify which control points are already covered by `storyforge2`
- identify gaps that should guide PLAN6 follow-up work
- avoid rebuilding `workspace` flow verbatim inside `storyforge2`

This is a **diagnostic mapping**, not a compatibility spec.

## Mapping Table

| Workspace Step | Workspace Output / Guard | StoryForge2 Equivalent | Status | Gap / Follow-up |
|---|---|---|---|---|
| Step 0 项目基建 | story bible / outline / rules / state | `create_book` + truth index bootstrap + book/chapter state | Partial | `storyforge2` lacks explicit author-facing project bootstrap bundle |
| Step 1 读取上下文 | 16-file read set | `compose_chapter()` builds truth context slice | Partial | `storyforge2` reads truth and chapter artifacts, but not the richer workspace writing context files |
| Step 2 章节意图规划 | `intent.md` with beats / hook agenda / directives | `plan_chapter()` | Partial | current plan artifact is too thin; no explicit hook agenda / stale debt / mood directive layer |
| Step 3 编译运行时包 | `intent/context/rule-stack/writer-brief` | `compose_context` + `revision_brief` | Partial | no explicit `rule-stack` artifact; no author-facing runtime package quartet |
| Step 4 写前自检 | PRE_WRITE_CHECK 8项门控 | none before write | Missing | add a pre-write validation layer or compile-time checks before writer call |
| Step 5 Writer Agent 隔离写稿 | clean writer brief + context + inlined rules | `chapter_writer.py` + `write_chapter_draft()` | Covered | current writer path exists, but needs richer prompt inputs from PLAN6 assets |
| Step 6 确定性审计 | `chapter_audit.py` 8项检测 | `gate_runner` mechanical lane | Covered / Divergent | rules overlap but thresholds/pattern sets differ; merge target for A1 |
| Step 7 字数校准 | length normalize then re-audit | writer fail-fast at `<800` only | Missing | no dedicated soft target/normalizer stage yet |
| Step 8 审稿 | isolated review | `audit_chapter()` dual channel | Covered | `storyforge2` stronger than workspace here |
| Step 8.5 附加复核 | extra review gate | none explicit | Missing | could remain optional; not needed before PLAN6 A/B stabilize |
| Step 9 修订 | revise based on audit findings | `revise_chapter()` + `chapter_writer.generate_revision()` | Covered | now structurally present; needs better rule absorption from workspace |
| Step 10 Settler | settle / finalize candidate | `settle_chapter()` | Covered | `storyforge2` stronger due to truth basis tracking |
| Step 11 章节闭环 | final handoff & state continuity | `compare -> approve -> export` | Covered / Stronger | `storyforge2` stronger with compare/rollback/freeze/export |

## Where StoryForge2 Is Stronger

- explicit 13-stage state machine
- immutable truth snapshots
- truth reconcile and propagation invalidation
- compare / rollback / plateau / human review
- approve / export freeze semantics
- full pytest regression baseline

These are not present as first-class engine controls in `workspace`.

## High-Value Gaps To Absorb

The most useful missing controls from `workspace` are:

1. richer intent/planning structure before write
2. explicit runtime package semantics
3. pre-write validation checklist
4. softer length normalization policy
5. richer author-facing diagnostics vocabulary

## Guidance For PLAN6

Priority order:

1. absorb `chapter_audit.py` rules into `gate_runner` / `style_signal`
2. expand rule inputs for writer/revision from learning logs
3. treat `runtime/*.intent/context/rule-stack/writer-brief` as bridge samples, not as native internal schema
4. postpone any attempt to mirror the full 11-step control plane one-to-one
