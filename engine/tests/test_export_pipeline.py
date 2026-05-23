from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from engine.schemas.artifact import AuditRecord
from engine.services import StoryEngineService


class PassingAuditor:
    def review(self, bundle) -> AuditRecord:
        return AuditRecord(
            passed=True,
            critical_count=0,
            issues=[],
            recommended_mode="accept",
            score_summary={"overall": 6.8, "logic": 6.6, "character": 6.8, "hook": 6.9, "pace": 6.7},
        )


class ExportPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.book_id = "export-book"
        self.service = StoryEngineService(self.root)
        self.service.create_book({"book_id": self.book_id, "title": "Export Book"})
        self.service.init_chapter(self.book_id, 1)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _approved_chapter(self) -> None:
        self.service.plan_chapter(self.book_id, 1, guidance="keep the hook sharp")
        self.service.compose_chapter(self.book_id, 1)
        draft_id = self.service.write_chapter_draft(self.book_id, 1, mode="initial")
        settled = self.service.settle_chapter(self.book_id, 1, draft_id)
        original = self.service.gate_runner.auditor
        self.service.gate_runner.auditor = PassingAuditor()
        try:
            audit = self.service.audit_chapter(self.book_id, 1, settled.current_artifact_refs["settlement"])
        finally:
            self.service.gate_runner.auditor = original
        self.assertTrue(audit["gate_decision"]["passed"])
        approved = self.service.approve_chapter(self.book_id, 1)
        self.assertEqual(approved.stage.value, "approved")

    def test_approved_exports_and_creates_manifest_package(self) -> None:
        self._approved_chapter()
        exported = self.service.export_chapter(self.book_id, 1)
        self.assertEqual(exported["status"].stage.value, "exported")
        status = self.service.get_chapter_status(self.book_id, 1)["status"]
        self.assertIn("export_manifest", status.current_artifact_refs)
        self.assertIn("export_package", status.current_artifact_refs)
        self.assertIn("export_metadata", status.current_artifact_refs)
        export_dir = self.root / "books" / self.book_id / "exports" / "tomato" / "chapter-0001" / "v001"
        self.assertTrue((export_dir / "chapter.md").exists())
        self.assertTrue((export_dir / "metadata.json").exists())
        self.assertTrue((export_dir / "manifest.json").exists())

    def test_metadata_only_reexport_preserves_text_hash(self) -> None:
        self._approved_chapter()
        first = self.service.export_chapter(self.book_id, 1)
        first_manifest = self.service._load_artifact_payload(self.book_id, first["export_manifest_artifact_id"])
        self.service.set_chapter_export_metadata(self.book_id, 1, "tomato", {"summary": "stronger pitch"})
        second = self.service.export_chapter(self.book_id, 1)
        second_manifest = self.service._load_artifact_payload(self.book_id, second["export_manifest_artifact_id"])
        self.assertEqual(second["version"], 2)
        self.assertEqual(first_manifest["chapter_file_sha256"], second_manifest["chapter_file_sha256"])
        self.assertEqual(first_manifest["chapter_semantic_sha256"], second_manifest["chapter_semantic_sha256"])

    def test_tamper_detection_blocks_export_and_verify(self) -> None:
        self._approved_chapter()
        self.service.export_chapter(self.book_id, 1)
        chapter_path = self.root / "books" / self.book_id / "chapters" / "0001.md"
        chapter_path.write_text("tampered chapter file with different semantics", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service.verify_export_integrity(self.book_id, 1)
        with self.assertRaises(ValueError):
            self.service.export_chapter(self.book_id, 1)

    def test_semantic_changing_micro_revision_is_rejected(self) -> None:
        self._approved_chapter()
        self.service.export_chapter(self.book_id, 1)
        with self.assertRaises(ValueError):
            self.service.micro_revise_exported_chapter(
                self.book_id,
                1,
                "This is a semantically different chapter body that changes what happened.",
            )

    def test_micro_revise_creates_surface_text_manifest_without_rewriting_approval_receipt(self) -> None:
        self._approved_chapter()
        first = self.service.export_chapter(self.book_id, 1)
        status = self.service.get_chapter_status(self.book_id, 1)["status"]
        approval_id = status.current_artifact_refs["approval_receipt"]
        approval_before = self.service._load_artifact_payload(self.book_id, approval_id)
        chapter_path = self.root / "books" / self.book_id / "chapters" / "0001.md"
        current_text = chapter_path.read_text(encoding="utf-8")
        candidate_text = current_text.replace("。", " 。 ").replace("，", "， ")

        revised = self.service.micro_revise_exported_chapter(
            self.book_id,
            1,
            candidate_text,
        )

        approval_after = self.service._load_artifact_payload(self.book_id, approval_id)
        manifest = self.service._load_artifact_payload(self.book_id, revised["export_manifest_artifact_id"])
        self.assertEqual(approval_before, approval_after)
        self.assertEqual(manifest["revision_kind"], "surface_text")
        self.assertEqual(manifest["previous_export_manifest_id"], first["export_manifest_artifact_id"])
        self.assertIn("export_diff_artifact_id", revised)


if __name__ == "__main__":
    unittest.main()
