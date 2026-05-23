from __future__ import annotations

import time

from .llm_provider import LLMProvider


class FakeLLMProvider(LLMProvider):
    def __init__(
        self,
        *,
        json_responses: list[dict] | None = None,
        text_responses: list[str] | None = None,
        json_errors: list[str] | None = None,
        text_errors: list[str] | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.json_responses = list(json_responses or [])
        self.text_responses = list(text_responses or [])
        self.json_errors = list(json_errors or [])
        self.text_errors = list(text_errors or [])
        self.delay_seconds = delay_seconds
        self.last_diagnostics: dict[str, object] = {}

    def generate_json(
        self,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_schema: dict,
    ) -> dict:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.json_errors:
            error = self.json_errors.pop(0)
            self.last_diagnostics = {
                "mode_used": "fake_json_error",
                "primary_error": error,
                "fallback_error": None,
                "raw_excerpt": None,
                "request_bytes": len(str(user_payload).encode("utf-8")),
            }
            return {"error": error, "task_name": task_name}
        if self.json_responses:
            self.last_diagnostics = {
                "mode_used": "fake_json",
                "primary_error": None,
                "fallback_error": None,
                "raw_excerpt": None,
                "request_bytes": len(str(user_payload).encode("utf-8")),
            }
            return self.json_responses.pop(0)
        self.last_diagnostics = {
            "mode_used": "fake_json_error",
            "primary_error": "no_fake_json_response",
            "fallback_error": None,
            "raw_excerpt": None,
            "request_bytes": len(str(user_payload).encode("utf-8")),
        }
        return {"error": "no_fake_json_response", "task_name": task_name}

    def generate_text(
        self,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        generation_config: dict | None = None,
    ) -> str:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.text_errors:
            error = self.text_errors.pop(0)
            self.last_diagnostics = {
                "mode_used": "fake_text_error",
                "primary_error": error,
                "fallback_error": None,
                "raw_excerpt": None,
                "request_bytes": len(str(user_payload).encode("utf-8")),
            }
            return f"error: {error}"
        if self.text_responses:
            response = self.text_responses.pop(0)
            self.last_diagnostics = {
                "mode_used": "fake_text",
                "primary_error": None,
                "fallback_error": None,
                "raw_excerpt": response[:300],
                "request_bytes": len(str(user_payload).encode("utf-8")),
            }
            return response
        self.last_diagnostics = {
            "mode_used": "fake_text_error",
            "primary_error": "no_fake_text_response",
            "fallback_error": None,
            "raw_excerpt": None,
            "request_bytes": len(str(user_payload).encode("utf-8")),
        }
        return "error: no_fake_text_response"
