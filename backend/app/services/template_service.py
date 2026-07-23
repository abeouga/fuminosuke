from __future__ import annotations

import json
from typing import Any

import cv2
import numpy as np

from app.core.config import Settings
from app.db.models import JobArtifact, TemplateVersion
from app.db.session import Database
from app.domain.enums import TemplateMode
from app.domain.template_config import default_template_config_path, load_template_config
from app.image_processing.artifacts import ArtifactWriter, encode_png
from app.image_processing.image_loader import ImageLoader
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.segmentation.candidate_regions import (
    handwriting_candidate_regions,
    handwriting_candidates_payload,
)
from app.storage.local_storage import LocalStorageService
from app.template_recovery.pipelines import PreparedTemplate, TemplateRecoveryPipelines


class TemplatePreparationService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        storage: LocalStorageService,
    ) -> None:
        self._settings = settings
        self._database = database
        self._storage = storage
        self._config = load_template_config()
        self._pipelines = TemplateRecoveryPipelines(settings, self._config)
        self._artifact_writer = ArtifactWriter(database, storage)
        self._image_loader = ImageLoader(settings.storage, settings.image_processing)

    def process(self, job_id: str) -> str:
        with self._database.session() as session:
            job_repository = JobRepository(session)
            job = job_repository.get(job_id)
            if job is None:
                raise ValueError("Job not found")
            inputs = job_repository.list_inputs(job_id)
            mode = TemplateMode(job.mode or "")
            name = str(job.config_snapshot_json.get("name", self._config.name))
            attempt = job.attempt_number
        images = [
            self._image_loader.load(
                self._storage.read_bytes(item.relative_path), item.media_type
            ).rgb
            for item in inputs
        ]
        self._progress(job_id, "NORMALIZING_DOCUMENT", 15)
        if mode is TemplateMode.REFERENCE_TEMPLATE:
            prepared = self._pipelines.reference(images)
        elif mode is TemplateMode.MULTI_SCAN_CONSENSUS:
            self._progress(job_id, "REGISTERING_TEMPLATE", 35)
            prepared = self._pipelines.consensus(images)
        else:
            self._progress(job_id, "RECOVERING_TEMPLATE", 35)
            prepared = self._pipelines.single_experimental(images)
        prepared = self._apply_edited_mask(job_id, mode, prepared)
        self._progress(job_id, "WRITING_ARTIFACTS", 80)
        return self._persist(job_id, attempt, name, mode, prepared)

    def _persist(
        self,
        job_id: str,
        attempt: int,
        name: str,
        mode: TemplateMode,
        prepared: PreparedTemplate,
    ) -> str:
        reprocess_version_id: str | None = None
        with self._database.session() as session:
            repository = TemplateRepository(session)
            job = JobRepository(session).get(job_id)
            if job is not None:
                value = job.config_snapshot_json.get("reprocessTemplateVersionId")
                reprocess_version_id = str(value) if value else None
            previous = (
                repository.get_version(reprocess_version_id) if reprocess_version_id else None
            )
            if previous is None:
                template = repository.create_template(
                    name=name, document_type="cow-breeding-ledger"
                )
                template_id = template.id
                version_number = 1
            else:
                template_id = previous.template_id
                versions = repository.list_versions(template_id)
                version_number = max(item.version_number for item in versions) + 1
        artifacts: dict[str, str] = {}
        for artifact_name, (file_name, image, rgb) in prepared.images.items():
            artifact = self._artifact_writer.image(
                job_id=job_id,
                attempt_number=attempt,
                name=artifact_name,
                file_name=file_name,
                image=image,
                rgb=rgb,
            )
            artifacts[artifact_name] = artifact.artifact_name
        clean_artifact = self._artifact_writer.image(
            job_id=job_id,
            attempt_number=attempt,
            name="cleanTemplate",
            file_name="clean_template.png",
            image=prepared.clean_template_rgb,
        )
        preview_artifact = self._artifact_writer.image(
            job_id=job_id,
            attempt_number=attempt,
            name="preview",
            file_name="preview.png",
            image=prepared.preview_rgb,
        )
        artifacts["cleanTemplate"] = clean_artifact.artifact_name
        artifacts["preview"] = preview_artifact.artifact_name
        mask_artifact_id: str | None = None
        if prepared.handwriting_mask is not None:
            mask_artifact = self._artifact_writer.image(
                job_id=job_id,
                attempt_number=attempt,
                name="handwritingMask",
                file_name="handwriting_mask.png",
                image=prepared.handwriting_mask,
                rgb=False,
            )
            mask_artifact_id = mask_artifact.id
            artifacts["handwritingMask"] = mask_artifact.artifact_name
            clean_preview_artifact = self._artifact_writer.image(
                job_id=job_id,
                attempt_number=attempt,
                name="cleanPreview",
                file_name="clean_preview.png",
                image=prepared.preview_rgb,
            )
            artifacts["cleanPreview"] = clean_preview_artifact.artifact_name
            candidates_artifact = self._artifact_writer.json(
                job_id=job_id,
                attempt_number=attempt,
                name="handwritingCandidates",
                file_name="handwriting_candidates.json",
                value=handwriting_candidates_payload(
                    prepared.handwriting_mask, prepared.handwriting_candidates
                ),
            )
            artifacts["handwritingCandidates"] = candidates_artifact.artifact_name
        self._artifact_writer.json(
            job_id=job_id,
            attempt_number=attempt,
            name="quality",
            file_name="quality.json",
            value=prepared.quality,
        )
        base_path = f"templates/{template_id}/versions/{version_number}"
        self._storage.write_bytes(
            f"{base_path}/clean_template.png", encode_png(prepared.clean_template_rgb)
        )
        self._storage.write_bytes(f"{base_path}/preview.png", encode_png(prepared.preview_rgb))
        if prepared.handwriting_mask is not None:
            self._storage.write_bytes(
                f"{base_path}/handwriting_mask.png",
                encode_png(prepared.handwriting_mask, rgb=False),
            )
        config_text = default_template_config_path().read_text(encoding="utf-8")
        config_relative_path = self._storage.write_text(f"{base_path}/template.yaml", config_text)
        self._storage.write_text(
            f"{base_path}/quality.json",
            json.dumps(prepared.quality, ensure_ascii=False, indent=2),
        )
        with self._database.session() as session:
            version = TemplateRepository(session).create_version(
                template_id=template_id,
                mode=mode,
                canonical_width=self._config.canonical_width,
                canonical_height=self._config.canonical_height,
                source_job_id=job_id,
                clean_template_artifact_id=clean_artifact.id,
                mask_artifact_id=mask_artifact_id,
                config_relative_path=config_relative_path,
                quality=prepared.quality,
            )
            if version.version_number != version_number:
                raise RuntimeError("Template version number changed during persistence")
            if reprocess_version_id:
                previous = TemplateRepository(session).get_version(reprocess_version_id)
                if previous is not None and previous.status == "DRAFT":
                    previous.status = "ARCHIVED"
                    session.commit()
            version_id = version.id
            job_repository = JobRepository(session)
            job = job_repository.get(job_id)
            if job is not None:
                snapshot = dict(job.config_snapshot_json)
                snapshot["templateId"] = template_id
                snapshot["templateVersionId"] = version_id
                job_repository.update(job_id, config_snapshot_json=snapshot)
        result = self._result_json(
            job_id=job_id,
            attempt=attempt,
            mode=mode,
            template_id=template_id,
            version_id=version_id,
            version_number=version_number,
            artifacts=artifacts,
            quality=prepared.quality,
            unknown=prepared.unknown_overlap_regions,
        )
        self._artifact_writer.json(
            job_id=job_id,
            attempt_number=attempt,
            name="result",
            file_name="result.json",
            value=result,
        )
        return version_id

    def _apply_edited_mask(
        self,
        job_id: str,
        mode: TemplateMode,
        prepared: PreparedTemplate,
    ) -> PreparedTemplate:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            version_id = (
                str(job.config_snapshot_json.get("reprocessTemplateVersionId"))
                if job and job.config_snapshot_json.get("reprocessTemplateVersionId")
                else None
            )
            version = session.get(TemplateVersion, version_id) if version_id else None
            artifact = (
                session.get(JobArtifact, version.mask_artifact_id)
                if version and version.mask_artifact_id
                else None
            )
            relative_path = artifact.relative_path if artifact else None
        if relative_path is None:
            return prepared
        mask = cv2.imdecode(
            np.frombuffer(self._storage.read_bytes(relative_path), dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE,
        )
        normalized_entry = prepared.images.get("normalized")
        if mask is None or normalized_entry is None:
            return prepared
        base = normalized_entry[1]
        if mask.shape != base.shape[:2]:
            raise ValueError("Edited mask dimensions changed after validation")
        normalized_mask = (mask >= 128).astype(np.uint8) * 255
        handwriting_only = np.full_like(base, 255)
        handwriting_only[normalized_mask > 0] = base[normalized_mask > 0]
        debug = base.copy()
        overlay = np.full_like(base, (235, 55, 55))
        selected = normalized_mask > 0
        debug[selected] = cv2.addWeighted(base, 0.35, overlay, 0.65, 0)[selected]
        preview = cv2.inpaint(base, normalized_mask, 3, cv2.INPAINT_TELEA).astype(np.uint8)
        prepared.handwriting_mask = normalized_mask
        prepared.handwriting_candidates = handwriting_candidate_regions(normalized_mask)
        prepared.preview_rgb = preview
        if mode is TemplateMode.SINGLE_IMAGE_EXPERIMENTAL:
            prepared.clean_template_rgb = preview
        prepared.images["handwritingOnly"] = (
            "handwriting_only.png",
            handwriting_only,
            True,
        )
        prepared.images["debugOverlay"] = ("debug_overlay.png", debug, True)
        warnings = prepared.quality.get("warnings", [])
        prepared.quality = {
            **prepared.quality,
            "maskSource": "USER_EDITED",
            "editedMaskCoverageRatio": float((normalized_mask > 0).mean()),
            "warnings": [
                *(warnings if isinstance(warnings, list) else []),
                "EDITED_MASK_APPLIED_TO_DEPENDENT_ARTIFACTS",
            ],
        }
        return prepared

    def _progress(self, job_id: str, stage: str, percent: int) -> None:
        with self._database.session() as session:
            repository = JobRepository(session)
            repository.update(job_id, progress_stage=stage, progress_percent=percent)
            repository.add_event(
                job_id=job_id,
                level="INFO",
                event_code="JOB_STAGE_COMPLETED",
                stage=stage,
                metadata={"progressPercent": percent},
            )

    def _result_json(
        self,
        *,
        job_id: str,
        attempt: int,
        mode: TemplateMode,
        template_id: str,
        version_id: str,
        version_number: int,
        artifacts: dict[str, str],
        quality: dict[str, Any],
        unknown: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "jobId": job_id,
            "jobType": "PREPARE_TEMPLATE",
            "mode": mode.value,
            "status": "REVIEW_REQUIRED",
            "attemptNumber": attempt,
            "template": {
                "templateId": template_id,
                "templateVersionId": version_id,
                "templateVersionNumber": version_number,
            },
            "artifacts": artifacts,
            "quality": quality,
            "unknownOverlapRegions": unknown,
            "ocr": {"provider": "disabled", "apiCallCount": 0},
            "error": None,
        }
