from __future__ import annotations

import re

from engine.schemas.chapter import ChapterStage
from engine.schemas.intent import (
    IntentAction,
    IntentCheckResult,
    IntentExecResult,
    ParsedIntent,
)


_CN_DIGITS = "零一二三四五六七八九十百千万"
_CN_TO_INT = {c: i for i, c in enumerate("零一二三四五六七八九")}
_CHAPTER_RE = r"第\s*([0-9零一二三四五六七八九十百]+)\s*章"


def _cn_number_to_int(text: str) -> int | None:
    text = text.strip()
    if text.isdigit():
        return int(text)
    if len(text) == 1 and text in _CN_TO_INT:
        return _CN_TO_INT[text]
    if len(text) == 2 and text[0] in _CN_TO_INT and text[1] == "十":
        return _CN_TO_INT[text[0]] * 10
    if text == "十":
        return 10
    return None


INTENT_PATTERNS: list[tuple[str, IntentAction]] = [
    (r"继续.*?" + _CHAPTER_RE, IntentAction.CONTINUE_CHAPTER),
    (r"重审.*?" + _CHAPTER_RE, IntentAction.RE_AUDIT_CHAPTER),
    (_CHAPTER_RE + r".*?回滚", IntentAction.ROLLBACK_CHAPTER),
    (r"回滚.*?" + _CHAPTER_RE, IntentAction.ROLLBACK_CHAPTER),
    (r"批准.*?导出\s*(番茄|起点|晋江|知乎)", IntentAction.EXPORT_CHAPTER),
    (r"导出.*?(番茄|起点|晋江|知乎)", IntentAction.EXPORT_CHAPTER),
    (r"恢复.*?批量", IntentAction.RESUME_BATCH),
    (r"查.*?真相", IntentAction.QUERY_TRUTH),
    (r"查.*?门禁.*?失败", IntentAction.QUERY_GATE_FAILURE),
    (r"门禁.*?失败", IntentAction.QUERY_GATE_FAILURE),
    (r"查.*?章节.*?质量", IntentAction.QUERY_CHAPTER_QUALITY),
    (r"批准.*?" + _CHAPTER_RE, IntentAction.APPROVE_CHAPTER),
]

PLATFORM_MAP = {"番茄": "tomato", "起点": "qidian", "晋江": "jjwxc", "知乎": "zhihu"}

CONTINUE_ALLOWED_STAGES = {
    ChapterStage.PLANNED,
    ChapterStage.COMPOSED,
    ChapterStage.AUDITED_FAILED,
    ChapterStage.ROLLED_BACK,
    ChapterStage.HUMAN_REVIEW_REQUIRED,
}


