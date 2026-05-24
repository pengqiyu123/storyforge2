# Workspace Read-Only Diagnostics

## Purpose

This document defines the PLAN6 A2 bridge boundary between:

- `storyforge/workspace`
- `storyforge2`

The bridge is **read-only**.

`storyforge2` may:

- read chapter markdown from `workspace/books`
- run mechanical gate / audit / style / truth diagnostics
- emit diagnostic results

`storyforge2` may not:

- write back to `workspace`
- sync state bidirectionally
- treat workspace internal files as native engine schema

## Recommended First Samples

Priority order:

1. `workspace/books/我是路人甲`
   - richer runtime/state samples
   - useful for intent/context/rule-stack inspection
2. `workspace/books/我成了仇人的外挂`
   - stronger finished-chapter corpus
   - useful for gate/style/audit read-only diagnosis

## Expected Outputs

For each diagnosed chapter, the bridge should return:

- chapter number
- mechanical gate result
- audit result
- gate decision
- optional style signal
- truth probe

These results are diagnostic only and must not mutate workspace assets.
