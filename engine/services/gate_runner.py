from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
from statistics import mean

from pydantic import ValidationError

from engine.gates import LLMAuditorAdapter
from engine.providers.provider_config import has_configured_api_key
from engine.providers import ResponsesAPIProvider
from engine.schemas.artifact import (
    AuditIssue,
    AuditRecord,
    GateDecisionRecord,
    GatePolicy,
    MechanicalGateRecord,
    RuleCategory,
    RuleDefinition,
    RuleResult,
    RuleSeverity,
)
from engine.utils.chinese_text import (
    check_chinese_brackets,
    count_chinese_chars,
    list_sentence_start_repetition,
    paragraph_stats,
    split_chinese_sentences,
    surprise_word_density,
    vague_word_density,
)


CORE_DIMENSIONS = ("logic", "character", "hook", "pace")
PASS_OVERALL_THRESHOLD = 6.0
PASS_DIMENSION_THRESHOLD = 5.5
MIN_CHINESE_CHAR_COUNT = 800

REPORT_TERMS = (
    "信息边界",
    "核心矛盾",
    "风险评估",
    "最大化收益",
    "推进目标",
    "关键抓手",
    "阶段结论",
    "核心动机",
    "信息落差",
    "核心风险",
    "当前处境",
    "行为约束",
    "性格过滤",
    "情绪外化",
    "锚定效应",
    "沉没成本",
    "认知共鸣",
)
META_PATTERNS = (
    "作为作者",
    "作为读者",
    "本章将",
    "下一章会",
    "让我们看看",
    "总结一下",
    "下面开始",
    "到这里算是",
    "接下来就是",
    "后面会",
    "故事发展到了",
    "读者可能",
    "我们可以",
)
EXPLANATORY_PATTERNS = ("之所以", "换句话说", "也就是说", "其原因在于", "从某种意义上说")
TRANSITION_PATTERNS = (
    "与此同时",
    "另一方面",
    "同一时间",
    "紧接着",
    "随后",
    "下一刻",
    "然而",
    "不过",
    "尽管如此",
    "话虽如此",
    "但值得注意的是",
)
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("forbidden_token_scan", r"forbidden"),
    ("report_output_leak", r"审计报告|实施计划|输出要求"),
)


@dataclass(slots=True)
class GateInputBundle:
    book_id: str
    chapter_no: int
    revision_round: int
    settlement_artifact_id: str
    candidate_signature: str
    draft_text: str
    plan_summary: str
    compose_constraints: list[str]
    baseline_gate_summary: dict[str, object]
    revision_mode: str | None = None


@dataclass(slots=True)
class MechanicalContext:
    text: str
    lowered: str
    chinese_char_count: int
    paragraphs: list[str]
    sentences: list[str]
    paragraph_uniformity: float
    vague_density: float
    surprise_density: float
    repeated_sentence_start: dict[str, object]
    bracket_check: dict[str, object]


class FallbackAuditor:
    def review(self, bundle: GateInputBundle) -> AuditRecord:
        issues: list[AuditIssue] = []
        regressed = "回退标记" in bundle.draft_text or "worse regression" in bundle.draft_text.lower()
        if bundle.revision_round == 0:
            issues.append(
                AuditIssue(
                    severity="critical",
                    category="logic",
                    description="初稿仍需至少一轮独立修订",
                    suggestion="先收紧逻辑，再强化章尾钩子",
                )
            )
        if regressed:
            issues.append(
                AuditIssue(
                    severity="critical",
                    category="logic",
                    description="修订稿相对基线出现明显退化",
                    suggestion="恢复前一版有效逻辑线，再做局部修订",
                )
            )
        if regressed:
            scores = {"logic": 4.0, "character": 4.2, "hook": 4.4, "pace": 4.1}
        elif issues:
            scores = {"logic": 5.0, "character": 5.1, "hook": 5.2, "pace": 5.0}
        else:
            scores = {"logic": 6.2, "character": 6.1, "hook": 6.4, "pace": 6.3}
        overall = round(mean(scores.values()), 2)
        return AuditRecord(
            passed=not issues and overall >= PASS_OVERALL_THRESHOLD and all(
                score >= PASS_DIMENSION_THRESHOLD for score in scores.values()
            ),
            critical_count=sum(1 for issue in issues if issue.severity == "critical"),
            issues=issues,
            recommended_mode="rework" if issues else "accept",
            score_summary={"overall": overall, **scores},
        )


