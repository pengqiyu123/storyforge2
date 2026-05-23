from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from engine.providers.provider_config import load_storyforge2_provider_config
from engine.providers.responses_api_provider import ResponsesAPIProvider
from engine.schemas.chapter import ChapterStage
from engine.services import StoryEngineService
from engine.services.batch_orchestrator import BatchOrchestratorService
from engine.services.gate_runner import GateInputBundle
from engine.gates.llm_auditor_adapter import LLMAuditorAdapter
from engine.truth.truth_extractor_adapter import TruthExtractorAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="storyforge2")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_book = subparsers.add_parser("create-book")
    create_book.add_argument("book_id")
    create_book.add_argument("title")
    create_book.add_argument("--target-chapters", type=int, default=12)

    init_chapter = subparsers.add_parser("init-chapter")
    init_chapter.add_argument("book_id")
    init_chapter.add_argument("chapter_no", type=int)

    show_book = subparsers.add_parser("show-book")
    show_book.add_argument("book_id")

    show_chapter = subparsers.add_parser("show-chapter")
    show_chapter.add_argument("book_id")
    show_chapter.add_argument("chapter_no", type=int)

    transition = subparsers.add_parser("transition")
    transition.add_argument("book_id")
    transition.add_argument("chapter_no", type=int)
    transition.add_argument("target_stage")
    transition.add_argument("--run-id", required=True)
    transition.add_argument("--artifact-ref", action="append", default=[])

    rebuild = subparsers.add_parser("rebuild-db")
    rebuild.add_argument("book_id")

    batch_create = subparsers.add_parser("batch-create")
    batch_create.add_argument("book_id")
    batch_create.add_argument("batch_mode")
    batch_create.add_argument("start_chapter", type=int)
    batch_create.add_argument("end_chapter", type=int)

    batch_start = subparsers.add_parser("batch-start")
    batch_start.add_argument("batch_run_id")

    batch_status = subparsers.add_parser("batch-status")
    batch_status.add_argument("batch_run_id")

    batch_resume = subparsers.add_parser("batch-resume")
    batch_resume.add_argument("batch_run_id")

    batch_retry = subparsers.add_parser("batch-retry-item")
    batch_retry.add_argument("batch_run_id")
    batch_retry.add_argument("item_id")

    batch_review = subparsers.add_parser("batch-checkpoint-review")
    batch_review.add_argument("batch_run_id")
    batch_review.add_argument("--checkpoint-id")

    intent_parse = subparsers.add_parser("intent-parse")
    intent_parse.add_argument("book_id")
    intent_parse.add_argument("request")

    intent_exec = subparsers.add_parser("intent-exec")
    intent_exec.add_argument("book_id")
    intent_exec.add_argument("request")
    intent_exec.add_argument("--dry-run", action="store_true")

    provider_smoke = subparsers.add_parser("provider-smoke")
    provider_smoke.add_argument("--draft-text", default="林七推开旧仓库的铁门，听见楼上传来短促的金属碰撞声。他没有立刻上楼，而是先确认账册有没有被人动过。")

    chapter_smoke = subparsers.add_parser("chapter-smoke")
    chapter_smoke.add_argument("book_id")
    chapter_smoke.add_argument("title")
    chapter_smoke.add_argument("chapter_no", type=int)
    chapter_smoke.add_argument("--auto-revise", action="store_true")

    chapter_full_cycle = subparsers.add_parser("chapter-full-cycle")
    chapter_full_cycle.add_argument("book_id")
    chapter_full_cycle.add_argument("title")
    chapter_full_cycle.add_argument("chapter_no", type=int)
    chapter_full_cycle.add_argument("--platform", default="tomato")
    chapter_full_cycle.add_argument("--max-auto-rounds", type=int, default=None)

    return parser


