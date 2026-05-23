from __future__ import annotations

import os
import tomllib
from pathlib import Path


def load_storyforge2_provider_config() -> dict:
    root = Path(__file__).resolve().parents[2]
    config_dir = root / "config"
    candidates = [
        config_dir / "storyforge2.toml",
        config_dir / "storyforge2.local.toml",
    ]
    for path in candidates:
        if not path.exists():
            continue
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        provider_name = data.get("provider_name", "codex")
        provider = data.get("model_providers", {}).get(provider_name, {})
        return {
            "provider_name": provider_name,
            "model": data.get("model") or provider.get("model"),
            "base_url": provider.get("base_url"),
            "api_key": provider.get("api_key"),
            "wire_api": provider.get("wire_api"),
            "requires_openai_auth": provider.get("requires_openai_auth"),
            "config_path": str(path),
        }
    return {}


def resolve_provider_setting(explicit: str | None, env_name: str, config_key: str, default: str) -> str:
    if explicit:
        return explicit
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    config = load_storyforge2_provider_config()
    config_value = config.get(config_key)
    if isinstance(config_value, str) and config_value:
        return config_value
    return default


def has_configured_api_key() -> bool:
    env_value = os.environ.get("STORYFORGE2_LLM_API_KEY")
    if env_value:
        return True
    config = load_storyforge2_provider_config()
    api_key = config.get("api_key")
    return isinstance(api_key, str) and bool(api_key.strip())
