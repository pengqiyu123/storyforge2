# Workspace Rule Gap Analysis

## Summary

This document classifies the main `workspace` rule assets against the current `storyforge2` engine.

Sources reviewed:

- `workspace/glm-learning-log.md`
- `workspace/codex-learning-log.md`
- `workspace/scripts/chapter_audit.py`

Classification buckets:

- covered
- missing and worth absorbing
- conflicting / requires judgment

This is the priority source for PLAN6 A1 work.

## A. `chapter_audit.py` Rule Mapping

| Workspace Rule | Current StoryForge2 Coverage | Classification | Planned Landing |
|---|---|---|---|
| paragraph_uniformity | yes | Covered / tune | `gate_runner` warning threshold alignment |
| hedge_word_density | yes | Covered / tune | `gate_runner` warning threshold alignment |
| formulaic_transitions | yes | Covered / tune | `gate_runner` warning threshold alignment |
| list_like_structure | yes | Covered / tune | `gate_runner` warning threshold alignment |
| surprise_marker_density | yes | Covered / tune | `gate_runner` warning threshold alignment |
| forbidden_patterns | partial | Missing / absorb | extend `FORBIDDEN_PATTERNS` with workspace patterns |
| report_term_leak | yes | Covered / expand | merge workspace report term list into `REPORT_TERMS` |
| meta_narration_patterns | partial | Missing / absorb | merge workspace meta phrases into `META_PATTERNS` |

Immediate conclusion:

- the 8-point audit does **not** require a second auditor path
- it should be merged into the existing `gate_runner` rule universe

## B. GLM Rule Coverage (G1-G7)

| Rule | Current Coverage | Classification | Planned Landing |
|---|---|---|---|
| G1 system early-phase boundary | weak | Missing | writer/revision prompt constraint |
| G2 no ungrounded terminology | weak | Missing | writer prompt + auditor concern |
| G3 don't patch one sentence in isolation | partial | Missing | revision prompt instruction |
| G4 every scene needs protagonist agency | weak | Missing | writer/revision prompt constraint |
| G5 chapter ending needs role logic | weak | Missing | writer/revision prompt + audit concern |
| G6 sensory before judgment | weak | Missing | writer/revision prompt constraint |
| G7 minimum beat density | weak | Missing | writer prompt + audit concern |

Immediate conclusion:

- G rules are mostly **not** mechanical
- they belong primarily to writer/revision prompt constraints
- some should also be reflected in auditor emphasis

## C. Codex Rule Coverage (selected C-rules)

| Rule Group | Current Coverage | Classification | Planned Landing |
|---|---|---|---|
| C1-C4 close POV / reaction-first | weak | Missing | writer/revision prompt |
| C5 short paragraphs | partial | Missing / soft | style signal + prompt |
| C6-C8 first-body-contact / utility environment | weak | Missing | writer prompt |
| C9 sensory texture | weak | Missing | writer prompt |
| C10 direct inner voice | weak | Missing | writer prompt |
| C11 metaphor relevance | weak | Missing | writer prompt + style signal |
| C13 suspense via experience | weak | Missing | writer prompt + audit concern |
| C14 protagonist agency | weak | Missing | writer/revision prompt |
| C16 ending logic | weak | Missing | writer/revision prompt + audit concern |
| C18-C19 system/body reaction / micro-drama | weak | Missing | writer prompt |
| C20 dialogue carries info + attitude | weak | Missing | writer prompt + style signal |
| C21 ending atmosphere | weak | Missing | revision prompt |
| C23-C25 emotional wording / precise action adverbs | weak | Missing | writer prompt |
| HOT#3 anti-AI skeletons | partial | Missing / absorb | forbidden/meta/style diagnostics |
| HOT#4 maintain inner-voice perspective | weak | Missing | writer/revision prompt |
| HOT#6 action as thought lead-in | weak | Missing | writer prompt |
| HOT#7 do not repeat at chapter ending | partial | Missing | revision prompt + audit concern |

Immediate conclusion:

- Codex rules are mostly **prompt-side craft rules**
- only a small subset should become mechanical checks
- some can be observed via `style_signal`, but should not hard-block

## D. Conflicts / Needs Judgment

These need explicit judgment before absorption:

1. paragraph shortness vs chapter voice variation
   - too aggressive mechanicalization may punish otherwise strong prose
   - default landing: prompt + style, not hard block

2. direct inner voice formatting
   - workspace prefers stronger in-head immediacy
   - `storyforge2` should not hard-require quote-delimited inner monologue
   - default landing: prompt, not mechanical rule

3. system-specific protagonist constraints
   - some G/C rules are tied to `我是路人甲`
   - absorb only as generic narrative heuristics unless book-specific bridge is explicitly selected

## E. Priority Absorption Order

Phase A1 priority order:

1. merge `chapter_audit.py` lexical/pattern sets into existing gate mechanical rules
2. add G1-G7 as writer/revision prompt constraints
3. add selected Codex craft rules to writer/revision prompt
4. map a small subset into `style_signal` soft diagnostics

Do not do yet:

- full rule DSL
- automatic learning-log parser
- book-specific behavior branches in engine core