def parse_artifact_refs(values: list[str]) -> dict[str, str]:
    refs: dict[str, str] = {}
    for value in values:
        key, artifact_id = value.split("=", 1)
        refs[key] = artifact_id
    return refs


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    service = StoryEngineService(args.root, enable_real_provider=True)
    batch = BatchOrchestratorService(args.root, engine=service)

    if args.command == "create-book":
        result = service.create_book(
            {
                "book_id": args.book_id,
                "title": args.title,
                "target_chapters": args.target_chapters,
            }
        )
    elif args.command == "init-chapter":
        result = service.init_chapter(args.book_id, args.chapter_no)
    elif args.command == "show-book":
        result = service.get_book(args.book_id)
    elif args.command == "show-chapter":
        result = service.get_chapter_status(args.book_id, args.chapter_no)
    elif args.command == "transition":
        result = service.transition_chapter(
            args.book_id,
            args.chapter_no,
            args.target_stage,
            args.run_id,
            parse_artifact_refs(args.artifact_ref),
        )
    elif args.command == "rebuild-db":
        service.rebuild_read_model(args.book_id)
        result = {"ok": True, "book_id": args.book_id}
    elif args.command == "batch-create":
        result = batch.create_batch_run(
            args.book_id,
            chapter_range=[args.start_chapter, args.end_chapter],
            batch_mode=args.batch_mode,
        )
    elif args.command == "batch-start":
        result = batch.start_batch_run(args.batch_run_id)
    elif args.command == "batch-status":
        result = batch.get_batch_run(args.batch_run_id)
    elif args.command == "batch-resume":
        result = batch.resume_batch_run(args.batch_run_id)
    elif args.command == "batch-retry-item":
        result = batch.retry_batch_item(args.batch_run_id, args.item_id)
    elif args.command == "batch-checkpoint-review":
        result = batch.run_checkpoint_review(args.batch_run_id, checkpoint_id=args.checkpoint_id)
    elif args.command == "intent-parse":
        from engine.services.intent_compiler import IntentCompilerService
        compiler = IntentCompilerService(engine=service, batch_orchestrator=batch)
        parsed = compiler.parse(args.request, args.book_id)
        if parsed is None:
            result = {"error": "unrecognized_intent", "request": args.request}
        else:
            result = parsed.model_dump(mode="json")
    elif args.command == "intent-exec":
        from engine.services.intent_compiler import IntentCompilerService
        compiler = IntentCompilerService(engine=service, batch_orchestrator=batch)
        parsed = compiler.parse(args.request, args.book_id)
        if parsed is None:
            result = {"error": "unrecognized_intent", "request": args.request}
        else:
            result = compiler.execute(parsed, dry_run=args.dry_run).model_dump(mode="json")
    elif args.command == "provider-smoke":
        result = run_provider_smoke(args.draft_text)
    elif args.command == "chapter-smoke":
        result = run_chapter_smoke(
            service=service,
            book_id=args.book_id,
            title=args.title,
            chapter_no=args.chapter_no,
            auto_revise=args.auto_revise,
        )
    elif args.command == "chapter-full-cycle":
        result = run_chapter_full_cycle(
            service=service,
            book_id=args.book_id,
            title=args.title,
            chapter_no=args.chapter_no,
            platform=args.platform,
            max_auto_rounds=args.max_auto_rounds,
        )
    else:
        raise ValueError(f"unknown command: {args.command}")

    print(json.dumps(_to_jsonable(result), ensure_ascii=False, indent=2))
    return 0


def _to_jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


def run_provider_smoke(draft_text: str) -> dict:
    config = load_storyforge2_provider_config()
    provider = ResponsesAPIProvider(connect_timeout_seconds=20, read_timeout_seconds=90, max_retries=0)
    ping_result = provider.generate_json(
        "smoke_ping",
        "你是一个严格返回 JSON 的助手。",
        {"ping": "pong"},
        {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}, "echo": {"type": "string"}},
            "required": ["ok", "echo"],
            "additionalProperties": False,
        },
    )
    ping_diag = dict(provider.last_diagnostics)

    auditor = LLMAuditorAdapter(provider)
    audit_probe = auditor.review(
        GateInputBundle(
            book_id="probe-book",
            chapter_no=1,
            revision_round=0,
            settlement_artifact_id="settlement-probe",
            candidate_signature="sig-probe",
            draft_text=draft_text,
            plan_summary="都市悬疑，第一章需要快速建立危险感和钩子",
            compose_constraints=["中文小说口吻", "避免报告腔"],
            baseline_gate_summary={},
            revision_mode=None,
        )
    )
    audit_diag = dict(provider.last_diagnostics)

    extractor = TruthExtractorAdapter(provider)
    truth_probe = extractor.extract(
        book_id="probe-book",
        chapter_no=1,
        draft_text=draft_text,
        truth_snapshot={
            "snapshot": {"snapshot_id": "snapshot-0000"},
            "canon": {"facts": []},
            "characters": {"characters": [], "relationships": []},
            "hook_ledger": {"hooks": []},
            "chapter_facts": {"chapters": []},
        },
    )
    truth_diag = dict(provider.last_diagnostics)

    return {
        "config_source": config.get("config_path"),
        "model": provider.model,
        "base_url": provider.base_url,
        "ping_result": ping_result,
        "ping_diagnostics": ping_diag,
        "audit_probe": audit_probe.model_dump(mode="json"),
        "audit_diagnostics": audit_diag,
        "truth_probe": truth_probe,
        "truth_diagnostics": truth_diag,
    }