class GateRunner:
    """Dual-channel quality gate runner for the single-chapter lifecycle."""

    def __init__(self, auditor: object | None = None, *, enable_real_provider: bool = False) -> None:
        if auditor is not None:
            self.auditor = auditor
        elif enable_real_provider and has_configured_api_key():
            self.auditor = LLMAuditorAdapter(ResponsesAPIProvider())
        else:
            self.auditor = FallbackAuditor()
        self.fallback_auditor = FallbackAuditor()
        self.rule_registry = self._build_rule_registry()

    def evaluate(self, bundle: GateInputBundle) -> tuple[MechanicalGateRecord, AuditRecord, GateDecisionRecord]:
        with ThreadPoolExecutor(max_workers=2) as executor:
            mechanical_future = executor.submit(self._run_mechanical, bundle)
            audit_future = executor.submit(self._run_auditor, bundle)
            mechanical = mechanical_future.result()
            audit = audit_future.result()
        gate = self._aggregate(mechanical, audit)
        return mechanical, audit, gate

    @staticmethod
    def build_signature(text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()

    def _run_mechanical(self, bundle: GateInputBundle) -> MechanicalGateRecord:
        context = self._build_context(bundle.draft_text)
        results = [checker(context) for _, checker in self.rule_registry]
        blocking_rule_ids = [result.rule_id for result in results if result.blocking and not result.passed]
        warning_rule_ids = [result.rule_id for result in results if not result.blocking and not result.passed]
        severity_counts = {
            RuleSeverity.INFO.value: sum(1 for result in results if not result.passed and result.severity == RuleSeverity.INFO),
            RuleSeverity.WARNING.value: sum(
                1 for result in results if not result.passed and result.severity == RuleSeverity.WARNING
            ),
            RuleSeverity.CRITICAL.value: sum(
                1 for result in results if not result.passed and result.severity == RuleSeverity.CRITICAL
            ),
        }
        return MechanicalGateRecord(
            rule_results=results,
            blocked=bool(blocking_rule_ids),
            summary_counts={"total": len(results), "failed": sum(1 for result in results if not result.passed)},
            blocking_rule_ids=blocking_rule_ids,
            warning_rule_ids=warning_rule_ids,
            issue_counts_by_severity=severity_counts,
        )

    def _run_auditor(self, bundle: GateInputBundle) -> AuditRecord:
        raw = self.auditor.review(bundle)
        try:
            audit = AuditRecord.model_validate(raw)
        except ValidationError as exc:
            raise ValueError("auditor returned invalid audit schema") from exc
        for dimension in CORE_DIMENSIONS:
            if dimension not in audit.score_summary:
                raise ValueError(f"auditor missing required score dimension: {dimension}")
        if "overall" not in audit.score_summary:
            raise ValueError("auditor missing required overall score")
        return audit

    def _aggregate(self, mechanical: MechanicalGateRecord, audit: AuditRecord) -> GateDecisionRecord:
        overall = float(audit.score_summary["overall"])
        dimensions = {dimension: float(audit.score_summary[dimension]) for dimension in CORE_DIMENSIONS}
        reason_codes: list[str] = []
        if mechanical.blocked:
            reason_codes.extend(f"mechanical:{rule_id}" for rule_id in mechanical.blocking_rule_ids)
        if audit.critical_count > 0:
            reason_codes.extend(f"audit:{issue.category}" for issue in audit.issues if issue.severity == "critical")
        if overall < PASS_OVERALL_THRESHOLD:
            reason_codes.append("score:overall_below_threshold")
        for dimension, score in dimensions.items():
            if score < PASS_DIMENSION_THRESHOLD:
                reason_codes.append(f"score:{dimension}_below_threshold")
        passed = not mechanical.blocked and audit.critical_count == 0 and overall >= PASS_OVERALL_THRESHOLD and all(
            score >= PASS_DIMENSION_THRESHOLD for score in dimensions.values()
        )
        if passed:
            reason_codes.append("gate:passed")
        return GateDecisionRecord(
            passed=passed,
            overall_score=overall,
            critical_count=audit.critical_count,
            blocked_by_mechanical=mechanical.blocked,
            dimension_scores=dimensions,
            source_refs={},
            reason_codes=reason_codes,
        )

    def _build_rule_registry(self) -> list[tuple[RuleDefinition, object]]:
        return [
            (
                RuleDefinition(
                    rule_id="empty_text",
                    severity=RuleSeverity.CRITICAL,
                    category=RuleCategory.INTEGRITY,
                    policy=GatePolicy.BLOCK,
                    description="正文不能为空",
                ),
                self._check_empty_text,
            ),
            (
                RuleDefinition(
                    rule_id="below_min_word_count",
                    severity=RuleSeverity.CRITICAL,
                    category=RuleCategory.INTEGRITY,
                    policy=GatePolicy.BLOCK,
                    description="正文必须达到最小汉字数",
                    threshold=MIN_CHINESE_CHAR_COUNT,
                ),
                self._check_min_chinese_char_count,
            ),
            (
                RuleDefinition(
                    rule_id="unbalanced_quote_or_bracket",
                    severity=RuleSeverity.CRITICAL,
                    category=RuleCategory.STRUCTURE,
                    policy=GatePolicy.BLOCK,
                    description="引号与括号必须成对",
                ),
                self._check_balanced_pairs,
            ),
            (
                RuleDefinition(
                    rule_id="forbidden_patterns",
                    severity=RuleSeverity.CRITICAL,
                    category=RuleCategory.META,
                    policy=GatePolicy.BLOCK,
                    description="禁止模式不得出现在正文中",
                ),
                self._check_forbidden_patterns,
            ),
            (
                RuleDefinition(
                    rule_id="report_term_leak",
                    severity=RuleSeverity.WARNING,
                    category=RuleCategory.AI_TELL,
                    policy=GatePolicy.WARN,
                    description="报告术语泄漏",
                ),
                self._check_report_terms,
            ),
            (
                RuleDefinition(
                    rule_id="meta_narration_patterns",
                    severity=RuleSeverity.WARNING,
                    category=RuleCategory.META,
                    policy=GatePolicy.WARN,
                    description="元叙事口吻泄漏",
                ),
                self._check_meta_patterns,
            ),
            (
                RuleDefinition(
                    rule_id="explanatory_density",
                    severity=RuleSeverity.WARNING,
                    category=RuleCategory.STYLE,
                    policy=GatePolicy.WARN,
                    description="解释腔过密",
                    threshold=2,
                ),
                self._check_explanatory_patterns,
            ),
            (
                RuleDefinition(
                    rule_id="formulaic_transitions",
                    severity=RuleSeverity.WARNING,
                    category=RuleCategory.STYLE,
                    policy=GatePolicy.WARN,
                    description="套话式转场过多",
                    threshold=3,
                ),
                self._check_formulaic_transitions,
            ),
            (
                RuleDefinition(
                    rule_id="paragraph_uniformity",
                    severity=RuleSeverity.WARNING,
                    category=RuleCategory.STRUCTURE,
                    policy=GatePolicy.WARN,
                    description="段落长度过于均匀",
                    threshold=0.15,
                ),
                self._check_paragraph_uniformity,
            ),
            (
                RuleDefinition(
                    rule_id="hedge_word_density",
                    severity=RuleSeverity.WARNING,
                    category=RuleCategory.STYLE,
                    policy=GatePolicy.WARN,
                    description="模糊词密度过高",
                    threshold=3.0,
                ),
                self._check_hedge_words,
            ),
            (
                RuleDefinition(
                    rule_id="list_like_structure",
                    severity=RuleSeverity.WARNING,
                    category=RuleCategory.STRUCTURE,
                    policy=GatePolicy.WARN,
                    description="句首重复导致列表腔",
                    threshold=3,
                ),
                self._check_list_like_structure,
            ),
            (
                RuleDefinition(
                    rule_id="surprise_marker_density",
                    severity=RuleSeverity.WARNING,
                    category=RuleCategory.AI_TELL,
                    policy=GatePolicy.WARN,
                    description="惊讶词密度过高",
                    threshold=35.0,
                ),
                self._check_surprise_markers,
            ),
        ]

    @staticmethod
    def _build_context(text: str) -> MechanicalContext:
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
        paragraph_metrics = paragraph_stats(text)
        return MechanicalContext(
            text=text,
            lowered=text.lower(),
            chinese_char_count=count_chinese_chars(text),
            paragraphs=paragraphs,
            sentences=split_chinese_sentences(text),
            paragraph_uniformity=float(paragraph_metrics["uniformity"]),
            vague_density=vague_word_density(text),
            surprise_density=surprise_word_density(text),
            repeated_sentence_start=list_sentence_start_repetition(text),
            bracket_check=check_chinese_brackets(text),
        )

    def _rule_result(
        self,
        *,
        definition: RuleDefinition,
        passed: bool,
        message: str,
        observed: float | int | str | None = None,
        evidence: list[str] | None = None,
    ) -> RuleResult:
        return RuleResult(
            rule_id=definition.rule_id,
            passed=passed,
            message=message,
            severity=definition.severity,
            category=definition.category,
            blocking=definition.policy == GatePolicy.BLOCK,
            observed=observed,
            threshold=definition.threshold,
            evidence=evidence or [],
        )

    def _definition(self, rule_id: str) -> RuleDefinition:
        for definition, _ in self.rule_registry:
            if definition.rule_id == rule_id:
                return definition
        raise KeyError(rule_id)

    def _check_empty_text(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("empty_text")
        passed = bool(context.text.strip())
        return self._rule_result(
            definition=definition,
            passed=passed,
            message="正文非空" if passed else "正文为空",
            observed=context.chinese_char_count,
        )

    def _check_min_chinese_char_count(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("below_min_word_count")
        passed = context.chinese_char_count >= int(definition.threshold)
        return self._rule_result(
            definition=definition,
            passed=passed,
            message="汉字数达到下限" if passed else "汉字数低于最小阈值",
            observed=context.chinese_char_count,
        )

    def _check_balanced_pairs(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("unbalanced_quote_or_bracket")
        passed = bool(context.bracket_check.get("balanced"))
        return self._rule_result(
            definition=definition,
            passed=passed,
            message="引号括号配对正常" if passed else "存在未配平的引号或括号",
            observed=len(context.bracket_check.get("mismatches", [])),
            evidence=[str(item) for item in context.bracket_check.get("mismatches", [])],
        )

    def _check_forbidden_patterns(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("forbidden_patterns")
        hits: list[str] = []
        for name, pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, context.lowered):
                hits.append(name)
        return self._rule_result(
            definition=definition,
            passed=not hits,
            message="未命中禁用模式" if not hits else "正文命中禁用模式",
            observed=len(hits),
            evidence=hits,
        )

    def _check_report_terms(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("report_term_leak")
        hits = [term for term in REPORT_TERMS if term in context.text]
        return self._rule_result(
            definition=definition,
            passed=not hits,
            message="未发现报告术语" if not hits else "发现报告术语泄漏",
            observed=len(hits),
            evidence=hits,
        )

    def _check_meta_patterns(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("meta_narration_patterns")
        hits = [pattern for pattern in META_PATTERNS if pattern in context.text]
        return self._rule_result(
            definition=definition,
            passed=not hits,
            message="未发现元叙事口吻" if not hits else "发现元叙事口吻",
            observed=len(hits),
            evidence=hits,
        )

    def _check_explanatory_patterns(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("explanatory_density")
        hits = [pattern for pattern in EXPLANATORY_PATTERNS if pattern in context.text]
        passed = len(hits) < int(definition.threshold)
        return self._rule_result(
            definition=definition,
            passed=passed,
            message="解释腔控制正常" if passed else "解释腔偏重",
            observed=len(hits),
            evidence=hits,
        )

    def _check_formulaic_transitions(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("formulaic_transitions")
        repeated = [pattern for pattern in TRANSITION_PATTERNS if context.text.count(pattern) >= int(definition.threshold)]
        return self._rule_result(
            definition=definition,
            passed=not repeated,
            message="转场表达正常" if not repeated else "套话式转场过多",
            observed=len(repeated),
            evidence=repeated,
        )

    def _check_paragraph_uniformity(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("paragraph_uniformity")
        if len(context.paragraphs) < 3:
            return self._rule_result(
                definition=definition,
                passed=True,
                message="段落数量不足，跳过均匀度检查",
                observed=len(context.paragraphs),
            )
        passed = context.paragraph_uniformity >= float(definition.threshold)
        return self._rule_result(
            definition=definition,
            passed=passed,
            message="段落节奏有变化" if passed else "段落长度过于齐整",
            observed=round(context.paragraph_uniformity, 4),
        )

    def _check_hedge_words(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("hedge_word_density")
        passed = context.vague_density <= float(definition.threshold)
        return self._rule_result(
            definition=definition,
            passed=passed,
            message="模糊词密度正常" if passed else "模糊词密度偏高",
            observed=context.vague_density,
        )

    def _check_list_like_structure(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("list_like_structure")
        repetition = context.repeated_sentence_start
        passed = not bool(repetition.get("detected"))
        return self._rule_result(
            definition=definition,
            passed=passed,
            message="句首变化正常" if passed else "句首重复形成列表腔",
            observed=int(repetition.get("count", 0)),
            evidence=[str(repetition.get("pattern", ""))] if repetition.get("pattern") else [],
        )

    def _check_surprise_markers(self, context: MechanicalContext) -> RuleResult:
        definition = self._definition("surprise_marker_density")
        passed = context.surprise_density <= float(definition.threshold)
        return self._rule_result(
            definition=definition,
            passed=passed,
            message="惊讶词密度正常" if passed else "惊讶词或强调词偏多",
            observed=context.surprise_density,
        )
