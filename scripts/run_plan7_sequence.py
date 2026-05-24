from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.cli.main import _audit_with_diagnostics, _load_style_drift_axes  # noqa: E402
from engine.schemas.chapter import ChapterStage  # noqa: E402
from engine.services import StoryEngineService  # noqa: E402


PROVIDER_ERROR_PATTERNS = (
    r"HTTP Error 502",
    r"HTTP Error 504",
    r"timeout",
    r"timed out",
    r"gateway time-?out",
    r"Expecting value: line 1 column 1 \(char 0\)",
    r"JSONDecodeError",
)


def is_provider_fault(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(re.search(pattern.lower(), lowered) for pattern in PROVIDER_ERROR_PATTERNS)


def ensure_chapter_exists(service: StoryEngineService, book_id: str, chapter_no: int) -> None:
    try:
        service.get_chapter_status(book_id, chapter_no)
    except Exception:
        service.init_chapter(book_id, chapter_no)


def clear_pending_comparison(service: StoryEngineService, book_id: str, chapter_no: int) -> None:
    """Ensure pending_comparison is cleared after successful compare."""
    service.repo.json.append_chapter_note(book_id, chapter_no, {"pending_comparison": False})


def is_truth_stale(service: StoryEngineService, book_id: str, chapter_no: int) -> bool:
    """Check if chapter's truth basis is stale against current truth head."""
    status = service.repo.load_chapter_status(book_id, chapter_no)
    compose_id = status.current_artifact_refs.get("compose")
    if not compose_id:
        return False  # Not composed yet, can't be stale
    compose_payload = service._optional_artifact_payload(book_id, compose_id) or {}
    basis_snapshot_id = compose_payload.get("truth_snapshot_id")
    truth_index = service.repo.json.load_truth_index(book_id)
    current_snapshot_id = truth_index.current_snapshot_id
    if basis_snapshot_id and basis_snapshot_id != current_snapshot_id:
        return True
    return False


def re_compose_if_stale(service: StoryEngineService, book_id: str, chapter_no: int) -> dict:
    """Re-compose chapter if truth is stale. Returns updated status."""
    if is_truth_stale(service, book_id, chapter_no):
        # Re-compose with current truth
        compose_id = service.compose_chapter(book_id, chapter_no)
        return service.get_chapter_status(book_id, chapter_no)
    return service.get_chapter_status(book_id, chapter_no)


def write_result(run_dir: Path, chapter_no: int, result: dict) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"chapter-{chapter_no:04d}.full_cycle.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_summary(results: list[dict]) -> tuple[dict, str]:
    summary = {
        "book_id": results[0]["book_id"] if results else None,
        "chapters": results,
    }
    lines = [
        "# PLAN7 Run Summary",
        "",
        "| Chapter | Final Outcome | Final Stage | Revisions | Style Drift | Error |",
        "|---|---|---|---:|---|---|",
    ]
    for item in results:
        drift = ", ".join(item.get("style_drift_axes", [])) or "-"
        error = item.get("error") or "-"
        lines.append(
            f"| {item['chapter_no']} | {item['final_outcome']} | {item['final_stage']} | "
            f"{item.get('revision_rounds_executed', 0)} | {drift} | {error} |"
        )
    return summary, "\n".join(lines) + "\n"


def in_place_full_cycle(
    service: StoryEngineService,
    *,
    book_id: str,
    title: str,
    chapter_no: int,
    platform: str,
    max_auto_rounds: int,
) -> dict:
    status = service.get_chapter_status(book_id, chapter_no)["status"]
    if status.stage == ChapterStage.INVALIDATED:
        service.reset_invalidated_chapter(book_id, chapter_no)
        status = service.get_chapter_status(book_id, chapter_no)["status"]

    plan_id = status.current_artifact_refs.get("plan")
    compose_id = status.current_artifact_refs.get("compose")
    draft_id = status.current_artifact_refs.get("draft")
    settled = None
    audit_diagnostics_history: list[dict] = []
    truth_diagnostics_history: list[dict] = []
    comparison_history: list[dict] = []
    approval_receipt_artifact_id: str | None = status.current_artifact_refs.get("approval_receipt")
    export_manifest_artifact_id: str | None = status.current_artifact_refs.get("export_manifest")
    final_audit: dict | None = None

    def resolve_baseline_gate_id(current_status) -> str | None:
        comparison_id = current_status.current_artifact_refs.get("comparison")
        if comparison_id:
            comparison_payload = service._optional_artifact_payload(book_id, comparison_id) or {}
            baseline_gate_id = comparison_payload.get("baseline_gate_id")
            if baseline_gate_id:
                return baseline_gate_id
        return current_status.current_artifact_refs.get("gate_decision")

    def failure(final_outcome: str, error: str) -> dict:
        current_status = service.get_chapter_status(book_id, chapter_no)["status"]
        compare_payload = None
        compare_id = current_status.current_artifact_refs.get("comparison")
        if compare_id:
            compare_payload = service._optional_artifact_payload(book_id, compare_id)
        return {
            "chapter_no": chapter_no,
            "book_id": book_id,
            "final_stage": current_status.stage.value,
            "final_outcome": final_outcome,
            "approved": current_status.stage in {ChapterStage.APPROVED, ChapterStage.EXPORTED},
            "exported": current_status.stage == ChapterStage.EXPORTED,
            "revision_rounds_executed": current_status.revision_round,
            "comparison_history": comparison_history or (
                [{
                    "comparison_artifact_id": compare_id,
                    "style_signal_artifact_id": current_status.current_artifact_refs.get("style_signal"),
                    "decision": compare_payload.get("decision"),
                    "reason_codes": compare_payload.get("reason_codes", []),
                    "delta_overall": compare_payload.get("delta_overall"),
                    "critical_delta": compare_payload.get("critical_delta"),
                }] if compare_payload else []
            ),
            "last_gate_reasons": final_audit["gate_decision"]["reason_codes"] if final_audit else [],
            "last_truth_delta_notes": final_audit["truth_delta"].get("notes", []) if final_audit else [],
            "style_drift_axes": _load_style_drift_axes(
                service, book_id, current_status.current_artifact_refs.get("style_signal")
            ),
            "audit_diagnostics_history": audit_diagnostics_history,
            "truth_diagnostics_history": truth_diagnostics_history,
            "error": error,
            "approval_receipt_artifact_id": current_status.current_artifact_refs.get("approval_receipt"),
            "export_manifest_artifact_id": current_status.current_artifact_refs.get("export_manifest"),
        }

    if status.stage == ChapterStage.EXPORTED:
        return {
            "chapter_no": chapter_no,
            "book_id": book_id,
            "final_stage": status.stage.value,
            "final_outcome": status.stage.value,
            "approved": True,
            "exported": True,
            "revision_rounds_executed": status.revision_round,
            "comparison_history": [],
            "last_gate_reasons": [],
            "last_truth_delta_notes": [],
            "style_drift_axes": _load_style_drift_axes(service, book_id, status.current_artifact_refs.get("style_signal")),
            "audit_diagnostics_history": [],
            "truth_diagnostics_history": [],
            "error": None,
            "approval_receipt_artifact_id": status.current_artifact_refs.get("approval_receipt"),
            "export_manifest_artifact_id": status.current_artifact_refs.get("export_manifest"),
        }

    def hydrate_from_status(current_status) -> None:
        nonlocal final_audit
        compare_id = current_status.current_artifact_refs.get("comparison")
        if compare_id and not comparison_history:
            compare_payload = service._optional_artifact_payload(book_id, compare_id) or {}
            comparison_history.append(
                {
                    "comparison_artifact_id": compare_id,
                    "style_signal_artifact_id": current_status.current_artifact_refs.get("style_signal"),
                    "decision": compare_payload.get("decision"),
                    "reason_codes": compare_payload.get("reason_codes", []),
                    "delta_overall": compare_payload.get("delta_overall"),
                    "critical_delta": compare_payload.get("critical_delta"),
                }
            )
        if final_audit is None:
            audit_id = current_status.current_artifact_refs.get("audit")
            gate_id = current_status.current_artifact_refs.get("gate_decision")
            truth_delta_id = current_status.current_artifact_refs.get("truth_delta")
            if audit_id and gate_id and truth_delta_id:
                final_audit = {
                    "gate_decision": service._optional_artifact_payload(book_id, gate_id) or {},
                    "truth_delta": service._optional_artifact_payload(book_id, truth_delta_id) or {},
                    "audit_report": service._optional_artifact_payload(book_id, audit_id) or {},
                }

    try:
        if status.stage in {ChapterStage.ROLLED_BACK, ChapterStage.HUMAN_REVIEW_REQUIRED}:
            baseline_gate_id = resolve_baseline_gate_id(status)
            auto_rounds = 0
            while status.stage in {ChapterStage.ROLLED_BACK, ChapterStage.HUMAN_REVIEW_REQUIRED} and auto_rounds < max_auto_rounds:
                revise = service.revise_chapter(book_id, chapter_no, mode="rework")
                status = revise["status"]
                settled = service.settle_chapter(book_id, chapter_no, revise["draft_artifact_id"])
                audit, audit_diag, truth_diag = _audit_with_diagnostics(
                    service, book_id, chapter_no, settled.current_artifact_refs["settlement"]
                )
                audit_diagnostics_history.append(audit_diag)
                truth_diagnostics_history.append(truth_diag)
                final_audit = audit
                compare = service.compare_candidate(book_id, chapter_no, baseline_gate_id, audit["gate_decision_artifact_id"])
                clear_pending_comparison(service, book_id, chapter_no)
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
                if compare["decision"] == "keep":
                    baseline_gate_id = audit["gate_decision_artifact_id"]
                auto_rounds += 1
                if status.stage == ChapterStage.AUDITED_PASSED:
                    break
        if status.stage == ChapterStage.PLANNED:
            plan_id = service.plan_chapter(book_id, chapter_no, guidance="中文都市悬疑，节奏紧，避免报告腔，章尾留钩子")
            status = service.get_chapter_status(book_id, chapter_no)["status"]
        if status.stage == ChapterStage.COMPOSED or (status.stage == ChapterStage.PLANNED and not compose_id):
            compose_id = status.current_artifact_refs.get("compose") or service.compose_chapter(book_id, chapter_no)
            status = service.get_chapter_status(book_id, chapter_no)["status"]
        if status.stage == ChapterStage.COMPOSED:
            draft_id = service.write_chapter_draft(book_id, chapter_no, mode="initial")
            status = service.get_chapter_status(book_id, chapter_no)["status"]
        if status.stage in {ChapterStage.DRAFTED, ChapterStage.REVISING}:
            draft_id = status.current_artifact_refs["draft"]
            try:
                settled = service.settle_chapter(book_id, chapter_no, draft_id)
                status = settled
            except ValueError as e:
                if "stale against truth head" in str(e):
                    # Truth is stale - invalidate first, then reset
                    service.mark_invalidated(book_id, chapter_no, "truth_stale", "truth snapshot out of sync")
                    service.reset_invalidated_chapter(book_id, chapter_no)
                    status = service.get_chapter_status(book_id, chapter_no)["status"]
                    # Continue to compose below
                else:
                    raise
        if status.stage == ChapterStage.SETTLED:
            audit, audit_diag, truth_diag = _audit_with_diagnostics(
                service, book_id, chapter_no, status.current_artifact_refs["settlement"]
            )
            audit_diagnostics_history.append(audit_diag)
            truth_diagnostics_history.append(truth_diag)
            final_audit = audit
            status = audit["status"]
        auto_rounds = 0
        baseline_gate_id = resolve_baseline_gate_id(status)
        while status.stage in {ChapterStage.AUDITED_FAILED, ChapterStage.ROLLED_BACK, ChapterStage.HUMAN_REVIEW_REQUIRED} and auto_rounds < max_auto_rounds:
            revise_mode = "surgical" if status.stage == ChapterStage.AUDITED_FAILED else "rework"
            revise = service.revise_chapter(book_id, chapter_no, mode=revise_mode)
            status = revise["status"]
            settled = service.settle_chapter(book_id, chapter_no, revise["draft_artifact_id"])
            audit, audit_diag, truth_diag = _audit_with_diagnostics(
                service, book_id, chapter_no, settled.current_artifact_refs["settlement"]
            )
            audit_diagnostics_history.append(audit_diag)
            truth_diagnostics_history.append(truth_diag)
            final_audit = audit
            compare = service.compare_candidate(book_id, chapter_no, baseline_gate_id, audit["gate_decision_artifact_id"])
            clear_pending_comparison(service, book_id, chapter_no)
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
            baseline_gate_id = audit["gate_decision_artifact_id"] if compare["decision"] == "keep" else baseline_gate_id
            auto_rounds += 1
            if compare["decision"] == "rollback":
                if auto_rounds >= max_auto_rounds:
                    break
                continue
        if status.stage == ChapterStage.AUDITED_PASSED:
            approved = service.approve_chapter(book_id, chapter_no)
            approval_receipt_artifact_id = approved.current_artifact_refs.get("approval_receipt")
            exported = service.export_chapter(book_id, chapter_no, platform=platform)
            export_manifest_artifact_id = exported["export_manifest_artifact_id"]
            status = exported["status"]
        elif status.stage == ChapterStage.APPROVED:
            exported = service.export_chapter(book_id, chapter_no, platform=platform)
            export_manifest_artifact_id = exported["export_manifest_artifact_id"]
            status = exported["status"]
    except Exception as exc:
        outcome = "provider_fault" if is_provider_fault(str(exc)) else "engine_fault"
        hydrate_from_status(service.get_chapter_status(book_id, chapter_no)["status"])
        return failure(outcome, str(exc))

    current_status = service.get_chapter_status(book_id, chapter_no)["status"]
    hydrate_from_status(current_status)
    return {
        "chapter_no": chapter_no,
        "book_id": book_id,
        "final_stage": current_status.stage.value,
        "final_outcome": current_status.stage.value,
        "approved": current_status.stage in {ChapterStage.APPROVED, ChapterStage.EXPORTED},
        "exported": current_status.stage == ChapterStage.EXPORTED,
        "revision_rounds_executed": current_status.revision_round,
        "comparison_history": comparison_history,
        "last_gate_reasons": final_audit["gate_decision"]["reason_codes"] if final_audit else [],
        "last_truth_delta_notes": final_audit["truth_delta"].get("notes", []) if final_audit else [],
        "style_drift_axes": _load_style_drift_axes(service, book_id, current_status.current_artifact_refs.get("style_signal")),
        "audit_diagnostics_history": audit_diagnostics_history,
        "truth_diagnostics_history": truth_diagnostics_history,
        "error": None,
        "approval_receipt_artifact_id": approval_receipt_artifact_id,
        "export_manifest_artifact_id": export_manifest_artifact_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="run_plan7_sequence")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--book-id", default="plan6-seq-run")
    parser.add_argument("--title", default="PLAN6 Sequential Run")
    parser.add_argument("--chapter-from", type=int, default=6)
    parser.add_argument("--chapter-to", type=int, default=10)
    parser.add_argument("--platform", default="tomato")
    parser.add_argument("--max-auto-rounds", type=int, default=2)
    parser.add_argument("--max-provider-retries", type=int, default=2)
    args = parser.parse_args()

    service = StoryEngineService(Path(args.root), enable_real_provider=True)
    service.get_book(args.book_id)

    run_dir = Path(args.root) / "docs" / "plan7-runs" / args.book_id
    results: list[dict] = []

    for chapter_no in range(args.chapter_from, args.chapter_to + 1):
        ensure_chapter_exists(service, args.book_id, chapter_no)
        attempt = 0
        while True:
            result = in_place_full_cycle(
                service,
                book_id=args.book_id,
                title=args.title,
                chapter_no=chapter_no,
                platform=args.platform,
                max_auto_rounds=args.max_auto_rounds,
            )
            if result["final_outcome"] == "provider_fault" and attempt < args.max_provider_retries:
                attempt += 1
                result["provider_retry_attempt"] = attempt
                continue
            results.append(result)
            write_result(run_dir, chapter_no, result)
            if result["final_outcome"] in {"engine_fault", "provider_fault", "invalidated", "rolled_back", "human_review_required"}:
                summary, summary_md = build_summary(results)
                (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                (run_dir / "summary.md").write_text(summary_md, encoding="utf-8")
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 1
            break

    summary, summary_md = build_summary(results)
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "summary.md").write_text(summary_md, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
