from __future__ import annotations

from engine.providers.llm_provider import LLMProvider
from engine.schemas.artifact import ReaderPanelRecord


READER_PANEL_SCHEMA = {
    "type": "object",
    "properties": {
        "editor_findings": {"type": "array"},
        "genre_reader_findings": {"type": "array"},
        "writer_findings": {"type": "array"},
        "first_reader_findings": {"type": "array"},
        "momentum_loss": {"type": "boolean"},
        "earned_ending": {"type": "boolean"},
        "cut_candidate": {"type": "array"},
        "missing_scene": {"type": "array"},
        "thinnest_character": {"type": "string"},
        "aggregate_recommendation": {"type": "string"},
        "risk_flags": {"type": "array"},
    },
    "required": [
        "editor_findings",
        "genre_reader_findings",
        "writer_findings",
        "first_reader_findings",
        "aggregate_recommendation",
    ],
}

SYSTEM_PROMPT = (
    "你是中文小说读者评估面板。请从四个角色视角评估提供的章节片段：\n"
    "1. 编辑：关注叙事结构和市场定位\n"
    "2. 类型读者：关注节奏、钩子和类型惯例\n"
    "3. 作家：关注技巧、文笔和人物塑造\n"
    "4. 初读者：关注沉浸感和可读性\n"
    "请只输出结构化 JSON。"
)


class LLMReaderPanelAdapter:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def evaluate(
        self,
        *,
        batch_run: object,
        checkpoint: object,
        slice_payload: dict,
    ) -> dict:
        chapter_text = slice_payload.get("chapter_text", "")
        if not chapter_text:
            return self._fallback("no_chapter_text")

        payload = self.provider.generate_json(
            "reader_panel",
            SYSTEM_PROMPT,
            {
                "chapter_slice": slice_payload.get("chapter_slice", []),
                "chapter_text": chapter_text,
                "book_context": slice_payload.get("book_context", ""),
            },
            READER_PANEL_SCHEMA,
        )

        if "error" in payload:
            return self._fallback(payload["error"])

        required = {"editor_findings", "genre_reader_findings", "writer_findings", "first_reader_findings", "aggregate_recommendation"}
        if not required.issubset(payload):
            return self._fallback("invalid_payload")

        return {
            "panel_scope": "checkpoint",
            "editor_findings": payload.get("editor_findings", []),
            "genre_reader_findings": payload.get("genre_reader_findings", []),
            "writer_findings": payload.get("writer_findings", []),
            "first_reader_findings": payload.get("first_reader_findings", []),
            "momentum_loss": payload.get("momentum_loss", False),
            "earned_ending": payload.get("earned_ending", True),
            "cut_candidate": payload.get("cut_candidate", []),
            "missing_scene": payload.get("missing_scene", []),
            "thinnest_character": payload.get("thinnest_character"),
            "aggregate_recommendation": payload.get("aggregate_recommendation", "continue"),
            "risk_flags": payload.get("risk_flags", []),
        }

    @staticmethod
    def _fallback(reason: str) -> dict:
        return {
            "panel_scope": "checkpoint",
            "editor_findings": [],
            "genre_reader_findings": [],
            "writer_findings": [],
            "first_reader_findings": [],
            "momentum_loss": False,
            "earned_ending": True,
            "cut_candidate": [],
            "missing_scene": [],
            "thinnest_character": f"panel_fallback:{reason}",
            "aggregate_recommendation": "continue",
            "risk_flags": [f"panel_fallback:{reason}"],
        }
