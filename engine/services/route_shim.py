from __future__ import annotations

from engine.schemas.intent import IntentExecResult, ParsedIntent
from engine.services.intent_compiler import IntentCompilerService


class SkillRouteShim:
    def __init__(self, engine: object, batch: object | None = None) -> None:
        self.compiler = IntentCompilerService(engine=engine, batch_orchestrator=batch)

    def handle_skill_request(self, skill_name: str, payload: dict) -> dict:
        request = payload.get("request", "")
        book_id = payload.get("book_id", "")
        if not request or not book_id:
            return {"error": "missing_request_or_book_id", "skill_name": skill_name}
        parsed = self.compiler.parse(request, book_id)
        if parsed is None:
            return {"error": "unrecognized_intent", "skill_name": skill_name, "request": request}
        dry_run = payload.get("dry_run", False)
        result = self.compiler.execute(parsed, dry_run=dry_run)
        return result.model_dump(mode="json")
