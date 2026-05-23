from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable

from engine.schemas.artifact import (
    ApprovalReceiptRecord,
    BookExportProfileRecord,
    ExportDiffRecord,
    ExportManifestRecord,
    ExportPackageRecord,
    PlatformExportMetadataRecord,
)
from engine.schemas.chapter import ChapterStage
from engine.schemas.run import RunAction, RunStatus
from engine.state_machine import next_status


@dataclass(slots=True)
class ExportService:
    repo: object
    register_artifact: Callable[[str, int, str, dict, str], str]
    start_run: Callable[[str, int, str, str, list[str] | None], object]
    finish_run: Callable[[str, int, str, str, list[str], str | None], object]
    load_artifact_payload: Callable[[str, str], dict]
    assert_truth_fresh_for_action: Callable[[str, int, str], None]
    utc_now: Callable[[], object]

    def set_export_profile(self, book_id: str, platform: str, payload: dict) -> dict:
        profile = self.repo.json.load_export_profile(book_id)
        defaults = dict(profile.defaults)
        defaults[platform] = PlatformExportMetadataRecord(platform=platform, **payload)
        updated = profile.model_copy(update={"defaults": defaults})
        self.repo.json.save_export_profile(book_id, updated)
        return updated.model_dump(mode="json")

    def set_chapter_export_metadata(self, book_id: str, chapter_no: int, platform: str, payload: dict) -> dict:
        profile = self.repo.json.load_export_profile(book_id)
        overrides = dict(profile.chapter_overrides)
        chapter_key = f"{chapter_no:04d}"
        chapter_overrides = dict(overrides.get(chapter_key, {}))
        chapter_overrides[platform] = PlatformExportMetadataRecord(platform=platform, **payload)
        overrides[chapter_key] = chapter_overrides
        updated = profile.model_copy(update={"chapter_overrides": overrides})
        self.repo.json.save_export_profile(book_id, updated)
        return updated.model_dump(mode="json")

    def export_chapter(self, book_id: str, chapter_no: int, platform: str = "tomato") -> dict:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        self.assert_truth_fresh_for_action(book_id, chapter_no, "export")
        if status.stage not in {ChapterStage.APPROVED, ChapterStage.EXPORTED}:
            raise ValueError("export_chapter requires approved or exported stage")
        approval_id = status.current_artifact_refs.get("approval_receipt")
        if not approval_id:
            raise ValueError("export_chapter requires approval_receipt artifact")
        approval = ApprovalReceiptRecord.model_validate(self.load_artifact_payload(book_id, approval_id))
        profile = self.repo.json.load_export_profile(book_id)
        metadata = self._build_export_metadata(profile, chapter_no, platform)
        export_index = self.repo.json.load_export_index(book_id)
        export_key = f"{chapter_no:04d}:{platform}"
        previous = export_index.get("entries", {}).get(export_key)
        if previous is None:
            integrity = self._assert_export_integrity(book_id, chapter_no, approval)
        else:
            manifest_payload = self.load_artifact_payload(book_id, previous["latest_manifest_id"])
            previous_manifest = ExportManifestRecord.model_validate(manifest_payload)
            allow_surface_revision = status.current_artifact_refs.get("export_diff") is not None
            integrity = self._assert_export_manifest_integrity(
                book_id,
                chapter_no,
                previous_manifest,
                approval,
                allow_surface_revision=allow_surface_revision,
            )
        version = 1 if previous is None else int(previous["version"]) + 1
        revision_kind = "initial" if previous is None else self._classify_export_revision(
            previous_file_sha=previous["chapter_file_sha256"],
            previous_semantic_sha=previous["chapter_semantic_sha256"],
            current_file_sha=integrity["chapter_file_sha256"],
            current_semantic_sha=integrity["chapter_semantic_sha256"],
        )
        run = self.start_run(book_id, chapter_no, RunAction.EXPORT.value, "exporter", [approval_id])
        metadata_id = self.register_artifact(
            book_id,
            chapter_no,
            "export_metadata",
            metadata.model_dump(mode="json"),
            run.run_id,
        )
        export_dir = self.repo.json.build_export_dir(book_id, platform, chapter_no, version)
        chapter_path = self.repo.json.ensure_book_dirs(book_id)["chapters"] / f"{chapter_no:04d}.md"
        chapter_text = chapter_path.read_text(encoding="utf-8")
        export_chapter_path = export_dir / "chapter.md"
        export_metadata_path = export_dir / "metadata.json"
        export_manifest_path = export_dir / "manifest.json"
        export_chapter_path.write_text(chapter_text, encoding="utf-8")
        export_metadata_path.write_text(json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        package_hash = self._hash_text(
            chapter_text + json.dumps(metadata.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        )
        export_id = f"export-{chapter_no:04d}-{platform}-{version:03d}"
        package = ExportPackageRecord(
            export_id=export_id,
            platform=platform,
            chapter_no=chapter_no,
            version=version,
            package_root=str(export_dir.relative_to(self.repo.json.ensure_book_dirs(book_id)["book_root"])).replace("\\", "/"),
            chapter_file=str(export_chapter_path.name),
            metadata_file=str(export_metadata_path.name),
            manifest_file=str(export_manifest_path.name),
            package_hash=package_hash,
        )
        package_id = self.register_artifact(
            book_id,
            chapter_no,
            "export_package",
            package.model_dump(mode="json"),
            run.run_id,
        )
        manifest = ExportManifestRecord(
            export_id=export_id,
            book_id=book_id,
            chapter_no=chapter_no,
            platform=platform,
            version=version,
            source_stage=status.stage.value,
            approval_receipt_artifact_id=approval_id,
            truth_commit_receipt_artifact_id=approval.truth_commit_receipt_artifact_id,
            draft_artifact_id=approval.draft_artifact_id,
            settlement_artifact_id=approval.settlement_artifact_id,
            chapter_file_path=str(chapter_path.name),
            chapter_file_sha256=integrity["chapter_file_sha256"],
            chapter_semantic_sha256=integrity["chapter_semantic_sha256"],
            metadata_artifact_id=metadata_id,
            package_artifact_id=package_id,
            previous_export_manifest_id=previous["latest_manifest_id"] if previous else None,
            revision_kind=revision_kind,
            integrity_status="ok",
        )
        manifest_id = self.register_artifact(
            book_id,
            chapter_no,
            "export_manifest",
            manifest.model_dump(mode="json"),
            run.run_id,
        )
        export_manifest_path.write_text(json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if status.stage == ChapterStage.APPROVED:
            next_record = next_status(
                status,
                ChapterStage.EXPORTED,
                run_id=run.run_id,
                artifact_refs={
                    "export_metadata": metadata_id,
                    "export_package": package_id,
                    "export_manifest": manifest_id,
                },
            )
        else:
            next_record = status.model_copy(
                update={
                    "current_artifact_refs": {
                        **status.current_artifact_refs,
                        "export_metadata": metadata_id,
                        "export_package": package_id,
                        "export_manifest": manifest_id,
                    },
                    "last_run_id": run.run_id,
                    "updated_at": self.utc_now(),
                }
            )
        self.repo.save_chapter_status(next_record)
        self.repo.json.update_export_index(
            book_id,
            chapter_no,
            platform,
            {
                "latest_manifest_id": manifest_id,
                "version": version,
                "chapter_file_sha256": manifest.chapter_file_sha256,
                "chapter_semantic_sha256": manifest.chapter_semantic_sha256,
                "integrity_status": manifest.integrity_status,
            },
        )
        self.repo._read_model(book_id).upsert_chapter_export(
            book_id=book_id,
            chapter_no=chapter_no,
            platform=platform,
            latest_manifest_id=manifest_id,
            version=version,
            chapter_file_sha256=manifest.chapter_file_sha256,
            chapter_semantic_sha256=manifest.chapter_semantic_sha256,
            integrity_status=manifest.integrity_status,
            exported_at=manifest.exported_at.isoformat(),
        )
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [metadata_id, package_id, manifest_id], None)
        return {
            "status": next_record,
            "export_manifest_artifact_id": manifest_id,
            "export_package_artifact_id": package_id,
            "export_metadata_artifact_id": metadata_id,
            "version": version,
        }

    def verify_export_integrity(self, book_id: str, chapter_no: int, platform: str | None = None) -> dict:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        if status.stage == ChapterStage.INVALIDATED:
            raise ValueError("verify_export_integrity requires non-invalidated chapter")
        self.assert_truth_fresh_for_action(book_id, chapter_no, "verify export")
        manifest_id = status.current_artifact_refs.get("export_manifest")
        if not manifest_id:
            raise ValueError("verify_export_integrity requires export_manifest artifact")
        manifest = ExportManifestRecord.model_validate(self.load_artifact_payload(book_id, manifest_id))
        if platform is not None and manifest.platform != platform:
            raise ValueError("verify_export_integrity platform mismatch")
        approval = ApprovalReceiptRecord.model_validate(self.load_artifact_payload(book_id, manifest.approval_receipt_artifact_id))
        integrity = self._assert_export_manifest_integrity(book_id, chapter_no, manifest, approval)
        return {
            "ok": True,
            "platform": manifest.platform,
            "manifest_id": manifest_id,
            "chapter_file_sha256": integrity["chapter_file_sha256"],
            "chapter_semantic_sha256": integrity["chapter_semantic_sha256"],
        }

    def micro_revise_exported_chapter(
        self,
        book_id: str,
        chapter_no: int,
        candidate_text: str,
        platform: str = "tomato",
    ) -> dict:
        status = self.repo.load_chapter_status(book_id, chapter_no)
        self.assert_truth_fresh_for_action(book_id, chapter_no, "micro revise export")
        if status.stage != ChapterStage.EXPORTED:
            raise ValueError("micro_revise_exported_chapter requires exported stage")
        manifest_id = status.current_artifact_refs.get("export_manifest")
        approval_id = status.current_artifact_refs.get("approval_receipt")
        if not manifest_id or not approval_id:
            raise ValueError("micro_revise_exported_chapter requires export_manifest and approval_receipt")
        chapter_path = self.repo.json.ensure_book_dirs(book_id)["chapters"] / f"{chapter_no:04d}.md"
        current_text = chapter_path.read_text(encoding="utf-8")
        current_sha = self._hash_text(current_text)
        current_semantic = self._semantic_hash(current_text)
        candidate_sha = self._hash_text(candidate_text)
        candidate_semantic = self._semantic_hash(candidate_text)
        diff_excerpt = list(difflib.unified_diff(current_text.splitlines(), candidate_text.splitlines(), lineterm=""))[:20]
        classification = "surface_text" if current_semantic == candidate_semantic else "semantic_change"
        if classification != "surface_text":
            raise ValueError("micro revision changed semantic hash and is not allowed")
        run = self.start_run(book_id, chapter_no, RunAction.EXPORT.value, "exporter", [manifest_id])
        diff = ExportDiffRecord(
            before_sha256=current_sha,
            after_sha256=candidate_sha,
            before_semantic_sha256=current_semantic,
            after_semantic_sha256=candidate_semantic,
            changed_line_count=len(diff_excerpt),
            diff_excerpt=diff_excerpt,
            classification=classification,
        )
        diff_id = self.register_artifact(
            book_id,
            chapter_no,
            "export_diff",
            diff.model_dump(mode="json"),
            run.run_id,
        )
        chapter_path.write_text(candidate_text, encoding="utf-8")
        refreshed_status = self.repo.load_chapter_status(book_id, chapter_no)
        updated_status = refreshed_status.model_copy(
            update={
                "current_artifact_refs": {
                    **refreshed_status.current_artifact_refs,
                    "export_diff": diff_id,
                },
                "last_run_id": run.run_id,
                "updated_at": self.utc_now(),
            }
        )
        self.repo.save_chapter_status(updated_status)
        self.finish_run(book_id, chapter_no, run.run_id, RunStatus.SUCCEEDED.value, [diff_id], None)
        export_result = self.export_chapter(book_id, chapter_no, platform=platform)
        export_result["export_diff_artifact_id"] = diff_id
        return export_result

    def _build_export_metadata(
        self,
        profile: BookExportProfileRecord,
        chapter_no: int,
        platform: str,
    ) -> PlatformExportMetadataRecord:
        default = profile.defaults.get(platform, PlatformExportMetadataRecord(platform=platform))
        chapter_overrides = profile.chapter_overrides.get(f"{chapter_no:04d}", {})
        override = chapter_overrides.get(platform)
        if not override:
            return default
        payload = default.model_dump(mode="json")
        payload.update({key: value for key, value in override.model_dump(mode="json").items() if value not in (None, [], {})})
        return PlatformExportMetadataRecord.model_validate(payload)

    def _assert_export_integrity(self, book_id: str, chapter_no: int, approval: ApprovalReceiptRecord) -> dict:
        chapter_path = self.repo.json.ensure_book_dirs(book_id)["chapters"] / f"{chapter_no:04d}.md"
        current_text = chapter_path.read_text(encoding="utf-8")
        current_sha = self._hash_text(current_text)
        current_semantic = self._semantic_hash(current_text)
        if current_sha != approval.chapter_file_sha256 or current_semantic != approval.chapter_semantic_sha256:
            raise ValueError("approved chapter file has been tampered with")
        return {
            "chapter_file_sha256": current_sha,
            "chapter_semantic_sha256": current_semantic,
        }

    def _assert_export_manifest_integrity(
        self,
        book_id: str,
        chapter_no: int,
        manifest: ExportManifestRecord,
        approval: ApprovalReceiptRecord,
        allow_surface_revision: bool = False,
    ) -> dict:
        chapter_path = self.repo.json.ensure_book_dirs(book_id)["chapters"] / f"{chapter_no:04d}.md"
        current_text = chapter_path.read_text(encoding="utf-8")
        current_sha = self._hash_text(current_text)
        current_semantic = self._semantic_hash(current_text)
        if current_semantic != approval.chapter_semantic_sha256:
            raise ValueError("approved chapter semantic content has diverged from approval baseline")
        if current_semantic != manifest.chapter_semantic_sha256:
            raise ValueError("latest exported chapter file has been tampered with")
        if not allow_surface_revision and current_sha != manifest.chapter_file_sha256:
            raise ValueError("latest exported chapter file has been tampered with")
        return {
            "chapter_file_sha256": current_sha,
            "chapter_semantic_sha256": current_semantic,
        }

    @staticmethod
    def _classify_export_revision(
        *,
        previous_file_sha: str,
        previous_semantic_sha: str,
        current_file_sha: str,
        current_semantic_sha: str,
    ) -> str:
        if current_semantic_sha != previous_semantic_sha:
            raise ValueError("export revision changed semantic hash and must return to the main review chain")
        if current_file_sha != previous_file_sha:
            return "surface_text"
        return "metadata_only"

    @staticmethod
    def _hash_text(text: str) -> str:
        return sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _semantic_hash(text: str) -> str:
        normalized = "".join(ch for ch in text if not ch.isspace() and ch not in "，。！？,.!?;；:\"'`()[]{}<>《》、")
        return sha256(normalized.encode("utf-8")).hexdigest()