def run_chapter_smoke(service: StoryEngineService, book_id: str, title: str, chapter_no: int, auto_revise: bool) -> dict:
    book_id, requested_book_id = _ensure_smoke_book(service, book_id, title, chapter_no)
    try:
        plan_id, compose_id, draft_id, settled = _run_until_settled(service, book_id, chapter_no)
    except Exception as exc:
        status = service.get_chapter_status(book_id, chapter_no)["status"]
        return _build_full_cycle_failure(
            service=service,
            book_id=book_id,
            requested_book_id=requested_book_id,
            chapter_no=chapter_no,
            plan_id=None,
            compose_id=None,
            draft_id=None,
            status=status,
            final_outcome="draft_generation_failed",
            platform="tomato",
            audit_diagnostics_history=[],
            truth_diagnostics_history=[],
            comparison_history=[],
            error=str(exc),
        )
    audit, audit_diag, truth_diag = _audit_with_diagnostics(service, book_id, chapter_no, settled.current_artifact_refs["settlement"])
    result = {
        "book_id": book_id,
        "requested_book_id": requested_book_id,
        "chapter_no": chapter_no,
        "plan_id": plan_id,
        "compose_id": compose_id,
        "draft_id": draft_id,
        "settled_stage": settled.stage.value,
        "audit_stage": audit["status"].stage.value,
        "gate_passed": audit["gate_decision"]["passed"],
        "gate_reasons": audit["gate_decision"]["reason_codes"],
        "truth_delta_notes": audit["truth_delta"].get("notes", []),
        "artifacts": audit["status"].current_artifact_refs,
        "audit_diagnostics": audit_diag,
        "truth_diagnostics": truth_diag,
        "final_stage": audit["status"].stage.value,
        "final_outcome": "audited_passed" if audit["gate_decision"]["passed"] else "audited_failed",
    }
    if auto_revise and not audit["gate_decision"]["passed"]:
        try:
            revise = service.revise_chapter(book_id, chapter_no, mode="surgical")
            result["auto_revise"] = {
                "status": revise["status"].stage.value,
                "revision_brief_artifact_id": revise["revision_brief_artifact_id"],
                "draft_artifact_id": revise["draft_artifact_id"],
            }
        except Exception as exc:
            status = service.get_chapter_status(book_id, chapter_no)["status"]
            result["auto_revise"] = {
                "status": status.stage.value,
                "error": str(exc),
            }
    return result


