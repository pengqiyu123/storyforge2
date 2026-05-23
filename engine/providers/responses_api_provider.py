from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .llm_provider import LLMProvider
from .provider_config import resolve_provider_setting


class ResponsesAPIProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        connect_timeout_seconds: int = 30,
        read_timeout_seconds: int = 120,
        max_retries: int = 2,
    ) -> None:
        self.api_key = resolve_provider_setting(api_key, "STORYFORGE2_LLM_API_KEY", "api_key", "")
        self.base_url = resolve_provider_setting(
            base_url, "STORYFORGE2_LLM_BASE_URL", "base_url", "https://api.vip1129.cc"
        ).rstrip("/")
        self.model = resolve_provider_setting(model, "STORYFORGE2_LLM_MODEL", "model", "codex")
        self.connect_timeout_seconds = connect_timeout_seconds
        self.read_timeout_seconds = read_timeout_seconds
        self.max_retries = max_retries
        self.last_diagnostics: dict[str, object] = {}

    def generate_json(
        self,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_schema: dict,
    ) -> dict:
        if not self.api_key:
            self.last_diagnostics = self._build_diagnostics(
                mode_used="none",
                primary_error="missing_api_key",
                request_bytes=0,
            )
            return {"error": "missing_api_key", "task_name": task_name}
        schema_payload = self._build_json_schema_payload(task_name, system_prompt, user_payload, response_schema)
        request_bytes = len(json.dumps(schema_payload, ensure_ascii=False).encode("utf-8"))
        primary_error: str | None = None
        try:
            raw = self._post_json(schema_payload)
            parsed = self._extract_json(raw)
            if "error" not in parsed:
                self.last_diagnostics = self._build_diagnostics(
                    mode_used="json_schema",
                    request_bytes=request_bytes,
                )
                return parsed
            primary_error = str(parsed["error"])
        except Exception as exc:  # pragma: no cover - network path
            primary_error = str(exc)
        fallback = self._generate_json_via_text(task_name, system_prompt, user_payload, response_schema, primary_error)
        if "error" not in fallback:
            return fallback
        self.last_diagnostics = self._build_diagnostics(
            mode_used=str(self.last_diagnostics.get("mode_used", "text_fallback")),
            primary_error=primary_error,
            fallback_error=str(fallback.get("error")),
            raw_excerpt=str(fallback.get("raw", ""))[:300] or None,
            request_bytes=request_bytes,
        )
        return {
            "error": primary_error or "generate_json_failed",
            "task_name": task_name,
            "fallback_error": fallback.get("error"),
        }

    def generate_text(
        self,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        generation_config: dict | None = None,
    ) -> str:
        generation_config = generation_config or {}
        payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": json.dumps(user_payload, ensure_ascii=False)}]},
            ],
        }
        for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
            if key in generation_config and generation_config[key] is not None:
                payload[key] = generation_config[key]
        if not self.api_key:
            self.last_diagnostics = self._build_diagnostics(
                mode_used="none",
                primary_error="missing_api_key",
                request_bytes=0,
            )
            return "error: missing_api_key"
        try:
            raw = self._post_json(payload)
        except Exception as exc:  # pragma: no cover - network path
            self.last_diagnostics = self._build_diagnostics(
                mode_used="text",
                primary_error=str(exc),
                request_bytes=len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            )
            return f"error: {exc}"
        text = self._extract_text(raw)
        self.last_diagnostics = self._build_diagnostics(
            mode_used="text",
            request_bytes=len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            raw_excerpt=text[:300] or None,
        )
        return text

    def _build_json_schema_payload(
        self,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_schema: dict,
    ) -> dict:
        return {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": json.dumps(user_payload, ensure_ascii=False)}]},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": task_name,
                    "schema": response_schema,
                }
            },
        }

    def _generate_json_via_text(
        self,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_schema: dict,
        schema_error: str,
    ) -> dict:
        schema_instruction = self._build_schema_instruction(response_schema)
        fallback_prompt = (
            f"{system_prompt}\n"
            "你上一次在结构化响应模式下失败了。"
            "这一次不要输出任何解释、前缀、Markdown 或代码块。"
            "只输出一个 JSON object。"
            f" task_name={task_name}; {schema_instruction}"
        )
        text_payload = {
            "model": self.model,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": fallback_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": json.dumps(user_payload, ensure_ascii=False)}]},
            ],
        }
        try:
            raw = self._post_json(text_payload)
            text_result = self._extract_text(raw)
        except Exception as exc:
            self.last_diagnostics = self._build_diagnostics(
                mode_used="text_fallback",
                primary_error=schema_error,
                fallback_error=str(exc),
                request_bytes=len(json.dumps(text_payload, ensure_ascii=False).encode("utf-8")),
            )
            return {"error": f"error: {exc}", "task_name": task_name, "schema_error": schema_error}
        if text_result.startswith("error:"):
            self.last_diagnostics = self._build_diagnostics(
                mode_used="text_fallback",
                primary_error=schema_error,
                fallback_error=text_result,
                raw_excerpt=text_result[:300],
                request_bytes=len(json.dumps(text_payload, ensure_ascii=False).encode("utf-8")),
            )
            return {"error": text_result, "task_name": task_name, "schema_error": schema_error}
        try:
            parsed = json.loads(text_result)
            self.last_diagnostics = self._build_diagnostics(
                mode_used="text_fallback",
                primary_error=schema_error,
                raw_excerpt=text_result[:300] or None,
                request_bytes=len(json.dumps(text_payload, ensure_ascii=False).encode("utf-8")),
            )
        except json.JSONDecodeError:
            parsed = self._salvage_json_object(text_result)
            if parsed is not None:
                self.last_diagnostics = self._build_diagnostics(
                    mode_used="text_salvage",
                    primary_error=schema_error,
                    raw_excerpt=text_result[:300] or None,
                    request_bytes=len(json.dumps(text_payload, ensure_ascii=False).encode("utf-8")),
                )
            else:
                self.last_diagnostics = self._build_diagnostics(
                    mode_used="text_fallback",
                    primary_error=schema_error,
                    fallback_error="fallback_invalid_json_response",
                    raw_excerpt=text_result[:300] or None,
                    request_bytes=len(json.dumps(text_payload, ensure_ascii=False).encode("utf-8")),
                )
                return {
                    "error": "fallback_invalid_json_response",
                    "task_name": task_name,
                    "schema_error": schema_error,
                    "raw": text_result,
                }
        if not isinstance(parsed, dict):
            self.last_diagnostics = self._build_diagnostics(
                mode_used="text_fallback",
                primary_error=schema_error,
                fallback_error="fallback_json_response_not_object",
                raw_excerpt=text_result[:300] or None,
                request_bytes=len(json.dumps(text_payload, ensure_ascii=False).encode("utf-8")),
            )
            return {
                "error": "fallback_json_response_not_object",
                "task_name": task_name,
                "schema_error": schema_error,
                "raw": text_result,
            }
        return parsed

    @staticmethod
    def _build_schema_instruction(response_schema: dict) -> str:
        properties = response_schema.get("properties", {})
        required = response_schema.get("required", [])
        if not isinstance(properties, dict):
            return "返回一个合法 JSON object。"
        parts: list[str] = []
        for key in required:
            prop = properties.get(key, {})
            kind = prop.get("type", "value") if isinstance(prop, dict) else "value"
            parts.append(f"{key}:{kind}")
        if not parts:
            return "返回一个合法 JSON object。"
        return "必须包含这些字段，且字段名必须完全一致：" + ", ".join(parts) + "。"

    @staticmethod
    def _build_diagnostics(
        *,
        mode_used: str,
        request_bytes: int,
        primary_error: str | None = None,
        fallback_error: str | None = None,
        raw_excerpt: str | None = None,
    ) -> dict[str, object]:
        return {
            "mode_used": mode_used,
            "primary_error": primary_error,
            "fallback_error": fallback_error,
            "raw_excerpt": raw_excerpt,
            "request_bytes": request_bytes,
        }

    @staticmethod
    def _salvage_json_object(text: str) -> dict | None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 3:
                cleaned = "\n".join(lines[1:-1]).strip()
        start = cleaned.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    snippet = cleaned[start : index + 1]
                    try:
                        parsed = json.loads(snippet)
                    except json.JSONDecodeError:
                        return None
                    return parsed if isinstance(parsed, dict) else None
        return None

    def _post_json(self, payload: dict) -> dict:
        url = f"{self.base_url}/responses"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.connect_timeout_seconds + self.read_timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:  # pragma: no cover - network path
                last_error = exc
                if exc.code < 500 or attempt >= self.max_retries:
                    break
                time.sleep(0.5 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:  # pragma: no cover - network path
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(str(last_error or "responses_api_request_failed"))

    @staticmethod
    def _extract_text(raw: dict) -> str:
        for item in raw.get("output", []):
            for content in item.get("content", []):
                text = content.get("text")
                if isinstance(text, str):
                    return text
        return ""

    def _extract_json(self, raw: dict) -> dict:
        text = self._extract_text(raw)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"error": "invalid_json_response", "raw": text}
        return parsed if isinstance(parsed, dict) else {"error": "json_response_not_object", "raw": text}
