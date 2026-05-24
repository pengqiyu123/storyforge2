from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.schemas.artifact import AuditRecord
from engine.services.gate_runner import GateInputBundle, GateRunner
from engine.services.style_signal import StyleSignalAdapter
from engine.truth.truth_extractor_adapter import TruthExtractorAdapter


@dataclass(slots=True)
class WorkspaceBridgeService:
    gate_runner: GateRunner
    style_signal: StyleSignalAdapter
    truth_extractor: object

    def diagnose_workspace_chapter(
        self,
        *,
        book_dir: str | Path,
        chapter_path: str | Path,
        baseline_text: str | None = None,
    ) -> dict:
        book_dir = Path(book_dir)
        chapter_path = Path(chapter_path)
        text = self._load_chapter_text(chapter_path)
        mechanical, audit, gate = self.gate_runner.evaluate(
            GateInputBundle(
                book_id=book_dir.name,
                chapter_no=self._extract_chapter_no(chapter_path.name),
                revision_round=0,
                settlement_artifact_id="workspace-diagnosis",
                candidate_signature=self.gate_runner.build_signature(text),
                draft_text=text,
                plan_summary="workspace_read_only_diagnosis",
                compose_constraints=["workspace_read_only"],
                baseline_gate_summary={},
                revision_mode=None,
            )
        )
        style_signal = None
        if baseline_text:
            style_signal = self.style_signal.evaluate_pairwise(
                baseline_text=baseline_text,
                candidate_text=text,
                chapter_no=self._extract_chapter_no(chapter_path.name),
                reference_profile_ref="workspace-baseline",
            )
        truth_snapshot = self._build_minimal_truth_snapshot(book_dir)
        truth_payload = self.truth_extractor.extract(
            book_id=book_dir.name,
            chapter_no=self._extract_chapter_no(chapter_path.name),
            draft_text=text,
            truth_snapshot=truth_snapshot,
        )
        return {
            "book_dir": str(book_dir),
            "chapter_path": str(chapter_path),
            "chapter_no": self._extract_chapter_no(chapter_path.name),
            "mechanical_gate": mechanical.model_dump(mode="json"),
            "audit": audit.model_dump(mode="json") if isinstance(audit, AuditRecord) else audit,
            "gate_decision": gate.model_dump(mode="json"),
            "style_signal": style_signal.model_dump(mode="json") if style_signal else None,
            "truth_probe": truth_payload,
        }

    @staticmethod
    def _load_chapter_text(chapter_path: Path) -> str:
        text = chapter_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if lines and lines[0].startswith("第") and "章" in lines[0]:
            lines = lines[1:]
        lines = [line for line in lines if line.strip() != "---"]
        return "\n".join(lines).strip()

    @staticmethod
    def _extract_chapter_no(filename: str) -> int:
        prefix = filename.split("-", 1)[0]
        try:
            return int(prefix)
        except ValueError:
            return 1

    @staticmethod
    def _build_minimal_truth_snapshot(book_dir: Path) -> dict:
        story_dir = book_dir / "story"
        state_dir = story_dir / "state"
        return {
            "snapshot": {"snapshot_id": "workspace-read-only"},
            "canon": {"facts": []},
            "characters": {"characters": [], "relationships": []},
            "hook_ledger": {"hooks": []},
            "chapter_facts": {"chapters": []},
            "workspace_state_available": state_dir.exists(),
        }
