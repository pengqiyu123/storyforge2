from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.providers.provider_config import has_configured_api_key, load_storyforge2_provider_config
from engine.providers.responses_api_provider import ResponsesAPIProvider
from engine.services import StoryEngineService


class StubResponsesProvider(ResponsesAPIProvider):
    def __init__(self, payloads: list[dict]) -> None:
        super().__init__(api_key="test-key", base_url="https://example.invalid", model="codex")
        self._payloads = list(payloads)

    def _post_json(self, payload: dict) -> dict:
        if not self._payloads:
            raise AssertionError("no stub payload remaining")
        item = self._payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class LLMProviderTests(unittest.TestCase):
    def test_load_provider_config_from_project_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "storyforge2.toml"
            config_path.write_text(
                '\n'.join(
                    [
                        'provider_name = "codex"',
                        'model = "gpt-5.4"',
                        '',
                        '[model_providers.codex]',
                        'base_url = "https://api.vip1129.cc"',
                        'wire_api = "responses"',
                        'requires_openai_auth = true',
                        'api_key = "test-config-key"',
                    ]
                ),
                encoding="utf-8",
            )
            fake_module = root / "engine" / "providers" / "provider_config.py"
            fake_module.parent.mkdir(parents=True, exist_ok=True)
            fake_module.write_text("", encoding="utf-8")
            with patch("engine.providers.provider_config.Path.resolve", return_value=fake_module):
                config = load_storyforge2_provider_config()
                self.assertEqual(config["model"], "gpt-5.4")
                self.assertEqual(config["base_url"], "https://api.vip1129.cc")
                self.assertEqual(config["api_key"], "test-config-key")

    def test_provider_reads_project_config_without_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_dir = root / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = config_dir / "storyforge2.toml"
            config_path.write_text(
                '\n'.join(
                    [
                        'provider_name = "codex"',
                        'model = "gpt-5.4"',
                        '',
                        '[model_providers.codex]',
                        'base_url = "https://api.vip1129.cc"',
                        'wire_api = "responses"',
                        'requires_openai_auth = true',
                        'api_key = "test-config-key"',
                    ]
                ),
                encoding="utf-8",
            )
            fake_module = root / "engine" / "providers" / "provider_config.py"
            fake_module.parent.mkdir(parents=True, exist_ok=True)
            fake_module.write_text("", encoding="utf-8")
            with patch("engine.providers.provider_config.Path.resolve", return_value=fake_module):
                provider = ResponsesAPIProvider()
                self.assertEqual(provider.model, "gpt-5.4")
                self.assertEqual(provider.base_url, "https://api.vip1129.cc")
                self.assertEqual(provider.api_key, "test-config-key")
                self.assertTrue(has_configured_api_key())

    def test_fake_provider_returns_preconfigured_json_and_text(self) -> None:
        provider = FakeLLMProvider(
            json_responses=[{"ok": True}],
            text_responses=["plain text"],
        )
        self.assertEqual(
            provider.generate_json("audit", "system", {"chapter": 1}, {"type": "object"}),
            {"ok": True},
        )
        self.assertEqual(provider.generate_text("compose", "system", {"chapter": 1}), "plain text")

    def test_fake_provider_returns_structured_error_payload(self) -> None:
        provider = FakeLLMProvider(json_errors=["network down"], text_errors=["timeout"])
        json_result = provider.generate_json("audit", "system", {"chapter": 1}, {"type": "object"})
        self.assertIn("error", json_result)
        text_result = provider.generate_text("compose", "system", {"chapter": 1})
        self.assertIn("timeout", text_result)

    def test_responses_provider_extracts_json_from_output_text(self) -> None:
        provider = StubResponsesProvider(
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps({"passed": True}, ensure_ascii=False),
                                }
                            ],
                        }
                    ]
                }
            ]
        )
        result = provider.generate_json("audit", "system", {"chapter": 1}, {"type": "object"})
        self.assertEqual(result, {"passed": True})

    def test_responses_provider_returns_error_on_invalid_json(self) -> None:
        provider = StubResponsesProvider(
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "not-json"}],
                        }
                    ]
                },
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "not-json"}],
                        }
                    ]
                }
            ]
        )
        result = provider.generate_json("audit", "system", {"chapter": 1}, {"type": "object"})
        self.assertIn("error", result)
        self.assertEqual(result["fallback_error"], "fallback_invalid_json_response")

    def test_responses_provider_falls_back_to_text_json_when_schema_request_fails(self) -> None:
        provider = StubResponsesProvider(
            [
                RuntimeError("HTTP Error 502: Bad Gateway"),
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps({"passed": True}, ensure_ascii=False),
                                }
                            ],
                        }
                    ]
                },
            ]
        )
        result = provider.generate_json("audit", "system", {"chapter": 1}, {"type": "object"})
        self.assertEqual(result, {"passed": True})
        self.assertEqual(provider.last_diagnostics["mode_used"], "text_fallback")
        self.assertEqual(provider.last_diagnostics["primary_error"], "HTTP Error 502: Bad Gateway")

    def test_responses_provider_salvages_fenced_json(self) -> None:
        provider = StubResponsesProvider(
            [
                RuntimeError("HTTP Error 502: Bad Gateway"),
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "```json\n{\"passed\": true}\n```",
                                }
                            ],
                        }
                    ]
                },
            ]
        )
        result = provider.generate_json("audit", "system", {"chapter": 1}, {"type": "object"})
        self.assertEqual(result, {"passed": True})
        self.assertEqual(provider.last_diagnostics["mode_used"], "text_salvage")

    def test_responses_provider_rejects_non_object_json_in_fallback(self) -> None:
        provider = StubResponsesProvider(
            [
                RuntimeError("HTTP Error 502: Bad Gateway"),
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "[1,2,3]",
                                }
                            ],
                        }
                    ]
                },
            ]
        )
        result = provider.generate_json("audit", "system", {"chapter": 1}, {"type": "object"})
        self.assertIn("error", result)
        self.assertEqual(result["fallback_error"], "fallback_json_response_not_object")

    def test_responses_provider_keeps_json_schema_diagnostics_on_primary_success(self) -> None:
        provider = StubResponsesProvider(
            [
                {
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps({"ok": True, "echo": "pong"}, ensure_ascii=False),
                                }
                            ],
                        }
                    ]
                }
            ]
        )
        result = provider.generate_json("ping", "system", {"ping": "pong"}, {"type": "object"})
        self.assertTrue(result["ok"])
        self.assertEqual(provider.last_diagnostics["mode_used"], "json_schema")
        self.assertIsNone(provider.last_diagnostics["primary_error"])
        self.assertIsNone(provider.last_diagnostics["fallback_error"])
        self.assertGreater(provider.last_diagnostics["request_bytes"], 0)

    def test_story_engine_defaults_to_local_fake_provider_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = StoryEngineService(temp_dir)
            self.assertEqual(service.gate_runner.auditor.__class__.__name__, "FallbackAuditor")
            self.assertEqual(service.truth_extractor.provider.__class__.__name__, "FakeLLMProvider")

    def test_responses_provider_generate_text_forwards_generation_config(self) -> None:
        captured = {}

        class CaptureProvider(StubResponsesProvider):
            def _post_json(self, payload: dict) -> dict:
                captured.update(payload)
                return {
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "正文文本"}],
                        }
                    ]
                }

        provider = CaptureProvider([])
        result = provider.generate_text(
            "writer",
            "system",
            {"chapter": 1},
            generation_config={
                "temperature": 0.85,
                "top_p": 0.92,
                "frequency_penalty": 0.15,
                "presence_penalty": 0.1,
            },
        )
        self.assertEqual(result, "正文文本")
        self.assertEqual(captured["temperature"], 0.85)
        self.assertEqual(captured["top_p"], 0.92)
        self.assertEqual(captured["frequency_penalty"], 0.15)
        self.assertEqual(captured["presence_penalty"], 0.1)


if __name__ == "__main__":
    unittest.main()