class IntentCompilerService:
    def __init__(self, engine: object, batch_orchestrator: object | None = None) -> None:
        self.engine = engine
        self.batch = batch_orchestrator

    def parse(self, request: str, book_id: str) -> ParsedIntent | None:
        for pattern, action in INTENT_PATTERNS:
            match = re.search(pattern, request)
            if match:
                chapter_no = None
                parameters: dict = {}
                if action == IntentAction.EXPORT_CHAPTER:
                    platform_raw = match.group(1)
                    parameters["platform"] = PLATFORM_MAP.get(platform_raw, "tomato")
                    ch_match = re.search(_CHAPTER_RE, request)
                    if ch_match:
                        chapter_no = _cn_number_to_int(ch_match.group(1))
                elif action == IntentAction.RESUME_BATCH:
                    batch_match = re.search(r"(run[-_\w]*)", request)
                    if batch_match:
                        parameters["batch_run_id"] = batch_match.group(1)
                else:
                    for g in match.groups():
                        parsed = _cn_number_to_int(g)
                        if parsed is not None:
                            chapter_no = parsed
                            break
                return ParsedIntent(
                    action=action,
                    book_id=book_id,
                    chapter_no=chapter_no,
                    parameters=parameters,
                )
        return None

    def check(self, intent: ParsedIntent) -> IntentCheckResult:
        blockers: list[str] = []
        if intent.action in (
            IntentAction.QUERY_TRUTH,
            IntentAction.QUERY_GATE_FAILURE,
            IntentAction.QUERY_CHAPTER_QUALITY,
        ):
            return IntentCheckResult(allowed=True)
        if intent.action == IntentAction.RESUME_BATCH:
            return IntentCheckResult(allowed=True)
        chapter_no = intent.chapter_no
        if chapter_no is None:
            blockers.append("missing_chapter_number")
            return IntentCheckResult(allowed=False, blockers=blockers)
        try:
            status = self.engine.repo.load_chapter_status(intent.book_id, chapter_no)
        except Exception:
            blockers.append("chapter_not_found")
            return IntentCheckResult(allowed=False, blockers=blockers)
        stage = status.stage
        frozen_stages = {ChapterStage.APPROVED, ChapterStage.EXPORTED}
        if intent.action == IntentAction.CONTINUE_CHAPTER:
            if stage in frozen_stages:
                blockers.append("chapter_frozen")
            if stage == ChapterStage.INVALIDATED:
                blockers.append("chapter_invalidated")
            if stage == ChapterStage.BLOCKED:
                blockers.append("chapter_blocked")
            freshness = self.engine.get_chapter_truth_freshness(intent.book_id, chapter_no)
            if freshness and not freshness.get("is_fresh", True):
                blockers.append("truth_stale")
        elif intent.action == IntentAction.RE_AUDIT_CHAPTER:
            if stage != ChapterStage.SETTLED:
                blockers.append(f"stage_not_settled:{stage}")
        elif intent.action == IntentAction.ROLLBACK_CHAPTER:
            if "chapter_quality" not in status.current_artifact_refs:
                blockers.append("no_quality_record")
        elif intent.action == IntentAction.APPROVE_CHAPTER:
            if stage != ChapterStage.AUDITED_PASSED:
                blockers.append(f"stage_not_audited_passed:{stage}")
            notes = self.engine.repo.json.load_chapter_note(intent.book_id, chapter_no)
            if notes.get("pending_comparison"):
                blockers.append("pending_comparison")
        elif intent.action == IntentAction.EXPORT_CHAPTER:
            if stage not in {ChapterStage.APPROVED, ChapterStage.EXPORTED}:
                blockers.append(f"stage_not_approved_or_exported:{stage}")
        return IntentCheckResult(allowed=not blockers, blockers=blockers)

    def execute(self, intent: ParsedIntent, dry_run: bool = False) -> IntentExecResult:
        check_result = self.check(intent)
        if not check_result.allowed:
            return IntentExecResult(
                success=False,
                action=intent.action,
                message=f"blocked: {', '.join(check_result.blockers)}",
            )
        if dry_run:
            return IntentExecResult(
                success=True,
                action=intent.action,
                message="dry_run: checks passed, no action taken",
            )
        try:
            return self._dispatch(intent)
        except Exception as exc:
            return IntentExecResult(
                success=False,
                action=intent.action,
                message=f"execution_error: {exc}",
            )

    def _dispatch(self, intent: ParsedIntent) -> IntentExecResult:
        action = intent.action
        book_id = intent.book_id
        chapter_no = intent.chapter_no
        if action == IntentAction.CONTINUE_CHAPTER:
            return self._continue_chapter(book_id, chapter_no)
        if action == IntentAction.RE_AUDIT_CHAPTER:
            return self._re_audit(book_id, chapter_no)
        if action == IntentAction.ROLLBACK_CHAPTER:
            return self._rollback(book_id, chapter_no)
        if action == IntentAction.APPROVE_CHAPTER:
            return self._approve(book_id, chapter_no)
        if action == IntentAction.EXPORT_CHAPTER:
            return self._export(book_id, chapter_no, intent.parameters.get("platform", "tomato"))
        if action == IntentAction.RESUME_BATCH:
            return self._resume_batch(intent.parameters.get("batch_run_id"))
        if action == IntentAction.QUERY_TRUTH:
            return self._query_truth(book_id, chapter_no)
        if action == IntentAction.QUERY_GATE_FAILURE:
            return self._query_gate_failure(book_id, chapter_no)
        if action == IntentAction.QUERY_CHAPTER_QUALITY:
            return self._query_quality(book_id, chapter_no)
        return IntentExecResult(success=False, action=action, message="unknown_action")

    def _continue_chapter(self, book_id: str, chapter_no: int) -> IntentExecResult:
        status = self.engine.repo.load_chapter_status(book_id, chapter_no)
        stage = status.stage
        refs: list[str] = []
        if stage == ChapterStage.PLANNED:
            plan_id = self.engine.plan_chapter(book_id, chapter_no)
            refs.append(plan_id)
            stage = ChapterStage.COMPOSED
        if stage == ChapterStage.COMPOSED:
            compose_id = self.engine.compose_chapter(book_id, chapter_no)
            refs.append(compose_id)
            draft_id = self.engine.write_chapter_draft(book_id, chapter_no)
            refs.append(draft_id)
            status = self.engine.repo.load_chapter_status(book_id, chapter_no)
            draft_ref = status.current_artifact_refs.get("draft", draft_id)
            result = self.engine.settle_chapter(book_id, chapter_no, draft_ref)
            refs.append(result.current_artifact_refs.get("settlement", ""))
            stage = ChapterStage.SETTLED
            status = self.engine.repo.load_chapter_status(book_id, chapter_no)
        if stage == ChapterStage.SETTLED:
            settle_ref = status.current_artifact_refs.get("settlement", "")
            audit_result = self.engine.audit_chapter(book_id, chapter_no, settle_ref)
            refs.append(audit_result.get("gate_decision_artifact_id", ""))
        elif stage in {ChapterStage.AUDITED_FAILED, ChapterStage.ROLLED_BACK, ChapterStage.HUMAN_REVIEW_REQUIRED}:
            revise_mode = "surgical" if stage == ChapterStage.AUDITED_FAILED else "rework"
            revise_result = self.engine.revise_chapter(book_id, chapter_no, mode=revise_mode)
            refs.append(revise_result.get("revision_brief_artifact_id", ""))
            if revise_result.get("draft_artifact_id"):
                refs.append(revise_result.get("draft_artifact_id", ""))
        return IntentExecResult(
            success=True,
            action=IntentAction.CONTINUE_CHAPTER,
            result_refs=refs,
            message=f"continued chapter {chapter_no}",
        )

    def _re_audit(self, book_id: str, chapter_no: int) -> IntentExecResult:
        status = self.engine.repo.load_chapter_status(book_id, chapter_no)
        settle_ref = status.current_artifact_refs.get("settlement", "")
        result = self.engine.audit_chapter(book_id, chapter_no, settle_ref)
        return IntentExecResult(
            success=True,
            action=IntentAction.RE_AUDIT_CHAPTER,
            result_refs=[result.get("gate_decision_artifact_id", "")],
            message=f"re-audited chapter {chapter_no}",
        )

    def _rollback(self, book_id: str, chapter_no: int) -> IntentExecResult:
        status = self.engine.repo.load_chapter_status(book_id, chapter_no)
        baseline_gate = status.current_artifact_refs.get("gate_decision", "")
        candidate_gate = status.current_artifact_refs.get("gate_decision", "")
        result = self.engine.compare_candidate(book_id, chapter_no, baseline_gate, candidate_gate)
        return IntentExecResult(
            success=True,
            action=IntentAction.ROLLBACK_CHAPTER,
            result_refs=[result.get("comparison_artifact_id", "")],
            message=f"rollback evaluated for chapter {chapter_no}",
        )

    def _approve(self, book_id: str, chapter_no: int) -> IntentExecResult:
        result = self.engine.approve_chapter(book_id, chapter_no)
        receipt_ref = ""
        if hasattr(result, "current_artifact_refs"):
            receipt_ref = result.current_artifact_refs.get("approval_receipt", "")
        return IntentExecResult(
            success=True,
            action=IntentAction.APPROVE_CHAPTER,
            result_refs=[receipt_ref],
            message=f"approved chapter {chapter_no}",
        )

    def _export(self, book_id: str, chapter_no: int, platform: str) -> IntentExecResult:
        result = self.engine.export_chapter(book_id, chapter_no, platform=platform)
        return IntentExecResult(
            success=True,
            action=IntentAction.EXPORT_CHAPTER,
            result_refs=[result.get("manifest_artifact_id", "")],
            message=f"exported chapter {chapter_no} to {platform}",
        )

    def _resume_batch(self, batch_run_id: str | None) -> IntentExecResult:
        if not self.batch or not batch_run_id:
            return IntentExecResult(
                success=False,
                action=IntentAction.RESUME_BATCH,
                message="no batch orchestrator or batch_run_id",
            )
        result = self.batch.resume_batch_run(batch_run_id)
        return IntentExecResult(
            success=True,
            action=IntentAction.RESUME_BATCH,
            message=f"resumed batch {batch_run_id}",
            result_refs=[str(result.get("batch_run_id", ""))],
        )

    def _query_truth(self, book_id: str, chapter_no: int | None) -> IntentExecResult:
        truth_head = self.engine.get_truth_head(book_id)
        snapshot_id = truth_head.get("truth_index", {}).get("current_snapshot_id", "none") if truth_head else "none"
        return IntentExecResult(
            success=True,
            action=IntentAction.QUERY_TRUTH,
            message=f"truth head: {snapshot_id}",
        )

    def _query_gate_failure(self, book_id: str, chapter_no: int) -> IntentExecResult:
        status = self.engine.repo.load_chapter_status(book_id, chapter_no)
        quality_ref = status.current_artifact_refs.get("chapter_quality", "")
        if quality_ref:
            payload = self.engine._load_artifact_payload(book_id, quality_ref)
            reasons = payload.get("reason_codes", [])
            return IntentExecResult(
                success=True,
                action=IntentAction.QUERY_GATE_FAILURE,
                message=f"gate failure reasons: {reasons}",
            )
        return IntentExecResult(
            success=True,
            action=IntentAction.QUERY_GATE_FAILURE,
            message="no quality record found",
        )

    def _query_quality(self, book_id: str, chapter_no: int) -> IntentExecResult:
        status = self.engine.repo.load_chapter_status(book_id, chapter_no)
        quality_ref = status.current_artifact_refs.get("chapter_quality", "")
        if quality_ref:
            payload = self.engine._load_artifact_payload(book_id, quality_ref)
            overall = payload.get("overall_score", "n/a")
            return IntentExecResult(
                success=True,
                action=IntentAction.QUERY_CHAPTER_QUALITY,
                message=f"quality score: {overall}",
            )
        return IntentExecResult(
            success=True,
            action=IntentAction.QUERY_CHAPTER_QUALITY,
            message="no quality record found",
        )