def run_chapter_full_cycle(
    service: StoryEngineService,
    book_id: str,
    title: str,
    chapter_no: int,
    platform: str = "tomato",
    max_auto_rounds: int | None = None,
) -> dict:
    book_id, requested_book_id = _ensure_smoke_book(service, book_id, title, chapter_no)
    try:
        plan_id, compose_id, draft_id, settled = _run_until_settled(service, book_id, chapter_no)
    except Exception as exc:
        status = service.get_chapter_status(book_id, chapter_no)["status"]
        return _build_full_cycle_failure(
            service=service,
            book_id=book_id,
            requested_book_id=requested_book_id,
            chapter_no=chapter_no,
            plan_id=None,
            compose_id=None,
            draft_id=None,
            status=status,
            final_outcome="draft_generation_failed",
            platform=platform,
            audit_diagnostics_history=[],
            truth_diagnostics_history=[],
            comparison_history=[],
            error=str(exc),
        )
    status = settled
    audit_diagnostics_history: list[dict] = []
    truth_diagnostics_history: list[dict] = []
    comparison_history: list[dict] = []
    accepted_gate_id: str | None = None
    approval_receipt_artifact_id: str | None = None
    export_manifest_artifact_id: str | None = None
    final_outcome = "audited_failed"
    final_audit = None
    auto_round_limit = service.max_revision_rounds if max_auto_rounds is None else min(max_auto_rounds, service.max_revision_rounds)

    try:
        audit, audit_diag, truth_diag = _audit_with_diagnostics(
            service,
            book_id,
            chapter_no,
            settled.current_artifact_refs["settlement"],
        )
    except Exception as exc:
        status = service.get_chapter_status(book_id, chapter_no)["status"]
        return _build_full_cycle_failure(
            service=service,
            book_id=book_id,
            requested_book_id=requested_book_id,
            chapter_no=chapter_no,
            plan_id=plan_id,
            compose_id=compose_id,
            draft_id=draft_id,
            status=status,
            final_outcome="audit_runtime_error",
            platform=platform,
            audit_diagnostics_history=[],
            truth_diagnostics_history=[],
            comparison_history=[],
            error=str(exc),
        )
    audit_diagnostics_history.append(audit_diag)
    truth_diagnostics_history.append(truth_diag)
    final_audit = audit
    status = audit["status"]
    baseline_gate_id = audit["gate_decision_artifact_id"]
    accepted_gate_id = baseline_gate_id if audit["gate_decision"]["passed"] else None

    while True:
        if status.stage == ChapterStage.AUDITED_PASSED:
            try:
                approved = service.approve_chapter(book_id, chapter_no)
                approval_receipt_artifact_id = approved.current_artifact_refs.get("approval_receipt")
                exported = service.export_chapter(book_id, chapter_no, platform=platform)
            except Exception as exc:
                status = service.get_chapter_status(book_id, chapter_no)["status"]
                return _build_full_cycle_failure(
                    service=service,
                    book_id=book_id,
                    requested_book_id=requested_book_id,
                    chapter_no=chapter_no,
                    plan_id=plan_id,
                    compose_id=compose_id,
                    draft_id=draft_id,
                    status=status,
                    final_outcome="export_runtime_error",
                    platform=platform,
                    audit_diagnostics_history=audit_diagnostics_history,
                    truth_diagnostics_history=truth_diagnostics_history,
                    comparison_history=comparison_history,
                    error=str(exc),
                )
            export_manifest_artifact_id = exported["export_manifest_artifact_id"]
            final_outcome = "exported"
            status = exported["status"]
            break

        if status.stage in {
            ChapterStage.ROLLED_BACK,
            ChapterStage.HUMAN_REVIEW_REQUIRED,
            ChapterStage.INVALIDATED,
            ChapterStage.BLOCKED,
        }:
            final_outcome = status.stage.value
            break

        if status.stage != ChapterStage.AUDITED_FAILED:
            final_outcome = status.stage.value
            break

        if status.revision_round >= auto_round_limit:
            if auto_round_limit >= service.max_revision_rounds:
                escalated = service.revise_chapter(book_id, chapter_no, mode="surgical")
                status = escalated["status"]
                final_outcome = status.stage.value
            else:
                final_outcome = "audited_failed"
            break

        try:
            revise = service.revise_chapter(book_id, chapter_no, mode="surgical")
        except Exception as exc:
            status = service.get_chapter_status(book_id, chapter_no)["status"]
            return _build_full_cycle_failure(
                service=service,
                book_id=book_id,
                requested_book_id=requested_book_id,
                chapter_no=chapter_no,
                plan_id=plan_id,
                compose_id=compose_id,
                draft_id=draft_id,
                status=status,
                final_outcome="draft_generation_failed",
                platform=platform,
                audit_diagnostics_history=audit_diagnostics_history,
                truth_diagnostics_history=truth_diagnostics_history,
                comparison_history=comparison_history,
                error=str(exc),
            )
        status = revise["status"]
        if status.stage in {
            ChapterStage.HUMAN_REVIEW_REQUIRED,
            ChapterStage.INVALIDATED,
            ChapterStage.BLOCKED,
        }:
            final_outcome = status.stage.value
            break

        revised_draft_id = revise["draft_artifact_id"]
        settled = service.settle_chapter(book_id, chapter_no, revised_draft_id)
        status = settled
        try:
            audit, audit_diag, truth_diag = _audit_with_diagnostics(
                service,
                book_id,
                chapter_no,
                settled.current_artifact_refs["settlement"],
            )
        except Exception as exc:
            status = service.get_chapter_status(book_id, chapter_no)["status"]
            return _build_full_cycle_failure(
                service=service,
                book_id=book_id,
                requested_book_id=requested_book_id,
                chapter_no=chapter_no,
                plan_id=plan_id,
                compose_id=compose_id,
                draft_id=draft_id,
                status=status,
                final_outcome="audit_runtime_error",
                platform=platform,
                audit_diagnostics_history=audit_diagnostics_history,
                truth_diagnostics_history=truth_diagnostics_history,
                comparison_history=comparison_history,
                error=str(exc),
            )
        audit_diagnostics_history.append(audit_diag)
        truth_diagnostics_history.append(truth_diag)
        final_audit = audit
        status = audit["status"]
        candidate_gate_id = audit["gate_decision_artifact_id"]
        try:
            compare = service.compare_candidate(book_id, chapter_no, baseline_gate_id, candidate_gate_id)
        except Exception as exc:
            status = service.get_chapter_status(book_id, chapter_no)["status"]
            return _build_full_cycle_failure(
                service=service,
                book_id=book_id,
                requested_book_id=requested_book_id,
                chapter_no=chapter_no,
                plan_id=plan_id,
                compose_id=compose_id,
                draft_id=draft_id,
                status=status,
                final_outcome="compare_runtime_error",
                platform=platform,
                audit_diagnostics_history=audit_diagnostics_history,
                truth_diagnostics_history=truth_diagnostics_history,
                comparison_history=comparison_history,
                error=str(exc),
            )
        comparison_payload = service._load_artifact_payload(book_id, compare["comparison_artifact_id"])
        comparison_history.append(
            {
                "comparison_artifact_id": compare["comparison_artifact_id"],
                "style_signal_artifact_id": compare.get("style_signal_artifact_id"),
                "decision": compare["decision"],
                "reason_codes": comparison_payload.get("reason_codes", []),
                "delta_overall": comparison_payload.get("delta_overall"),
                "critical_delta": comparison_payload.get("critical_delta"),
            }
        )
        status = compare["status"]
        if compare["decision"] == "rollback":
            final_outcome = "rolled_back" if status.stage == ChapterStage.ROLLED_BACK else status.stage.value
            break
        if status.stage == ChapterStage.AUDITED_PASSED:
            baseline_gate_id = candidate_gate_id
            accepted_gate_id = candidate_gate_id
            continue
        if status.stage == ChapterStage.AUDITED_FAILED:
            baseline_gate_id = candidate_gate_id
            if status.revision_round >= auto_round_limit:
                if auto_round_limit >= service.max_revision_rounds:
                    escalated = service.revise_chapter(book_id, chapter_no, mode="surgical")
                    status = escalated["status"]
                    final_outcome = status.stage.value
                else:
                    final_outcome = "audited_failed"
                break
            continue
        final_outcome = status.stage.value
        break

    if final_outcome == "audited_failed" and status.stage == ChapterStage.AUDITED_FAILED and final_audit is not None:
        final_outcome = "audited_failed"

    return {
        "book_id": book_id,
        "requested_book_id": requested_book_id,
        "chapter_no": chapter_no,
        "plan_id": plan_id,
        "compose_id": compose_id,
        "draft_id": draft_id,
        "final_stage": status.stage.value,
        "final_outcome": final_outcome,
        "approved": status.stage in {ChapterStage.APPROVED, ChapterStage.EXPORTED},
        "exported": status.stage == ChapterStage.EXPORTED,
        "revision_rounds_executed": status.revision_round,
        "baseline_gate_id": baseline_gate_id,
        "accepted_gate_id": accepted_gate_id,
        "comparison_history": comparison_history,
        "style_signal_artifact_id": status.current_artifact_refs.get("style_signal"),
        "style_drift_axes": _load_style_drift_axes(service, book_id, status.current_artifact_refs.get("style_signal")),
        "audit_diagnostics_history": audit_diagnostics_history,
        "truth_diagnostics_history": truth_diagnostics_history,
        "approval_receipt_artifact_id": approval_receipt_artifact_id,
        "export_manifest_artifact_id": export_manifest_artifact_id,
        "artifacts": status.current_artifact_refs,
        "last_gate_passed": final_audit["gate_decision"]["passed"] if final_audit else None,
        "last_gate_reasons": final_audit["gate_decision"]["reason_codes"] if final_audit else [],
        "last_truth_delta_notes": final_audit["truth_delta"].get("notes", []) if final_audit else [],
        "platform": platform,
    }


