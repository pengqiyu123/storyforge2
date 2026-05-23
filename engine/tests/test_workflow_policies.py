import unittest

from engine.schemas.artifact import GateDecisionRecord
from engine.services.workflow_policies import decide_comparison, next_plateau_counter


def _gate(*, passed: bool, critical_count: int, blocked_by_mechanical: bool, overall_score: float, dimension_scores: dict[str, float]) -> GateDecisionRecord:
    return GateDecisionRecord(
        passed=passed,
        overall_score=overall_score,
        dimension_scores=dimension_scores,
        critical_count=critical_count,
        blocked_by_mechanical=blocked_by_mechanical,
        reason_codes=[],
        source_refs={},
    )


class WorkflowPoliciesTests(unittest.TestCase):
    def test_decide_comparison_rolls_back_on_critical_increase(self) -> None:
        result = decide_comparison(
            _gate(passed=False, critical_count=1, blocked_by_mechanical=False, overall_score=6.0, dimension_scores={"logic": 6.0}),
            _gate(passed=False, critical_count=2, blocked_by_mechanical=False, overall_score=6.3, dimension_scores={"logic": 6.3}),
            0.3,
            {"logic": 0.3},
            1,
        )
        self.assertEqual(result["decision"], "rollback")

    def test_decide_comparison_rolls_back_on_mechanical_regression(self) -> None:
        result = decide_comparison(
            _gate(passed=False, critical_count=1, blocked_by_mechanical=False, overall_score=6.0, dimension_scores={"logic": 6.0}),
            _gate(passed=False, critical_count=1, blocked_by_mechanical=True, overall_score=6.2, dimension_scores={"logic": 6.2}),
            0.2,
            {"logic": 0.2},
            0,
        )
        self.assertEqual(result["decision"], "rollback")

    def test_decide_comparison_keeps_when_candidate_became_passed(self) -> None:
        result = decide_comparison(
            _gate(passed=False, critical_count=1, blocked_by_mechanical=False, overall_score=6.0, dimension_scores={"logic": 6.0}),
            _gate(passed=True, critical_count=0, blocked_by_mechanical=False, overall_score=7.0, dimension_scores={"logic": 7.0}),
            1.0,
            {"logic": 1.0},
            -1,
        )
        self.assertEqual(result["decision"], "keep")

    def test_decide_comparison_keeps_when_score_improved(self) -> None:
        result = decide_comparison(
            _gate(passed=False, critical_count=1, blocked_by_mechanical=False, overall_score=6.0, dimension_scores={"logic": 6.0, "pace": 6.0}),
            _gate(passed=False, critical_count=1, blocked_by_mechanical=False, overall_score=6.8, dimension_scores={"logic": 6.6, "pace": 6.7}),
            0.8,
            {"logic": 0.6, "pace": 0.7},
            0,
        )
        self.assertEqual(result["decision"], "keep")

    def test_decide_comparison_rolls_back_when_improvement_insufficient(self) -> None:
        result = decide_comparison(
            _gate(passed=False, critical_count=1, blocked_by_mechanical=False, overall_score=6.0, dimension_scores={"logic": 6.0}),
            _gate(passed=False, critical_count=1, blocked_by_mechanical=False, overall_score=6.1, dimension_scores={"logic": 6.1}),
            0.1,
            {"logic": 0.1},
            0,
        )
        self.assertEqual(result["decision"], "rollback")

    def test_next_plateau_counter_increments_when_gain_is_small(self) -> None:
        result = next_plateau_counter(
            current_counter=1,
            candidate_gate=_gate(passed=True, critical_count=0, blocked_by_mechanical=False, overall_score=7.1, dimension_scores={"logic": 7.1}),
            delta_overall=0.2,
            critical_delta=0,
            plateau_delta=0.5,
        )
        self.assertEqual(result, 2)

    def test_next_plateau_counter_resets_when_gain_is_large_or_not_passed(self) -> None:
        large_gain = next_plateau_counter(
            current_counter=2,
            candidate_gate=_gate(passed=True, critical_count=0, blocked_by_mechanical=False, overall_score=7.5, dimension_scores={"logic": 7.5}),
            delta_overall=0.8,
            critical_delta=0,
            plateau_delta=0.5,
        )
        failed_gate = next_plateau_counter(
            current_counter=2,
            candidate_gate=_gate(passed=False, critical_count=1, blocked_by_mechanical=False, overall_score=6.4, dimension_scores={"logic": 6.4}),
            delta_overall=0.1,
            critical_delta=0,
            plateau_delta=0.5,
        )
        self.assertEqual(large_gain, 0)
        self.assertEqual(failed_gate, 0)
