from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from engine.providers import ResponsesAPIProvider
from engine.providers.fake_llm_provider import FakeLLMProvider
from engine.truth import TruthExtractorAdapter
from engine.schemas.book import BookRecord
from engine.schemas.chapter import ChapterStage, ChapterStatusRecord
from engine.schemas.run import RunAction, RunRecord, RunStatus
from engine.schemas.artifact import RevisionRecord
from engine.state_machine import next_status
from engine.storage import StoryForgeRepository
from .chapter_workflow import ChapterWorkflowService
from .chapter_writer import DEFAULT_WRITER_GENERATION_CONFIG, PlaceholderChapterWriter, RealChapterWriter
from .export_service import ExportService
from .gate_runner import FallbackAuditor, GateRunner
from .style_signal import StyleSignalAdapter
from .truth_service import TruthService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StoryEngineService:
    """Public service surface for StoryForge2 engine core."""

    def __init__(self, root: str | Path, *, enable_real_provider: bool = False) -> None:
        self.root = Path(root)
        self._repo = StoryForgeRepository(self.root)
        self._gate_runner = GateRunner(enable_real_provider=enable_real_provider)
        self._truth_fallback_auditor = FallbackAuditor()
        self._style_signal = StyleSignalAdapter()
        self.max_revision_rounds = 5
        self.plateau_delta = 0.5
        self.plateau_limit = 2
        writer = (
            RealChapterWriter(
                provider=ResponsesAPIProvider(),
                generation_config=dict(DEFAULT_WRITER_GENERATION_CONFIG),
            )
            if enable_real_provider
            else PlaceholderChapterWriter()
        )
        self._truth_service = TruthService(
            repo=self._repo,
            register_artifact=self.register_artifact,
            start_run=self.start_run,
            finish_run=self.finish_run,
            load_artifact_payload=self._load_artifact_payload,
            artifact_record=self._artifact_record,
            truth_extractor=(
                TruthExtractorAdapter(ResponsesAPIProvider())
                if enable_real_provider
                else TruthExtractorAdapter(FakeLLMProvider(json_errors=["real_provider_disabled"]))
            ),
            utc_now=utc_now,
        )
        self._export_service = ExportService(
            repo=self._repo,
            register_artifact=self.register_artifact,
            start_run=self.start_run,
            finish_run=self.finish_run,
            load_artifact_payload=self._load_artifact_payload,
            assert_truth_fresh_for_action=self._truth_service._assert_truth_fresh_for_action,
            utc_now=utc_now,
        )
        self._chapter_workflow = ChapterWorkflowService(
            repo=self._repo,
            gate_runner=self._gate_runner,
            style_signal=self._style_signal,
            writer=writer,
            truth_service=self._truth_service,
            register_artifact=self.register_artifact,
            start_run=self.start_run,
            finish_run=self.finish_run,
            load_artifact_payload=self._load_artifact_payload,
            optional_artifact_payload=self._optional_artifact_payload,
            max_revision_rounds=self.max_revision_rounds,
            plateau_delta=self.plateau_delta,
            plateau_limit=self.plateau_limit,
            utc_now=utc_now,
        )

    @property
    def repo(self):
        return self._repo

    @property
    def gate_runner(self):
        return self._gate_runner

    @property
    def truth_extractor(self):
        return self._truth_service.truth_extractor

    @truth_extractor.setter
    def truth_extractor(self, value) -> None:
        self._truth_service.truth_extractor = value

    def create_book(self, payload: dict) -> BookRecord:
        record = BookRecord.model_validate(payload)
        self.repo.create_book(record)
        return record

    def get_book(self, book_id: str) -> dict:
        book = self.repo.load_book(book_id)
        chapter_index = self.repo.json.load_book_index(book_id)
        return {"book": book, "chapter_index": chapter_index}

    def list_batch_runs(self, book_id: str, status: str | None = None) -> list[dict]:
        runs = self.repo.list_batch_runs(book_id)
        if status is not None:
            runs = [run for run in runs if run.status.value == status]
        return [run.model_dump(mode="json") for run in runs]

    def get_truth_head(self, book_id: str) -> dict:
        return self._truth_service.get_truth_head(book_id)

    def init_chapter(self, book_id: str, chapter_no: int) -> ChapterStatusRecord:
        _ = self.repo.load_book(book_id)
        record = ChapterStatusRecord(book_id=book_id, chapter_no=chapter_no)
        self.repo.save_chapter_status(record)
        return record

    def get_chapter_status(self, book_id: str, chapter_no: int) -> dict:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        return {"status": status, "notes": self.repo.json.load_chapter_note(book_id, chapter_no)}

    def list_propagation_debts(self, book_id: str, status: str | None = None) -> list[dict]:
        return self._truth_service.list_propagation_debts(book_id, status=status)

    def get_chapter_truth_freshness(self, book_id: str, chapter_no: int) -> dict:
        return self._truth_service.get_chapter_truth_freshness(book_id, chapter_no)

    def reset_invalidated_chapter(self, book_id: str, chapter_no: int) -> ChapterStatusRecord:
        return self._truth_service.reset_invalidated_chapter(book_id, chapter_no)

    def invalidate_downstream(self, book_id: str, from_artifact: str) -> dict:
        return self._truth_service.invalidate_downstream(book_id, from_artifact)

    def start_run(
        self,
        book_id: str,
        chapter_no: int,
        action: str,
        actor_role: str,
        input_refs: list[str] | None = None,
    ) -> RunRecord:
        run = RunRecord(
            run_id=str(uuid4()),
            book_id=book_id,
            chapter_no=chapter_no,
            stage_action=RunAction(action),
            actor_role=actor_role,
            input_refs=input_refs or [],
        )
        self.repo.save_run(run)
        return run

    def register_artifact(
        self,
        book_id: str,
        chapter_no: int,
        artifact_type: str,
        payload: dict,
        run_id: str,
    ) -> str:
        artifact_id = f"{chapter_no:04d}-{artifact_type}-{uuid4().hex[:8]}"
        record = self.repo.json.register_artifact(
            book_id=book_id,
            chapter_no=chapter_no,
            artifact_type=artifact_type,
            run_id=run_id,
            payload=payload,
            artifact_id=artifact_id,
        )
        self.repo.save_artifact(record)
        return record.artifact_id

    def transition_chapter(
        self,
        book_id: str,
        chapter_no: int,
        target_stage: str,
        run_id: str,
        artifact_refs: dict[str, str] | None = None,
        metadata: dict | None = None,
    ) -> ChapterStatusRecord:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        metadata = metadata or {}
        next_record = next_status(
            status,
            ChapterStage(target_stage),
            run_id=run_id,
            artifact_refs=artifact_refs or {},
            blocked_reason=metadata.get("blocked_reason"),
            invalidated_by=metadata.get("invalidated_by"),
        )
        self.repo.save_chapter_status(next_record)
        return next_record

    def finish_run(
        self,
        book_id: str,
        chapter_no: int,
        run_id: str,
        outcome: str,
        output_refs: list[str],
        error_summary: str | None = None,
    ) -> RunRecord:
        run = self.repo.json.load_run_record(book_id, run_id, chapter_no)
        finished = run.model_copy(
            update={
                "status": RunStatus(outcome),
                "output_refs": output_refs,
                "finished_at": utc_now(),
                "error_summary": error_summary,
            }
        )
        self.repo.save_run(finished)
        return finished

    def mark_invalidated(self, book_id: str, chapter_no: int, source_ref: str, reason: str) -> ChapterStatusRecord:
        run = self.start_run(book_id, chapter_no, RunAction.INVALIDATE.value, "system", [source_ref])
        status = self.transition_chapter(
            book_id,
            chapter_no,
            ChapterStage.INVALIDATED.value,
            run.run_id,
            metadata={"invalidated_by": source_ref},
        )
        self.repo.json.append_invalidation_entry(
            book_id,
            {
                "chapter_no": chapter_no,
                "source_ref": source_ref,
                "reason": reason,
                "run_id": run.run_id,
                "created_at": utc_now().isoformat(),
            },
        )
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [], None)
        return status

    def mark_blocked(self, book_id: str, chapter_no: int, reason: str) -> ChapterStatusRecord:
        run = self.start_run(book_id, chapter_no, RunAction.BLOCK.value, "system", [])
        status = self.transition_chapter(
            book_id,
            chapter_no,
            ChapterStage.BLOCKED.value,
            run.run_id,
            metadata={"blocked_reason": reason},
        )
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [], None)
        return status

    def rollback_chapter(self, book_id: str, chapter_no: int, revision_record: dict) -> ChapterStatusRecord:
        record = RevisionRecord.model_validate(revision_record)
        status = self.repo.load_chapter_status(book_id, chapter_no)
        if record.kept:
            raise ValueError("rollback_chapter requires a discarded revision decision")
        run = self.start_run(book_id, chapter_no, RunAction.ROLLBACK.value, "system", [record.base_artifact_id])
        next_record = next_status(
            status,
            ChapterStage.ROLLED_BACK,
            run_id=run.run_id,
            artifact_refs={"revision": record.base_artifact_id},
        )
        self.repo.save_chapter_status(next_record)
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [record.base_artifact_id], None)
        return next_record

    def rebuild_read_model(self, book_id: str) -> None:
        self.repo.rebuild_read_model(book_id)

    def plan_chapter(self, book_id: str, chapter_no: int, guidance: str | None = None) -> str:
        return self._chapter_workflow.plan_chapter(book_id, chapter_no, guidance)

    def compose_chapter(self, book_id: str, chapter_no: int) -> str:
        return self._chapter_workflow.compose_chapter(book_id, chapter_no)

    def write_chapter_draft(self, book_id: str, chapter_no: int, mode: str = "initial") -> str:
        return self._chapter_workflow.write_chapter_draft(book_id, chapter_no, mode=mode)

    def settle_chapter(self, book_id: str, chapter_no: int, draft_artifact_id: str) -> ChapterStatusRecord:
        return self._chapter_workflow.settle_chapter(book_id, chapter_no, draft_artifact_id)

    def audit_chapter(self, book_id: str, chapter_no: int, settlement_artifact_id: str) -> dict:
        return self._chapter_workflow.audit_chapter(book_id, chapter_no, settlement_artifact_id)

    def revise_chapter(self, book_id: str, chapter_no: int, mode: str | None = None) -> dict:
        return self._chapter_workflow.revise_chapter(book_id, chapter_no, mode=mode)

    def compare_candidate(self, book_id: str, chapter_no: int, baseline_gate_id: str, candidate_gate_id: str) -> dict:
        return self._chapter_workflow.compare_candidate(book_id, chapter_no, baseline_gate_id, candidate_gate_id)

    def approve_chapter(self, book_id: str, chapter_no: int) -> ChapterStatusRecord:
        return self._chapter_workflow.approve_chapter(book_id, chapter_no)

    def set_export_profile(self, book_id: str, platform: str, payload: dict) -> dict:
        return self._export_service.set_export_profile(book_id, platform, payload)

    def set_chapter_export_metadata(self, book_id: str, chapter_no: int, platform: str, payload: dict) -> dict:
        return self._export_service.set_chapter_export_metadata(book_id, chapter_no, platform, payload)

    def export_chapter(self, book_id: str, chapter_no: int, platform: str = "tomato") -> dict:
        return self._export_service.export_chapter(book_id, chapter_no, platform=platform)

    def verify_export_integrity(self, book_id: str, chapter_no: int, platform: str | None = None) -> dict:
        return self._export_service.verify_export_integrity(book_id, chapter_no, platform=platform)

    def micro_revise_exported_chapter(
        self,
        book_id: str,
        chapter_no: int,
        candidate_text: str,
        platform: str = "tomato",
    ) -> dict:
        return self._export_service.micro_revise_exported_chapter(book_id, chapter_no, candidate_text, platform=platform)

    # compat seam: transitional access for tests, batch, and intent callers.
    # This remains allowed in the current phase, but is not a recommended new extension point.
    def _load_artifact_payload(self, book_id: str, artifact_id: str) -> dict:
        record = self._artifact_record(book_id, artifact_id)
        book_root = self.repo.json.ensure_book_dirs(book_id)["book_root"]
        return self.repo.json.read_json(book_root / record.payload_ref.relative_path)

    # compat seam: transitional optional loader for existing schedulers and tests.
    def _optional_artifact_payload(self, book_id: str, artifact_id: str | None) -> dict | None:
        if not artifact_id:
            return None
        return self._load_artifact_payload(book_id, artifact_id)

    # compat seam: artifact record lookup stays local to the facade during the compatibility window.
    def _artifact_record(self, book_id: str, artifact_id: str):
        return self.repo.json.find_artifact(book_id, artifact_id)