def _build_full_cycle_failure(
    *,
    service: StoryEngineService,
    book_id: str,
    requested_book_id: str,
    chapter_no: int,
    plan_id: str | None,
    compose_id: str | None,
    draft_id: str | None,
    status,
    final_outcome: str,
    platform: str,
    audit_diagnostics_history: list[dict],
    truth_diagnostics_history: list[dict],
    comparison_history: list[dict],
    error: str,
) -> dict:
    return {
        "book_id": book_id,
        "requested_book_id": requested_book_id,
        "chapter_no": chapter_no,
        "plan_id": plan_id,
        "compose_id": compose_id,
        "draft_id": draft_id,
        "final_stage": status.stage.value,
        "final_outcome": final_outcome,
        "approved": status.stage in {ChapterStage.APPROVED, ChapterStage.EXPORTED},
        "exported": status.stage == ChapterStage.EXPORTED,
        "revision_rounds_executed": status.revision_round,
        "comparison_history": comparison_history,
        "style_signal_artifact_id": status.current_artifact_refs.get("style_signal"),
        "style_drift_axes": _load_style_drift_axes(service, book_id, status.current_artifact_refs.get("style_signal")),
        "audit_diagnostics_history": audit_diagnostics_history,
        "truth_diagnostics_history": truth_diagnostics_history,
        "approval_receipt_artifact_id": status.current_artifact_refs.get("approval_receipt"),
        "export_manifest_artifact_id": status.current_artifact_refs.get("export_manifest"),
        "artifacts": status.current_artifact_refs,
        "platform": platform,
        "error": error,
    }


