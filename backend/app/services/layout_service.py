from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.db.session import Database
from app.domain.enums import TemplateMode
from app.domain.field_targets import (
    FIELD_TARGET_BY_PATH,
    custom_field_key,
    is_supported_field_path,
)
from app.domain.template_config import FieldMappingConfig, MarkOptionConfig
from app.domain.template_fields import parse_legacy_spreadsheet_metadata
from app.image_processing.artifacts import ArtifactWriter, encode_png
from app.image_processing.line_detection import LineDetector, adaptive_ink_mask
from app.layout_analysis.detector import LayoutDetector, TextRegion
from app.layout_analysis.models import (
    LayoutCell,
    LayoutDefinition,
    LayoutLabelPoolEntry,
    LayoutRelation,
)
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.segmentation.candidate_regions import (
    handwriting_candidate_regions,
    handwriting_candidates_payload,
)
from app.segmentation.heuristic import HeuristicHandwritingSegmenter
from app.services.label_cell_ocr_service import (
    protected_label_cell_ids,
    recognize_label_cells,
)
from app.services.template_management_service import TemplateManagementService
from app.services.template_mutation import (
    LAYOUT_INPUT_REVISION_KEY,
    LAYOUT_VERIFICATION_FINGERPRINT_KEY,
    begin_immediate,
    invalidated_layout_quality,
    template_mutation_lock,
)
from app.storage.local_storage import LocalStorageService


@dataclass(frozen=True, slots=True)
class _VerificationInputs:
    layout: LayoutDefinition
    fingerprint: str


@dataclass(frozen=True, slots=True)
class _HandwritingFinalization:
    detector: str
    clean_artifact_id: str
    mask_artifact_id: str
    mask_source: str
    edited_mask_applied: bool
    edited_mask_applied_sha256: str | None
    input_cell_count: int
    coverage_ratio: float
    paddle_ocr_available: bool
    candidate_count: int


