from __future__ import annotations

import re

from engine.providers.llm_provider import LLMProvider
from engine.schemas.artifact import AdversarialEditRecord


class AdversarialEditor:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    def generate_candidate(
        self,
        source_text: str,
        instruction: str,
    ) -> str:
        if instruction == "compress":
            return self._truncate_to_ratio(source_text, 0.87)
        if instruction == "redundant":
            if self.provider:
                return self._llm_candidate(source_text, instruction)
            return self._truncate_to_ratio(source_text, 0.74)
        if instruction == "overexplain":
            if self.provider:
                return self._llm_candidate(source_text, instruction)
            return source_text + "\n\n[overexplain-marker]"
        return self._truncate_to_ratio(source_text, 0.87)

    @staticmethod
    def decide(
        *,
        original_overall_score: float,
        candidate_overall_score: float,
        source_settlement_artifact_id: str,
        candidate_draft_artifact_id: str,
        candidate_settlement_artifact_id: str,
        edit_instruction: str,
        original_char_count: int,
        candidate_char_count: int,
    ) -> AdversarialEditRecord:
        kept = candidate_overall_score >= original_overall_score
        reason_codes: list[str] = []
        if kept:
            reason_codes.append("candidate_score_gte_original")
        else:
            reason_codes.append("candidate_score_lt_original")
        reduction = original_char_count - candidate_char_count
        return AdversarialEditRecord(
            source_settlement_artifact_id=source_settlement_artifact_id,
            edit_instruction=edit_instruction,
            candidate_draft_artifact_id=candidate_draft_artifact_id,
            candidate_settlement_artifact_id=candidate_settlement_artifact_id,
            reduction_stats={
                "original_chars": original_char_count,
                "candidate_chars": candidate_char_count,
                "reduction": reduction,
            },
            kept=kept,
            decision_reason_codes=reason_codes,
        )

    def _truncate_to_ratio(self, text: str, ratio: float) -> str:
        target_len = max(1, int(len(text) * ratio))
        truncated = text[:target_len]
        last_period = max(
            truncated.rfind("。"),
            truncated.rfind("！"),
            truncated.rfind("？"),
            truncated.rfind("；"),
        )
        if last_period > target_len // 2:
            truncated = truncated[: last_period + 1]
        return truncated

    def _llm_candidate(self, source_text: str, instruction: str) -> str:
        if not self.provider:
            return source_text
        prompts = {
            "redundant": "请删除以下文本中约26%的冗余内容，保持核心情节和关键细节不变，直接输出修改后的文本：",
            "overexplain": "请将以下文本扩写约32%，增加解释性内容，直接输出修改后的文本：",
        }
        system_prompt = prompts.get(instruction, "请精简以下文本，直接输出修改后的文本：")
        result = self.provider.generate_text(
            f"adversarial_{instruction}",
            system_prompt,
            {"source_text": source_text},
        )
        if result.startswith("error:"):
            return source_text
        return result


def count_chinese_chars(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))
