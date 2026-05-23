from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from engine.providers.llm_provider import LLMProvider
from engine.schemas.artifact import AuditIssue, AuditRecord


AUDIT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "critical_count": {"type": "integer"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string"},
                    "category": {"type": "string"},
                    "description": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["severity", "category", "description"],
            },
        },
        "recommended_mode": {"type": "string"},
        "score_summary": {
            "type": "object",
            "properties": {
                "overall": {"type": "number"},
                "logic": {"type": "number"},
                "character": {"type": "number"},
                "hook": {"type": "number"},
                "pace": {"type": "number"},
            },
            "required": ["overall", "logic", "character", "hook", "pace"],
        },
    },
    "required": ["passed", "critical_count", "issues", "recommended_mode", "score_summary"],
}


class LLMAuditorAdapter:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def review(self, bundle) -> AuditRecord:
        payload = self.provider.generate_json(
            "chapter_audit",
            "你是一个与作者完全隔离的独立中文小说审稿人。请只输出结构化 JSON。",
            {
                "book_id": bundle.book_id,
                "chapter_no": bundle.chapter_no,
                "revision_round": bundle.revision_round,
                "draft_text": bundle.draft_text,
                "plan_summary": bundle.plan_summary,
                "constraints": bundle.compose_constraints,
                "baseline_gate_summary": bundle.baseline_gate_summary,
                "revision_mode": bundle.revision_mode,
            },
            AUDIT_RESPONSE_SCHEMA,
        )
        payload = self._normalize_payload(payload)
        if "error" in payload:
            return self._fallback_fail(f"audit_provider_error:{payload['error']}")
        try:
            audit = AuditRecord.model_validate(payload)
        except ValidationError:
            return self._fallback_fail("audit_invalid_schema")
        required = {"overall", "logic", "character", "hook", "pace"}
        if not required.issubset(audit.score_summary):
            return self._fallback_fail("audit_missing_core_dimensions")
        return audit

    def _normalize_payload(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or "error" in payload:
            return payload
        score_summary = payload.get("score_summary")
        if isinstance(score_summary, dict):
            normalized_scores: dict[str, float] = {}
            numeric_values: list[float] = []
            for key, value in score_summary.items():
                if isinstance(value, (int, float)):
                    numeric = float(value)
                    normalized_scores[key] = numeric
                    numeric_values.append(numeric)
            if numeric_values and max(numeric_values) > 10:
                normalized_scores = {key: round(value / 10.0, 2) for key, value in normalized_scores.items()}
            overall = normalized_scores.get("overall", 0.0)
            can_infer_core_dimensions = any(
                key in normalized_scores
                for key in {
                    "clarity",
                    "tone_fit",
                    "character_motivation",
                    "voice",
                    "risk",
                    "readability",
                    "atmosphere",
                    "tension",
                }
            )
            if can_infer_core_dimensions:
                normalized_scores["logic"] = normalized_scores.get("logic", normalized_scores.get("clarity", overall))
                normalized_scores["character"] = normalized_scores.get(
                    "character",
                    normalized_scores.get(
                        "character_motivation",
                        normalized_scores.get("voice", normalized_scores.get("tone_fit", overall)),
                    ),
                )
                normalized_scores["hook"] = normalized_scores.get(
                    "hook",
                    normalized_scores.get(
                        "atmosphere",
                        normalized_scores.get("tension", normalized_scores.get("risk", overall)),
                    ),
                )
                normalized_scores["pace"] = normalized_scores.get(
                    "pace", normalized_scores.get("readability", normalized_scores.get("risk", overall))
                )
            payload["score_summary"] = normalized_scores
        issues = payload.get("issues")
        if isinstance(issues, list):
            normalized_issues: list[dict[str, Any]] = []
            for issue in issues:
                if isinstance(issue, dict):
                    severity = str(issue.get("severity", "warning")).strip().lower()
                    severity = {
                        "minor": "warning",
                        "major": "critical",
                    }.get(severity, severity)
                    if severity not in {"info", "warning", "critical"}:
                        severity = "warning"
                    normalized_issues.append(
                        {
                            "severity": severity,
                            "category": issue.get("category", issue.get("title", "general")),
                            "description": issue.get(
                                "description",
                                issue.get("detail", issue.get("message", issue.get("title", "llm_issue"))),
                            ),
                            "suggestion": issue.get("suggestion"),
                        }
                    )
                elif isinstance(issue, str):
                    normalized_issues.append(
                        {
                            "severity": "warning",
                            "category": "general",
                            "description": issue,
                            "suggestion": None,
                        }
                    )
            payload["issues"] = normalized_issues
        if isinstance(payload.get("recommended_mode"), str):
            mode_map = {
                "通过": "accept",
                "放行": "accept",
                "直接放行": "accept",
                "light_polish": "accept",
                "polish": "accept",
                "light_touch": "surgical",
                "轻修": "surgical",
                "小修": "surgical",
                "重写": "rework",
                "人工复核": "human_review",
            }
            payload["recommended_mode"] = mode_map.get(payload["recommended_mode"], payload["recommended_mode"])
        return payload

    @staticmethod
    def _fallback_fail(reason: str) -> AuditRecord:
        return AuditRecord(
            passed=False,
            critical_count=1,
            issues=[
                AuditIssue(
                    severity="critical",
                    category="audit_runtime",
                    description=reason,
                    suggestion="retry_audit_or_human_review",
                )
            ],
            recommended_mode="human_review",
            score_summary={"overall": 0.0, "logic": 0.0, "character": 0.0, "hook": 0.0, "pace": 0.0},
        )