class LayoutService:
    def __init__(
        self,
        database: Database,
        storage: LocalStorageService,
        detector: LayoutDetector | None = None,
        handwriting_threshold: float = 0.55,
    ) -> None:
        self._database = database
        self._storage = storage
        self._detector = detector or LayoutDetector()
        self._handwriting_threshold = handwriting_threshold
        self._writer = ArtifactWriter(database, storage)

    def get(self, version_id: str) -> LayoutDefinition:
        with self._database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._not_found()
            layout = (
                LayoutDefinition.model_validate(version.layout_json)
                if version.layout_json
                else LayoutDefinition(
                    canonical_width=version.canonical_width,
                    canonical_height=version.canonical_height,
                )
            )
            legacy_config_path = (
                version.config_relative_path
                if version.mode == TemplateMode.SPREADSHEET_TEMPLATE.value
                and not layout.label_pool
                else None
            )
        if legacy_config_path is None:
            return layout
        legacy = parse_legacy_spreadsheet_metadata(
            self._storage.read_bytes(legacy_config_path).decode("utf-8")
        )
        if legacy is None:
            return layout
        _, field_catalog = legacy
        return layout.model_copy(
            update={
                "label_pool": [
                    LayoutLabelPoolEntry.model_validate(item) for item in field_catalog
                ]
            },
            deep=True,
        )

    def detect(self, version_id: str) -> LayoutDefinition:
        with self._database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._not_found()
            current_layout = (
                LayoutDefinition.model_validate(version.layout_json)
                if version.layout_json
                else LayoutDefinition(
                    canonical_width=version.canonical_width,
                    canonical_height=version.canonical_height,
                )
            )
            source_job = JobRepository(session).get(version.source_job_id or "")
            artifact = (
                ArtifactRepository(session).get_by_name(
                    job_id=source_job.id,
                    attempt_number=source_job.attempt_number,
                    artifact_name="normalized",
                )
                if source_job is not None
                else None
            )
            if artifact is None and version.clean_template_artifact_id:
                artifact = session.get(JobArtifact, version.clean_template_artifact_id)
            if artifact is None:
                raise AppError(
                    status=409,
                    code="CLEAN_TEMPLATE_NOT_READY",
                    title="Clean template is not ready",
                    detail="帳票整形画像がまだ生成されていません。",
                )
            relative_path = artifact.relative_path
        encoded = np.frombuffer(self._storage.read_bytes(relative_path), dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None:
            raise AppError(
                status=422,
                code="INVALID_TEMPLATE_IMAGE",
                title="Invalid template image",
                detail="帳票整形画像を読み込めませんでした。",
            )
        image_rgb = np.asarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), dtype=np.uint8)
        detected = self._detector.detect(image_rgb)
        return detected.model_copy(
            update={"label_pool": current_layout.label_pool},
            deep=True,
        )

    def recognize_labels(
        self,
        version_id: str,
        layout: LayoutDefinition,
    ) -> LayoutDefinition:
        with self._database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._not_found()
            if (
                layout.canonical_width != version.canonical_width
                or layout.canonical_height != version.canonical_height
            ):
                raise AppError(
                    status=422,
                    code="INVALID_LAYOUT_DIMENSIONS",
                    title="Invalid layout dimensions",
                    detail="レイアウト寸法がテンプレート画像と一致しません。",
                )
            artifact = (
                session.get(JobArtifact, version.clean_template_artifact_id)
                if version.clean_template_artifact_id
                else None
            )
            if artifact is None:
                source_job = JobRepository(session).get(version.source_job_id or "")
                artifact = (
                    ArtifactRepository(session).get_by_name(
                        job_id=source_job.id,
                        attempt_number=source_job.attempt_number,
                        artifact_name="normalized",
                    )
                    if source_job is not None
                    else None
                )
            if artifact is None:
                raise AppError(
                    status=409,
                    code="CLEAN_TEMPLATE_NOT_READY",
                    title="Clean template is not ready",
                    detail="ラベルOCRに使用する帳票整形画像がまだ生成されていません。",
                )
            relative_path = artifact.relative_path

        encoded = np.frombuffer(self._storage.read_bytes(relative_path), dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None:
            raise AppError(
                status=422,
                code="INVALID_TEMPLATE_IMAGE",
                title="Invalid template image",
                detail="ラベルOCR用の帳票画像を読み込めませんでした。",
            )
        image_rgb = np.asarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), dtype=np.uint8)
        result = recognize_label_cells(
            image_rgb,
            layout.cells,
            excluded_cell_ids=protected_label_cell_ids(layout),
        )
        warnings = [warning for warning in layout.warnings if not warning.startswith("LABEL_OCR_")]
        warnings.extend(result.warnings)
        return layout.model_copy(
            update={
                "cells": result.cells,
                "ocr_available": result.ocr_available,
                "warnings": warnings,
            }
        )

    def save(self, version_id: str, layout: LayoutDefinition) -> LayoutDefinition:
        with template_mutation_lock():
            return self._save_locked(version_id, layout)

    def _save_locked(self, version_id: str, layout: LayoutDefinition) -> LayoutDefinition:
        with self._database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._not_found()
            if (
                layout.canonical_width != version.canonical_width
                or layout.canonical_height != version.canonical_height
            ):
                raise AppError(
                    status=422,
                    code="INVALID_LAYOUT_DIMENSIONS",
                    title="Invalid layout dimensions",
                    detail="レイアウト寸法がテンプレート画像と一致しません。",
                )

        layout = layout.model_copy(
            update={
                "record_structure_verified": self._record_structure_is_complete(layout)
            }
        )
        mapping_finalized = False
        try:
            mappings = self._as_field_mappings(layout)
            mapped_cell_ids = {relation.input_cell_id for relation in layout.relations}
            mapped_cell_ids.update(
                cell.id for cell in layout.cells if cell.role == "mark" and cell.mark_sets
            )
            has_unassigned_fields = any(
                cell.role in {"input", "mark"} and cell.id not in mapped_cell_ids
                for cell in layout.cells
            )
            if mappings and not has_unassigned_fields:
                self._require_verified_record_structure(layout)
                mapping_finalized = True
            elif has_unassigned_fields:
                mappings = []
        except AppError as error:
            # A draft layout must remain resumable even while a mark field or
            # repeated-record binding is still being configured. Invalid
            # targets and duplicate mappings remain hard errors.
            if error.code not in {
                "MARK_OPTIONS_REQUIRED",
                "RECORD_STRUCTURE_REQUIRED",
                "RECORD_PARENT_LABEL_REQUIRED",
                "RECORD_KEY_REQUIRED",
            }:
                raise
            mappings = []
        TemplateManagementService(self._database, self._storage).save_field_mappings(
            version_id, mappings
        )
        with self._database.session() as session:
            begin_immediate(session)
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._not_found()
            version.layout_json = layout.model_dump(mode="json", by_alias=True)
            version.quality_json = {
                **invalidated_layout_quality(
                    version.quality_json,
                    bump_input_revision=True,
                ),
                "layoutCellCount": len(layout.cells),
                "layoutLabelCount": sum(cell.role == "label" for cell in layout.cells),
                "layoutParentLabelCount": sum(cell.role == "parent_label" for cell in layout.cells),
                "layoutInputCount": sum(cell.role == "input" for cell in layout.cells),
                "layoutMarkCount": sum(cell.role == "mark" for cell in layout.cells),
                "layoutRelationCount": len(layout.relations),
                "layoutDetector": layout.detector,
                "layoutFinalized": mapping_finalized,
                "layoutDraftSaved": True,
                "processingOrder": [
                    "PERSPECTIVE_CORRECTION",
                    "LAYOUT_CLASSIFICATION",
                    "LAYOUT_VERIFICATION",
                ],
            }
            session.commit()
        return layout

    def verify(self, version_id: str) -> LayoutDefinition:
        inputs = self._begin_verification(version_id)
        layout = inputs.layout
        if not layout.relations and not any(
            cell.role == "mark" and cell.mark_sets for cell in layout.cells
        ):
            raise AppError(
                status=422,
                code="LAYOUT_MEANING_REQUIRED",
                title="Layout meaning is required",
                detail="矩形の区分と保存先を意味づけしてから再検出してください。",
            )
        self._require_verified_record_structure(layout)
        finalization = self._finalize_handwriting(version_id, layout)
        self._complete_verification(
            version_id,
            inputs.fingerprint,
            finalization,
        )
        return layout

    def _begin_verification(self, version_id: str) -> _VerificationInputs:
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._not_found()
            layout = (
                LayoutDefinition.model_validate(version.layout_json)
                if version.layout_json
                else LayoutDefinition(
                    canonical_width=version.canonical_width,
                    canonical_height=version.canonical_height,
                )
            )
            if not layout.relations:
                raise AppError(
                    status=422,
                    code="LAYOUT_MEANING_REQUIRED",
                    title="Layout meaning is required",
                    detail="Meaning must be assigned to the template fields before verification.",
                )
            self._require_verified_record_structure(layout)
            fingerprint = self._verification_fingerprint(session, version)
            version.quality_json = invalidated_layout_quality(
                version.quality_json,
                bump_input_revision=False,
            )
            session.commit()
        return _VerificationInputs(layout=layout, fingerprint=fingerprint)

    def _complete_verification(
        self,
        version_id: str,
        expected_fingerprint: str,
        finalization: _HandwritingFinalization,
    ) -> None:
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._not_found()
            current_fingerprint = self._verification_fingerprint(session, version)
            if current_fingerprint != expected_fingerprint:
                version.quality_json = invalidated_layout_quality(
                    version.quality_json,
                    bump_input_revision=False,
                )
                session.commit()
                raise self._verification_conflict()
            version.clean_template_artifact_id = finalization.clean_artifact_id
            version.mask_artifact_id = finalization.mask_artifact_id
            version.quality_json = {
                **version.quality_json,
                "handwritingMaskSource": finalization.mask_source,
                "maskSource": finalization.mask_source,
                "editedMaskApplied": finalization.edited_mask_applied,
                "editedMaskAppliedSha256": finalization.edited_mask_applied_sha256,
                "handwritingInputCellCount": finalization.input_cell_count,
                "handwritingCoverageRatio": finalization.coverage_ratio,
                "layoutVerificationOpenCv": True,
                "layoutVerificationPaddleOcr": finalization.paddle_ocr_available,
                "layoutVerificationCandidateCount": finalization.candidate_count,
                "layoutVerified": True,
                "layoutVerificationDetector": finalization.detector,
                LAYOUT_VERIFICATION_FINGERPRINT_KEY: expected_fingerprint,
            }
            session.commit()

    def _verification_fingerprint(
        self,
        session: Session,
        version: TemplateVersion,
    ) -> str:
        source_job = JobRepository(session).get(version.source_job_id or "")
        if source_job is None:
            raise AppError(
                status=409,
                code="SOURCE_IMAGE_NOT_READY",
                title="Source image is not ready",
                detail="The normalized source image is not available.",
            )
        edited_mask_artifact = self._active_edited_mask_artifact(
            session,
            version,
            source_job,
        )
        config_content = self._storage.read_bytes(version.config_relative_path)
        edited_mask: dict[str, str] | None = None
        if edited_mask_artifact is not None:
            edited_content = self._storage.read_bytes(edited_mask_artifact.relative_path)
            edited_mask = {
                "artifactId": edited_mask_artifact.id,
                "relativePath": edited_mask_artifact.relative_path,
                "recordedSha256": edited_mask_artifact.sha256,
                "actualSha256": hashlib.sha256(edited_content).hexdigest(),
            }
        quality = version.quality_json
        payload = {
            "layout": version.layout_json,
            "config": {
                "relativePath": version.config_relative_path,
                "actualSha256": hashlib.sha256(config_content).hexdigest(),
            },
            "editedMask": edited_mask,
            "inputRevision": quality.get(LAYOUT_INPUT_REVISION_KEY, 0),
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def _active_edited_mask_artifact(
        self,
        session: Session,
        version: TemplateVersion,
        source_job: ProcessingJob,
    ) -> JobArtifact | None:
        quality = version.quality_json
        has_saved_edit = bool(
            quality.get("editedMaskSha256") or quality.get("editedMaskArtifactId")
        )
        has_inherited_edit = (
            quality.get("maskSource") == "USER_EDITED"
            or quality.get("handwritingMaskSource") == "USER_EDITED"
        )
        edited_mask_artifact = None
        edited_artifact_id = quality.get("editedMaskArtifactId")
        if has_saved_edit and isinstance(edited_artifact_id, str):
            candidate_artifact = session.get(JobArtifact, edited_artifact_id)
            if (
                candidate_artifact is not None
                and candidate_artifact.job_id == source_job.id
                and candidate_artifact.artifact_name == "handwritingMaskEdited"
            ):
                edited_mask_artifact = candidate_artifact
        if has_saved_edit and edited_mask_artifact is None:
            edited_mask_artifact = ArtifactRepository(session).get_by_name(
                job_id=source_job.id,
                attempt_number=source_job.attempt_number,
                artifact_name="handwritingMaskEdited",
            )
        if has_saved_edit and edited_mask_artifact is None and version.mask_artifact_id:
            candidate_artifact = session.get(JobArtifact, version.mask_artifact_id)
            if (
                candidate_artifact is not None
                and candidate_artifact.job_id == source_job.id
                and candidate_artifact.artifact_name == "handwritingMaskEdited"
            ):
                edited_mask_artifact = candidate_artifact
        if not has_saved_edit and has_inherited_edit and version.mask_artifact_id:
            candidate_artifact = session.get(JobArtifact, version.mask_artifact_id)
            if (
                candidate_artifact is not None
                and candidate_artifact.job_id == source_job.id
                and candidate_artifact.artifact_name in {"handwritingMaskEdited", "handwritingMask"}
            ):
                edited_mask_artifact = candidate_artifact
        if (has_saved_edit or has_inherited_edit) and edited_mask_artifact is None:
            raise AppError(
                status=409,
                code="EDITED_MASK_NOT_READY",
                title="Edited handwriting mask is not ready",
                detail="Save the edited handwriting mask again before verification.",
            )
        return edited_mask_artifact

    def _finalize_handwriting(
        self, version_id: str, layout: LayoutDefinition
    ) -> _HandwritingFinalization:
        with self._database.session() as session:
            version = TemplateRepository(session).get_version(version_id)
            if version is None:
                raise self._not_found()
            source_job = JobRepository(session).get(version.source_job_id or "")
            if source_job is None:
                raise AppError(
                    status=409,
                    code="SOURCE_IMAGE_NOT_READY",
                    title="Source image is not ready",
                    detail="歪み補正済みの元画像が見つかりません。",
                )
            artifact = ArtifactRepository(session).get_by_name(
                job_id=source_job.id,
                attempt_number=source_job.attempt_number,
                artifact_name="normalized",
            )
            if artifact is None:
                raise AppError(
                    status=409,
                    code="NORMALIZED_IMAGE_NOT_READY",
                    title="Normalized image is not ready",
                    detail="歪み補正済み画像が生成されていません。",
                )
            quality = version.quality_json
            has_saved_edit = bool(
                quality.get("editedMaskSha256") or quality.get("editedMaskArtifactId")
            )
            has_inherited_edit = (
                quality.get("maskSource") == "USER_EDITED"
                or quality.get("handwritingMaskSource") == "USER_EDITED"
            )
            edited_mask_artifact = None
            edited_artifact_id = quality.get("editedMaskArtifactId")
            if has_saved_edit and isinstance(edited_artifact_id, str):
                candidate_artifact = session.get(JobArtifact, edited_artifact_id)
                if (
                    candidate_artifact is not None
                    and candidate_artifact.job_id == source_job.id
                    and candidate_artifact.artifact_name == "handwritingMaskEdited"
                ):
                    edited_mask_artifact = candidate_artifact
            if has_saved_edit and edited_mask_artifact is None:
                edited_mask_artifact = ArtifactRepository(session).get_by_name(
                    job_id=source_job.id,
                    attempt_number=source_job.attempt_number,
                    artifact_name="handwritingMaskEdited",
                )
            if has_saved_edit and edited_mask_artifact is None and version.mask_artifact_id:
                candidate_artifact = session.get(JobArtifact, version.mask_artifact_id)
                if (
                    candidate_artifact is not None
                    and candidate_artifact.job_id == source_job.id
                    and candidate_artifact.artifact_name == "handwritingMaskEdited"
                ):
                    edited_mask_artifact = candidate_artifact
            if not has_saved_edit and has_inherited_edit and version.mask_artifact_id:
                candidate_artifact = session.get(JobArtifact, version.mask_artifact_id)
                if (
                    candidate_artifact is not None
                    and candidate_artifact.job_id == source_job.id
                    and candidate_artifact.artifact_name
                    in {"handwritingMaskEdited", "handwritingMask"}
                ):
                    edited_mask_artifact = candidate_artifact
            if (has_saved_edit or has_inherited_edit) and edited_mask_artifact is None:
                raise AppError(
                    status=409,
                    code="EDITED_MASK_NOT_READY",
                    title="Edited handwriting mask is not ready",
                    detail="保存済みの編集マスクが見つかりません。マスクを再保存してください。",
                )
            relative_path = artifact.relative_path
            edited_mask_relative_path = (
                edited_mask_artifact.relative_path if edited_mask_artifact is not None else None
            )
            edited_mask_sha256 = (
                edited_mask_artifact.sha256 if edited_mask_artifact is not None else None
            )
            job_id = source_job.id
            attempt = source_job.attempt_number
            template_id = version.template_id
            version_number = version.version_number

        image_rgb = self._read_rgb(relative_path)
        edited_mask = (
            self._read_edited_mask(
                edited_mask_relative_path,
                expected_shape=(image_rgb.shape[0], image_rgb.shape[1]),
            )
            if edited_mask_relative_path is not None
            else None
        )
        verified_layout, text_regions = self._detector.detect_with_text_regions(image_rgb)
        probability = HeuristicHandwritingSegmenter().predict(image_rgb)
        scope = np.zeros(image_rgb.shape[:2], dtype=np.uint8)
        cells = {cell.id: cell for cell in layout.cells}
        input_ids = {
            relation.input_cell_id
            for relation in layout.relations
            if cells[relation.input_cell_id].role == "input"
        }
        for input_id in input_ids:
            cell = cells[input_id]
            inset = max(2, min(5, cell.width // 12, cell.height // 12))
            x1 = min(cell.x + inset, cell.x + cell.width)
            y1 = min(cell.y + inset, cell.y + cell.height)
            x2 = max(x1, cell.x + cell.width - inset)
            y2 = max(y1, cell.y + cell.height - inset)
            scope[y1:y2, x1:x2] = 255

        automatic_mask = ((probability >= self._handwriting_threshold) & (scope > 0)).astype(
            np.uint8
        ) * 255
        automatic_mask = np.asarray(
            cv2.morphologyEx(
                automatic_mask,
                cv2.MORPH_CLOSE,
                np.ones((3, 3), np.uint8),
            ),
            dtype=np.uint8,
        )
        automatic_mask = np.asarray(
            cv2.dilate(automatic_mask, np.ones((3, 3), np.uint8)),
            dtype=np.uint8,
        )
        gray = np.asarray(
            cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY),
            dtype=np.uint8,
        )
        line_mask = LineDetector().detect(gray)
        protected_lines = np.asarray(
            cv2.dilate(line_mask, np.ones((2, 2), np.uint8)),
            dtype=np.uint8,
        )
        automatic_mask[protected_lines > 0] = 0
        direct_text_mask = self._direct_text_fallback_mask(
            gray=gray,
            protected_lines=protected_lines,
            scope=scope,
            layout=layout,
            text_regions=text_regions,
        )
        mask = edited_mask.copy() if edited_mask is not None else automatic_mask
        mask = np.asarray(cv2.bitwise_or(mask, direct_text_mask), dtype=np.uint8)
        mask_source = "USER_EDITED" if edited_mask is not None else "USER_CLASSIFIED_LAYOUT"

        handwriting_only = np.full_like(image_rgb, 255)
        handwriting_only[mask > 0] = image_rgb[mask > 0]
        clean = np.asarray(cv2.inpaint(image_rgb, mask, 4, cv2.INPAINT_TELEA), dtype=np.uint8)
        if edited_mask is None:
            clean[line_mask > 0] = image_rgb[line_mask > 0]
        debug = image_rgb.copy()
        red = np.full_like(image_rgb, (235, 55, 55))
        selected = mask > 0
        debug[selected] = cv2.addWeighted(image_rgb, 0.35, red, 0.65, 0)[selected]
        candidates = handwriting_candidate_regions(mask, probability)
        input_cells = [cells[input_id] for input_id in input_ids]
        verified_text_cells = [cell for cell in verified_layout.cells if cell.text.strip()]
        for candidate in candidates:
            center_x = (float(candidate["x"]) + float(candidate["width"]) / 2) * image_rgb.shape[1]
            center_y = (float(candidate["y"]) + float(candidate["height"]) / 2) * image_rgb.shape[0]
            input_cell = next(
                (
                    cell
                    for cell in input_cells
                    if cell.x <= center_x <= cell.x + cell.width
                    and cell.y <= center_y <= cell.y + cell.height
                ),
                None,
            )
            candidate["inputCellId"] = input_cell.id if input_cell else ""
            candidate["opencvCellDetected"] = bool(
                input_cell
                and any(
                    self._cell_center_is_inside(detected, input_cell)
                    for detected in verified_layout.cells
                )
            )
            candidate["paddleTextDetected"] = bool(
                input_cell
                and any(
                    self._cell_center_is_inside(detected, input_cell)
                    for detected in verified_text_cells
                )
            )
            candidate["source"] = (
                f"{'user-edited' if edited_mask is not None else 'heuristic'}"
                f"+{verified_layout.detector}"
            )

        verification_overlay = image_rgb.copy()
        for detected in verified_layout.cells:
            color = (123, 63, 197) if detected.text.strip() else (115, 124, 133)
            cv2.rectangle(
                verification_overlay,
                (detected.x, detected.y),
                (detected.x + detected.width, detected.y + detected.height),
                color,
                2,
            )
        for cell in layout.cells:
            if cell.role == "ignored":
                continue
            color = (
                (22, 128, 93)
                if cell.role == "parent_label"
                else (179, 107, 0)
                if cell.role == "label"
                else (0, 119, 125)
            )
            cv2.rectangle(
                verification_overlay,
                (cell.x, cell.y),
                (cell.x + cell.width, cell.y + cell.height),
                color,
                4,
            )

        clean_artifact = self._writer.image(
            job_id=job_id,
            attempt_number=attempt,
            name="cleanTemplate",
            file_name="clean_template.png",
            image=clean,
            replace=True,
        )
        mask_artifact = self._writer.image(
            job_id=job_id,
            attempt_number=attempt,
            name="handwritingMask",
            file_name="handwriting_mask.png",
            image=mask,
            rgb=False,
            replace=True,
        )
        self._writer.image(
            job_id=job_id,
            attempt_number=attempt,
            name="handwritingProbability",
            file_name="handwriting_probability.png",
            image=np.clip(probability * 255, 0, 255).astype(np.uint8),
            rgb=False,
            replace=True,
        )
        self._writer.image(
            job_id=job_id,
            attempt_number=attempt,
            name="handwritingOnly",
            file_name="handwriting_only.png",
            image=handwriting_only,
            replace=True,
        )
        self._writer.image(
            job_id=job_id,
            attempt_number=attempt,
            name="debugOverlay",
            file_name="debug_overlay.png",
            image=debug,
            replace=True,
        )
        self._writer.image(
            job_id=job_id,
            attempt_number=attempt,
            name="cleanPreview",
            file_name="clean_preview.png",
            image=clean,
            replace=True,
        )
        self._writer.image(
            job_id=job_id,
            attempt_number=attempt,
            name="layoutVerificationOverlay",
            file_name="layout_verification_overlay.png",
            image=verification_overlay,
            replace=True,
        )
        candidates_payload = handwriting_candidates_payload(mask, candidates)
        candidates_payload["detectors"] = {
            "opencv": True,
            "paddleOcr": verified_layout.ocr_available,
            "heuristicSegmentation": True,
        }
        candidates_payload["verifiedInputCellCount"] = len(input_cells)
        candidates_payload["maskSource"] = mask_source
        self._writer.json(
            job_id=job_id,
            attempt_number=attempt,
            name="handwritingCandidates",
            file_name="handwriting_candidates.json",
            value=candidates_payload,
            replace=True,
        )
        base_path = f"templates/{template_id}/versions/{version_number}"
        self._storage.write_bytes(f"{base_path}/clean_template.png", encode_png(clean))
        self._storage.write_bytes(f"{base_path}/handwriting_mask.png", encode_png(mask, rgb=False))
        return _HandwritingFinalization(
            detector=verified_layout.detector,
            clean_artifact_id=clean_artifact.id,
            mask_artifact_id=mask_artifact.id,
            mask_source=mask_source,
            edited_mask_applied=edited_mask is not None,
            edited_mask_applied_sha256=edited_mask_sha256,
            input_cell_count=len(input_ids),
            coverage_ratio=float((mask > 0).mean()),
            paddle_ocr_available=verified_layout.ocr_available,
            candidate_count=len(candidates),
        )

    def _read_rgb(self, relative_path: str) -> NDArray[np.uint8]:
        encoded = np.frombuffer(self._storage.read_bytes(relative_path), dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None:
            raise AppError(
                status=422,
                code="INVALID_TEMPLATE_IMAGE",
                title="Invalid template image",
                detail="帳票画像を読み込めませんでした。",
            )
        return np.asarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), dtype=np.uint8)

    def _read_edited_mask(
        self,
        relative_path: str,
        *,
        expected_shape: tuple[int, int],
    ) -> NDArray[np.uint8]:
        encoded = np.frombuffer(self._storage.read_bytes(relative_path), dtype=np.uint8)
        mask = cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE)
        if mask is None or mask.shape != expected_shape:
            raise AppError(
                status=409,
                code="EDITED_MASK_NOT_READY",
                title="Edited handwriting mask is not ready",
                detail="保存済みの編集マスクを読み込めません。マスクを再保存してください。",
            )
        return np.asarray((mask >= 128).astype(np.uint8) * 255, dtype=np.uint8)

    @staticmethod
    def _cell_center_is_inside(detected: LayoutCell, outer: LayoutCell) -> bool:
        center_x = detected.x + detected.width / 2
        center_y = detected.y + detected.height / 2
        return (
            outer.x <= center_x <= outer.x + outer.width
            and outer.y <= center_y <= outer.y + outer.height
        )

    @staticmethod
    def _direct_text_fallback_mask(
        *,
        gray: NDArray[np.uint8],
        protected_lines: NDArray[np.uint8],
        scope: NDArray[np.uint8],
        layout: LayoutDefinition,
        text_regions: list[TextRegion],
    ) -> NDArray[np.uint8]:
        """Recover OCR-confirmed ink missed by the handwriting heuristic.

        This is deliberately limited to directly-mapped or mixed-role cells.
        Conventional label/input pairs keep the existing conservative mask.
        """
        fallback = np.zeros(gray.shape, dtype=np.uint8)
        cell_by_id = {cell.id: cell for cell in layout.cells}
        direct_input_ids = {
            relation.input_cell_id for relation in layout.relations if not relation.label_cell_ids
        }
        eligible = [
            cell
            for input_id in {
                *direct_input_ids,
                *(
                    cell.id
                    for cell in layout.cells
                    if cell.role == "input" and cell.printed_label_text.strip()
                ),
            }
            if (cell := cell_by_id.get(input_id)) is not None and cell.role == "input"
        ]
        if not eligible or not text_regions:
            return fallback

        ink = adaptive_ink_mask(gray) > 0
        for region in text_regions:
            input_cell = next(
                (
                    cell
                    for cell in eligible
                    if LayoutService._text_region_center_is_inside(region, cell)
                ),
                None,
            )
            if input_cell is None:
                continue
            if LayoutService._is_exact_printed_label(
                region.text,
                input_cell.printed_label_text,
            ):
                continue
            polygon = np.asarray(region.points, dtype=np.int32)
            polygon[:, 0] = np.clip(polygon[:, 0], 0, gray.shape[1] - 1)
            polygon[:, 1] = np.clip(polygon[:, 1], 0, gray.shape[0] - 1)
            region_mask = np.zeros(gray.shape, dtype=np.uint8)
            cv2.fillPoly(region_mask, [polygon], (255,))
            selected = (region_mask > 0) & ink & (scope > 0) & (protected_lines == 0)
            fallback[selected] = 255

        fallback = np.asarray(
            cv2.dilate(fallback, np.ones((2, 2), np.uint8)),
            dtype=np.uint8,
        )
        fallback[(scope == 0) | (protected_lines > 0)] = 0
        return fallback

    @staticmethod
    def _text_region_center_is_inside(region: TextRegion, cell: LayoutCell) -> bool:
        left, top, right, bottom = region.bounds
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        return (
            cell.x <= center_x <= cell.x + cell.width and cell.y <= center_y <= cell.y + cell.height
        )

    @staticmethod
    def _is_exact_printed_label(detected_text: str, printed_label_text: str) -> bool:
        def normalize(value: str) -> str:
            return "".join(character for character in value.strip().lower() if character.isalnum())

        printed = normalize(printed_label_text)
        return bool(printed) and normalize(detected_text) == printed

    @staticmethod
    def _as_field_mappings(layout: LayoutDefinition) -> list[FieldMappingConfig]:
        cell_by_id = {cell.id: cell for cell in layout.cells}
        relations_by_input: dict[str, list[LayoutRelation]] = {}
        for relation in layout.relations:
            relations_by_input.setdefault(relation.input_cell_id, []).append(relation)

        mappings: list[FieldMappingConfig] = []
        target_paths: set[str] = set()
        for input_id, untyped_relations in relations_by_input.items():
            input_cell = cell_by_id[input_id]
            # A mark relation records the visual label-to-cell association. The
            # actual output bindings come from mark_sets below, so it must not
            # create a duplicate text mapping for the same rectangle.
            if input_cell.role == "mark":
                continue
            if input_cell.role != "input":
                raise AppError(
                    status=422,
                    code="INVALID_INPUT_CELL",
                    title="Invalid input cell",
                    detail=f"{input_id} は記入欄として設定されていません。",
                )
            relations = sorted(
                untyped_relations,
                key=lambda item: (item.axis, item.order),
            )
            relation = relations[0]
            target_path = relation.target_path
            if not is_supported_field_path(target_path):
                raise AppError(
                    status=422,
                    code="INVALID_FIELD_MAPPING",
                    title="Invalid field mapping",
                    detail=f"未対応の保存先です: {target_path}",
                )
            if target_path in target_paths:
                raise AppError(
                    status=422,
                    code="DUPLICATE_FIELD_TARGET",
                    title="Duplicate field target",
                    detail=f"同じ保存先が複数の記入欄に割り当てられています: {target_path}",
                )
            target_paths.add(target_path)
            label_ids = [label_id for item in relations for label_id in item.label_cell_ids]
            label_texts = [
                cell_by_id[label_id].text.strip()
                for label_id in dict.fromkeys(label_ids)
                if cell_by_id[label_id].text.strip()
            ]
            target = FIELD_TARGET_BY_PATH.get(target_path)
            mappings.append(
                FieldMappingConfig(
                    mapping_id=f"layout_{input_id}",
                    target_path=target_path,
                    label=(
                        " / ".join(label_texts)
                        or input_cell.printed_label_text.strip()
                        or (target.label if target else custom_field_key(target_path))
                        or target_path
                    ),
                    x=input_cell.x,
                    y=input_cell.y,
                    width=input_cell.width,
                    height=input_cell.height,
                    padding=4,
                    enabled=True,
                    model_detail="high",
                    printed_label_text=input_cell.printed_label_text.strip(),
                )
            )
        for cell in layout.cells:
            if cell.role != "mark":
                continue
            mark_label_ids = [
                label_id
                for relation in relations_by_input.get(cell.id, [])
                for label_id in relation.label_cell_ids
            ]
            mark_label_texts = [
                cell_by_id[label_id].text.strip()
                for label_id in dict.fromkeys(mark_label_ids)
                if cell_by_id[label_id].text.strip()
            ]
            if not cell.mark_sets:
                raise AppError(
                    status=422,
                    code="MARK_OPTIONS_REQUIRED",
                    title="Mark options are required",
                    detail=f"{cell.id} の選択肢を設定してください。",
                )
            for mark_set in cell.mark_sets:
                target_path = mark_set.target_path
                if not is_supported_field_path(target_path):
                    raise AppError(
                        status=422,
                        code="INVALID_FIELD_MAPPING",
                        title="Invalid field mapping",
                        detail=f"未対応の保存先です: {target_path}",
                    )
                if target_path in target_paths:
                    raise AppError(
                        status=422,
                        code="DUPLICATE_FIELD_TARGET",
                        title="Duplicate field target",
                        detail=f"同じ保存先が複数の矩形に割り当てられています: {target_path}",
                    )
                target_paths.add(target_path)
                target = FIELD_TARGET_BY_PATH.get(target_path)
                mapping_id = f"layout_{cell.id}_{mark_set.id}"
                if len(mapping_id) > 100:
                    mapping_id = mapping_id[:100]
                mappings.append(
                    FieldMappingConfig(
                        mapping_id=mapping_id,
                        target_path=target_path,
                        label=(
                            " / ".join(mark_label_texts)
                            or (target.label if target else target_path)
                        ),
                        x=cell.x,
                        y=cell.y,
                        width=cell.width,
                        height=cell.height,
                        padding=0,
                        enabled=True,
                        model_detail="high",
                        mapping_type="mark",
                        mark_selection_mode=mark_set.selection_mode,
                        mark_options=[
                            MarkOptionConfig.model_validate(
                                option.model_dump(mode="json", by_alias=True)
                            )
                            for option in mark_set.options
                        ],
                    )
                )
        return mappings

    @staticmethod
    def _record_structure_is_complete(layout: LayoutDefinition) -> bool:
        has_array_bindings = any(
            relation.target_path.startswith(("saleCalves.", "calvings."))
            for relation in layout.relations
        ) or any(
            mark_set.target_path.startswith(("saleCalves.", "calvings."))
            for cell in layout.cells
            if cell.role == "mark"
            for mark_set in cell.mark_sets
        )
        if not has_array_bindings:
            return layout.record_structure_verified
        try:
            LayoutService._require_verified_record_structure(layout)
        except AppError:
            return False
        return True

    @staticmethod
    def _require_verified_record_structure(layout: LayoutDefinition) -> None:
        array_relations = [
            relation
            for relation in layout.relations
            if relation.target_path.startswith(("saleCalves.", "calvings."))
        ]
        mark_bindings = [
            (cell.id, mark_set.target_path)
            for cell in layout.cells
            if cell.role == "mark"
            for mark_set in cell.mark_sets
            if mark_set.target_path.startswith(("saleCalves.", "calvings."))
        ]
        if not array_relations and not mark_bindings:
            return
        grouped_inputs = {
            input_id for group in layout.record_groups for input_id in group.input_cell_ids
        }
        missing_inputs = sorted(
            {
                relation.input_cell_id
                for relation in array_relations
                if relation.input_cell_id not in grouped_inputs
            }
            | {cell_id for cell_id, _ in mark_bindings if cell_id not in grouped_inputs}
        )
        if not layout.record_sections or not layout.record_groups or missing_inputs:
            raise AppError(
                status=422,
                code="RECORD_STRUCTURE_REQUIRED",
                title="Record structure is required",
                detail=(
                    "売却子牛・繁殖履歴の記入欄は、行レコードへまとめて"
                    "親セクションを設定してください。"
                ),
                metadata={"unassignedInputCellIds": missing_inputs},
            )
        grouped_section_ids = {group.section_id for group in layout.record_groups}
        missing_parent_sections = sorted(
            section.id
            for section in layout.record_sections
            if section.id in grouped_section_ids and section.parent_label_cell_id is None
        )
        if missing_parent_sections:
            raise AppError(
                status=422,
                code="RECORD_PARENT_LABEL_REQUIRED",
                title="Record parent label is required",
                detail=(
                    "列ラベルから作成したレコードを表全体の親ラベルへ接続してください。"
                ),
                metadata={"recordSectionIds": missing_parent_sections},
            )
        relations_by_input: dict[str, list[LayoutRelation]] = {}
        for relation in array_relations:
            relations_by_input.setdefault(relation.input_cell_id, []).append(relation)
        missing_keys = [
            group.id
            for group in layout.record_groups
            if not group.record_key_value
            and not any(
                relation.target_path.endswith(".parity")
                for input_id in group.input_cell_ids
                for relation in relations_by_input.get(input_id, [])
            )
        ]
        if missing_keys:
            raise AppError(
                status=422,
                code="RECORD_KEY_REQUIRED",
                title="Record key is required",
                detail="各行レコードの産次キーを入力するか、産次セルを意味づけしてください。",
                metadata={"recordGroupIds": missing_keys},
            )

    @staticmethod
    def _verification_conflict() -> AppError:
        return AppError(
            status=409,
            code="LAYOUT_VERIFICATION_CONFLICT",
            title="Template inputs changed during verification",
            detail="レイアウトまたは手書きマスクが更新されました。最新状態で再検証してください。",
        )

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            status=404,
            code="TEMPLATE_NOT_FOUND",
            title="Template version not found",
            detail="指定されたテンプレート版が見つかりません。",
        )