def _load_style_drift_axes(service: StoryEngineService, book_id: str, artifact_id: str | None) -> list[str]:
    if not artifact_id:
        return []
    payload = service._optional_artifact_payload(book_id, artifact_id) or {}
    axes = payload.get("dominant_drift_axes", [])
    return axes if isinstance(axes, list) else []


def _ensure_smoke_book(service: StoryEngineService, book_id: str, title: str, chapter_no: int) -> tuple[str, str]:
    requested_book_id = book_id
    try:
        service.get_book(book_id)
    except Exception:
        service.create_book({"book_id": book_id, "title": title, "target_chapters": max(12, chapter_no)})
        service.init_chapter(book_id, chapter_no)
    else:
        status = service.get_chapter_status(book_id, chapter_no)["status"]
        if status.stage.value != "planned":
            book_id = f"{requested_book_id}-{uuid4().hex[:6]}"
            service.create_book({"book_id": book_id, "title": title, "target_chapters": max(12, chapter_no)})
            service.init_chapter(book_id, chapter_no)
    return book_id, requested_book_id


def _run_until_settled(service: StoryEngineService, book_id: str, chapter_no: int) -> tuple[str | None, str | None, str, object]:
    status = service.get_chapter_status(book_id, chapter_no)["status"]
    if status.stage.value == "planned":
        plan_id = service.plan_chapter(book_id, chapter_no, guidance="中文都市悬疑，节奏紧，避免报告腔，章尾留钩子")
    else:
        plan_id = status.current_artifact_refs.get("plan")
    status = service.get_chapter_status(book_id, chapter_no)["status"]
    if status.stage.value == "composed":
        compose_id = status.current_artifact_refs.get("compose")
    else:
        compose_id = service.compose_chapter(book_id, chapter_no)
    status = service.get_chapter_status(book_id, chapter_no)["status"]
    if status.stage.value in {"drafted", "revising"}:
        draft_id = status.current_artifact_refs.get("draft")
    else:
        draft_id = service.write_chapter_draft(book_id, chapter_no, mode="initial")
    settled = service.settle_chapter(book_id, chapter_no, draft_id)
    return plan_id, compose_id, draft_id, settled


def _audit_with_diagnostics(service: StoryEngineService, book_id: str, chapter_no: int, settlement_artifact_id: str) -> tuple[dict, dict, dict]:
    audit = service.audit_chapter(book_id, chapter_no, settlement_artifact_id)
    audit_diag = _extract_provider_diagnostics(service.gate_runner.auditor)
    truth_diag = _extract_provider_diagnostics(service.truth_extractor)
    return audit, audit_diag, truth_diag


def _extract_provider_diagnostics(component) -> dict:
    provider = getattr(component, "provider", None)
    diagnostics = getattr(provider, "last_diagnostics", None)
    if not isinstance(diagnostics, dict):
        return {
            "mode_used": None,
            "primary_error": None,
            "fallback_error": None,
            "raw_excerpt": None,
            "request_bytes": None,
        }
    return {
        "mode_used": diagnostics.get("mode_used"),
        "primary_error": diagnostics.get("primary_error"),
        "fallback_error": diagnostics.get("fallback_error"),
        "raw_excerpt": diagnostics.get("raw_excerpt"),
        "request_bytes": diagnostics.get("request_bytes"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
