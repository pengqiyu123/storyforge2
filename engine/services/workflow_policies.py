from __future__ import annotations

from engine.schemas.artifact import GateDecisionRecord


def decide_comparison(
    baseline_gate: GateDecisionRecord,
    candidate_gate: GateDecisionRecord,
    delta_overall: float,
    delta_by_dimension: dict[str, float],
    critical_delta: int,
) -> dict:
    if candidate_gate.critical_count > baseline_gate.critical_count:
        return {"decision": "rollback", "reason_codes": ["critical_increase"]}
    if candidate_gate.blocked_by_mechanical and not baseline_gate.blocked_by_mechanical:
        return {"decision": "rollback", "reason_codes": ["mechanical_regression"]}
    if any(value < -0.5 for value in delta_by_dimension.values()):
        return {"decision": "rollback", "reason_codes": ["core_dimension_drop"]}
    if candidate_gate.passed and baseline_gate.passed:
        return {"decision": "keep", "reason_codes": ["maintain_passed_quality"]}
    if candidate_gate.passed and not baseline_gate.passed:
        return {"decision": "keep", "reason_codes": ["became_passed"]}
    if critical_delta < 0 and not candidate_gate.blocked_by_mechanical:
        return {"decision": "keep", "reason_codes": ["critical_reduced"]}
    if delta_overall >= 0.5 and all(value >= -0.5 for value in delta_by_dimension.values()):
        return {"decision": "keep", "reason_codes": ["score_improved"]}
    return {"decision": "rollback", "reason_codes": ["insufficient_improvement"]}


def next_plateau_counter(
    *,
    current_counter: int,
    candidate_gate: GateDecisionRecord,
    delta_overall: float,
    critical_delta: int,
    plateau_delta: float,
) -> int:
    if candidate_gate.passed and delta_overall < plateau_delta and critical_delta >= 0:
        return current_counter + 1
    return 0
