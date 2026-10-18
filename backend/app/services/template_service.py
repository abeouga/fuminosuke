from __future__ import annotations

from typing import Any, TypeVar

import cv2
import numpy as np
import yaml
from numpy.typing import NDArray
from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import JobArtifact, Template, TemplateVersion
from app.db.mutation import require_revision, revision_conflict
from app.db.session import Database
from app.domain.enums import JobStatus, TemplateMode, TemplateVersionStatus
from app.domain.template_config import (
    FieldMappingConfig,
    RegionConfig,
    TemplateConfig,
    build_blank_template_config,
    load_template_config,
    parse_template_config,
)
from app.image_processing.artifacts import ArtifactWriter
from app.image_processing.image_loader import ImageLoader
from app.layout_analysis.detector import LayoutDetector
from app.layout_analysis.models import LayoutDefinition, LayoutLabelPoolEntry
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.segmentation.candidate_regions import (
    handwriting_candidate_regions,
    handwriting_candidates_payload,
)
from app.services.template_mutation import require_draft
from app.storage.immutable_artifact import publish_config
from app.storage.local_storage import LocalStorageService
from app.template_recovery.pipelines import PreparedTemplate, TemplateRecoveryPipelines

BoxConfig = TypeVar("BoxConfig", RegionConfig, FieldMappingConfig)


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
            manual_boundary = job.config_snapshot_json.get("manualBoundaryNormalized")
            attempt = job.attempt_number
            if job.cancel_requested:
                return ""
        images = [
            self._image_loader.load(
                self._storage.read_bytes(item.relative_path), item.media_type
            ).rgb
            for item in inputs
        ]
        source_corners = self._manual_source_corners(manual_boundary, images[0])
        self._progress(job_id, "NORMALIZING_DOCUMENT", 15)
        if mode is TemplateMode.REFERENCE_TEMPLATE:
            prepared = self._pipelines.reference(images, source_corners=source_corners)
        else:
            self._progress(job_id, "RECOVERING_TEMPLATE", 35)
            prepared = self._pipelines.single_experimental(
                images,
                source_corners=source_corners,
            )
        if self._cancel_requested(job_id):
            return ""
        prepared = self._apply_edited_mask(job_id, mode, prepared)
        self._progress(job_id, "WRITING_ARTIFACTS", 80)
        try:
            version_id = self._persist(job_id, attempt, name, mode, prepared)
            if self._cancel_requested(job_id):
                self._discard_cancelled_version(job_id, version_id)
                return ""
            return version_id
        except Exception:
            self._remove_empty_template(name)
            raise

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
            expected_revision = job.config_snapshot_json.get("reprocessTemplateRevision") if job else None
            if reprocess_version_id:
                if previous is None or not isinstance(expected_revision, int):
                    raise revision_conflict()
                require_revision(previous, expected_revision)
                require_draft(previous)
            if previous is None:
                existing_version_id = session.scalar(
                    select(TemplateVersion.id)
                    .where(TemplateVersion.source_job_id == job_id)
                    .order_by(TemplateVersion.created_at.desc())
                    .limit(1)
                )
                if existing_version_id is not None:
                    return existing_version_id
                template = repository.create_template(
                    name=name, document_type="generic-document"
                )
                template_id = template.id
                version_number = 1
            else:
                template_id = previous.template_id
                completed_reprocess_id = session.scalar(
                    select(TemplateVersion.id)
                    .where(
                        TemplateVersion.source_job_id == job_id,
                        TemplateVersion.template_id == template_id,
                        TemplateVersion.version_number > previous.version_number,
                    )
                    .order_by(TemplateVersion.version_number.desc())
                    .limit(1)
                )
                if completed_reprocess_id is not None:
                    return completed_reprocess_id
                versions = repository.list_versions(template_id)
                version_number = max(item.version_number for item in versions) + 1
        normalized_entry = prepared.images.get("normalized")
        if (
            previous is not None
            and previous.layout_json
            and previous.canonical_width == prepared.canonical_width
            and previous.canonical_height == prepared.canonical_height
        ):
            initial_layout = LayoutDefinition.model_validate(previous.layout_json)
        elif normalized_entry is not None:
            self._progress(job_id, "DETECTING_LAYOUT", 65)
            initial_layout = LayoutDetector().detect(normalized_entry[1])
        else:
            initial_layout = LayoutDetector().detect(prepared.clean_template_rgb)
        if previous is None and job is not None:
            raw_catalog = job.config_snapshot_json.get("fieldCatalog", [])
            if isinstance(raw_catalog, list):
                field_catalog = [
                    LayoutLabelPoolEntry.model_validate(item)
                    for item in raw_catalog
                    if isinstance(item, dict)
                ]
                initial_layout = initial_layout.model_copy(
                    update={"label_pool": field_catalog},
                    deep=True,
                )
        prepared.quality = {
            **prepared.quality,
            "layoutPreDetected": True,
            "layoutCellCount": len(initial_layout.cells),
            "layoutLabelCount": sum(cell.role == "label" for cell in initial_layout.cells),
            "layoutInputCount": sum(cell.role == "input" for cell in initial_layout.cells),
            "layoutDetector": initial_layout.detector,
            "layoutOcrAvailable": initial_layout.ocr_available,
        }
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
        if previous is None:
            version_config = build_blank_template_config(
                template_id=template_id, template_version=version_number, name=name,
                canonical_width=prepared.canonical_width,
                canonical_height=prepared.canonical_height,
            )
        else:
            with self._database.session() as session:
                owner = session.get(Template, template_id)
                generic = owner is not None and owner.document_type == "generic-document"
            previous_config = (
                parse_template_config(
                    self._storage.read_bytes(previous.config_relative_path).decode("utf-8")
                ) if generic else None
            )
            version_config = self._config_for_dimensions(
                prepared.canonical_width, prepared.canonical_height, base=previous_config,
            ).model_copy(
                update={
                    "template_id": template_id, "template_version": version_number, "name": name,
                },
                deep=True,
            )
        config_text = yaml.safe_dump(
            version_config.model_dump(mode="json", by_alias=True),
            allow_unicode=True,
            sort_keys=False,
        )
        config_relative_path = publish_config(self._storage, config_text)
        with self._database.transaction() as session:
            job = JobRepository(session).get(job_id)
            if job is None or job.status != JobStatus.PROCESSING.value or job.attempt_number != attempt or job.cancel_requested:
                raise revision_conflict()
            previous = session.get(TemplateVersion, reprocess_version_id) if reprocess_version_id else None
            if reprocess_version_id:
                if previous is None:
                    raise revision_conflict()
                require_revision(previous, expected_revision)
                require_draft(previous)
            version = TemplateRepository(session).create_version(
                template_id=template_id,
                mode=mode,
                canonical_width=prepared.canonical_width,
                canonical_height=prepared.canonical_height,
                source_job_id=job_id,
                clean_template_artifact_id=clean_artifact.id,
                mask_artifact_id=mask_artifact_id,
                config_relative_path=config_relative_path,
                quality=prepared.quality,
                layout=initial_layout.model_dump(mode="json", by_alias=True),
            )
            if version.version_number != version_number:
                raise revision_conflict()
            if previous is None and self._settings.app.template_analysis_enabled:
                from app.editing.authoring_provenance import seed_detection

                seed_detection(session, version)
            if previous is not None:
                previous.status = TemplateVersionStatus.ARCHIVED.value
            version_id = version.id
            job.config_snapshot_json = {
                **job.config_snapshot_json, "templateId": template_id, "templateVersionId": version_id,
            }
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

    def _config_for_dimensions(
        self, width: int, height: int, *, base: TemplateConfig | None = None,
    ) -> TemplateConfig:
        config = base or self._config
        if (
            width == config.canonical_width
            and height == config.canonical_height
        ):
            return config.model_copy(deep=True)
        scale_x = width / config.canonical_width
        scale_y = height / config.canonical_height

        def scaled_box(item: BoxConfig) -> BoxConfig:
            x = min(width - 1, max(0, round(item.x * scale_x)))
            y = min(height - 1, max(0, round(item.y * scale_y)))
            box_width = min(width - x, max(1, round(item.width * scale_x)))
            box_height = min(height - y, max(1, round(item.height * scale_y)))
            return item.model_copy(
                update={"x": x, "y": y, "width": box_width, "height": box_height},
                deep=True,
            )

        return config.model_copy(
            update={
                "canonical_width": width,
                "canonical_height": height,
                "regions": [scaled_box(item) for item in config.regions],
                "field_mappings": [
                    scaled_box(item) for item in config.field_mappings
                ],
            },
            deep=True,
        )

    @staticmethod
    def _manual_source_corners(
        value: object,
        image_rgb: NDArray[np.uint8],
    ) -> NDArray[np.float32] | None:
        if value is None:
            return None
        points = np.asarray(value, dtype=np.float32)
        if points.shape != (4, 2) or not np.isfinite(points).all():
            raise AppError(
                status=422,
                code="DOCUMENT_BOUNDARY_INVALID",
                title="Document boundary is invalid",
                detail="保存された四隅の指定が正しくありません。もう一度指定してください。",
            )
        height, width = image_rgb.shape[:2]
        return points * np.asarray([width - 1, height - 1], dtype=np.float32)

    def _remove_empty_template(self, name: str) -> None:
        normalized_name = TemplateRepository.normalize_name(name)
        removed_ids: list[str] = []
        with self._database.session() as session:
            templates = [
                template
                for template in session.scalars(select(Template))
                if TemplateRepository.normalize_name(template.name) == normalized_name
            ]
            for template in templates:
                version_id = session.scalar(
                    select(TemplateVersion.id)
                    .where(TemplateVersion.template_id == template.id)
                    .limit(1)
                )
                if version_id is not None:
                    continue
                removed_ids.append(template.id)
                session.delete(template)
            if removed_ids:
                session.commit()
        for template_id in removed_ids:
            relative_path = f"templates/{template_id}"
            if self._storage.exists(relative_path):
                self._storage.delete_tree(relative_path)

    def _discard_cancelled_version(self, job_id: str, version_id: str) -> None:
        template_id: str | None = None
        version_number: int | None = None
        delete_template = False
        with self._database.session() as session:
            version = session.get(TemplateVersion, version_id)
            if version is None or version.source_job_id != job_id:
                return
            template_id = version.template_id
            version_number = version.version_number
            job = JobRepository(session).get(job_id)
            previous_id = (
                str(job.config_snapshot_json.get("reprocessTemplateVersionId"))
                if job and job.config_snapshot_json.get("reprocessTemplateVersionId")
                else None
            )
            previous = session.get(TemplateVersion, previous_id) if previous_id else None
            session.delete(version)
            session.flush()
            remaining = session.scalar(
                select(TemplateVersion.id)
                .where(TemplateVersion.template_id == template_id)
                .limit(1)
            )
            if remaining is None:
                template = session.get(Template, template_id)
                if template is not None:
                    session.delete(template)
                    delete_template = True
            elif (
                previous is not None
                and previous.status == TemplateVersionStatus.ARCHIVED.value
            ):
                previous.status = TemplateVersionStatus.DRAFT.value
            if job is not None:
                snapshot = dict(job.config_snapshot_json)
                snapshot.pop("templateId", None)
                snapshot.pop("templateVersionId", None)
                job.config_snapshot_json = snapshot
            session.commit()
        if template_id is None or version_number is None:
            return
        relative_path = (
            f"templates/{template_id}"
            if delete_template
            else f"templates/{template_id}/versions/{version_number}"
        )
        if self._storage.exists(relative_path):
            self._storage.delete_tree(relative_path)

    def _discard_job_version(self, job_id: str) -> None:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            reprocess_version_id = (
                str(job.config_snapshot_json.get("reprocessTemplateVersionId"))
                if job and job.config_snapshot_json.get("reprocessTemplateVersionId")
                else None
            )
            statement = select(TemplateVersion.id).where(
                TemplateVersion.source_job_id == job_id,
                TemplateVersion.status == TemplateVersionStatus.DRAFT.value,
            )
            if reprocess_version_id:
                previous = session.get(TemplateVersion, reprocess_version_id)
                if previous is not None:
                    statement = statement.where(
                        TemplateVersion.template_id == previous.template_id,
                        TemplateVersion.version_number > previous.version_number,
                    )
                else:
                    statement = statement.where(
                        TemplateVersion.id != reprocess_version_id
                    )
            version_id = session.scalar(
                statement
                .order_by(TemplateVersion.created_at.desc())
                .limit(1)
            )
        if version_id is not None:
            self._discard_cancelled_version(job_id, version_id)

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

    def _cancel_requested(self, job_id: str) -> bool:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            return job is None or job.cancel_requested

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
