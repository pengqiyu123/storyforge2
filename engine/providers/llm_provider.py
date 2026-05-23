from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def generate_json(
        self,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        response_schema: dict,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def generate_text(
        self,
        task_name: str,
        system_prompt: str,
        user_payload: dict,
        generation_config: dict | None = None,
    ) -> str:
        raise NotImplementedError
