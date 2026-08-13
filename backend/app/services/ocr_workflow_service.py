from __future__ import annotations

import asyncio
import hashlib
import logging

from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import Database
from app.domain.ocr_models import BoundingBox, FieldReading, OcrDocument, ReadingStatus
from app.domain.sale_notes import SALE_NOTE_SEX_SYMBOLS
from app.image_processing.artifacts import ArtifactWriter, encode_png
from app.ocr.base import (
    OcrProvider,
    OcrProviderRequest,
    OcrRegionInput,
)
from app.ocr.disabled_provider import DisabledOcrProvider
from app.ocr.mock_provider import MockOcrProvider
from app.ocr.openai_provider import OpenAIOcrProvider
from app.ocr.text_presence import PaddleTextPresenceGate
from app.repositories.job_repository import JobRepository
from app.repositories.ocr_result_repository import OcrResultRepository
from app.services.extraction_service import ExtractionOutput
from app.storage.local_storage import LocalStorageService
from app.validation.business_validators import DeterministicValidator

logger = logging.getLogger(__name__)


class OcrWorkflowService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        storage: LocalStorageService,
    ) -> None:
        self._settings = settings
        self._database = database
        self._writer = ArtifactWriter(database, storage)
        self._validator = DeterministicValidator()
        self._presence_gate = PaddleTextPresenceGate()

    def process(self, extraction: ExtractionOutput) -> None:
        with self._database.session() as session:
            job = JobRepository(session).get(extraction.job_id)
            if job is None:
                raise ValueError("OCR job not found")
            job_snapshot = dict(job.config_snapshot_json)
            ocr_enabled = bool(job_snapshot.get("ocrEnabled", True))
        provider_name = (
            str(job_snapshot.get("ocrProvider", self._settings.ocr.provider))
            if ocr_enabled
            else "disabled"
        )
        provider = self._create_provider(provider_name)
        sire_name_pool = tuple(
            value
            for value in job_snapshot.get("sireNamePool", [])
            if isinstance(value, str) and value
        )
        sire_name_suffixes = tuple(
            value
            for value in job_snapshot.get("sireNameAllowedSuffixes", [])
            if isinstance(value, str) and value
        )
        sale_note_symbols = tuple(
            value
            for value in job_snapshot.get(
                "saleNoteSexSymbolPool",
                SALE_NOTE_SEX_SYMBOLS,
            )
            if value in SALE_NOTE_SEX_SYMBOLS
        ) or SALE_NOTE_SEX_SYMBOLS
        region_configs = (
            extraction.config.mapped_ocr_regions()
            if provider_name == "openai"
            else extraction.config.ocr_regions()
        )
        all_regions: list[OcrRegionInput] = []
        for index, region in enumerate(region_configs, start=1):
            if region.region_id not in extraction.crops:
                continue
            openai_mapping_id = (
                f"cell_{index:04d}"
                if provider_name == "openai"
                else region.region_id
            )
            target_path = (
                region.expected_fields[0]
                if len(region.expected_fields) == 1
                else None
            )
            is_sire_name = bool(
                target_path and target_path.startswith("saleCalves.")
                and target_path.endswith(".sireName")
            )
            is_sale_note = bool(
                target_path
                and target_path.startswith("saleCalves.")
                and target_path.endswith(".notes")
            )
            is_birth_date = target_path == "document.birthDate"
            crop = extraction.crops[region.region_id]
            printed_label_excluded = bool(region.printed_label_text.strip())
            all_regions.append(
                OcrRegionInput(
                    region_id=openai_mapping_id,
                    image_png=encode_png(
                        crop.handwriting_only
                        if printed_label_excluded
                        else crop.aligned_color
                    ),
                    handwriting_png=(
                        None
                        if printed_label_excluded
                        else encode_png(crop.handwriting_only)
                    ),
                    target_path=target_path,
                    source_region_id=(
                        region.region_id if provider_name == "openai" else None
                    ),
                    bounding_box=extraction.crops[region.region_id].bbox,
                    candidates=(
                        sire_name_pool
                        if is_sire_name
                        else sale_note_symbols
                        if is_sale_note
                        else ()
                    ),
                    allowed_suffixes=sire_name_suffixes if is_sire_name else (),
                    candidate_type=(
                        "sire_name"
                        if is_sire_name
                        else "sale_calf_note_prefix"
                        if is_sale_note
                        else None
                    ),
                    transcription_format=(
                        "japanese_era_date_numbers" if is_birth_date else None
                    ),
                    printed_label_excluded=printed_label_excluded,
                    printed_label_text=region.printed_label_text.strip(),
                )
            )
        mapped_cells_payload: dict[str, object] | None = None
        if provider_name == "openai":
            if not all_regions or any(not region.target_path for region in all_regions):
                raise AppError(
                    status=409,
                    code="TEMPLATE_FIELD_MAPPING_REQUIRED",
                    title="Template field mappings are required",
                    detail=(
                        "OpenAI読み取りには意味づけ済みの手書きセルが必要です。"
                        "ラベルや帳票全体からの推測読み取りは行いません。"
                    ),
                )
            self._progress(extraction.job_id, "PREPARING_MAPPED_CELLS", 72)
            if self._presence_gate.available:
                presence_by_id = {
                    region.region_id: self._presence_gate.inspect(
                        extraction.crops[
                            region.source_region_id or region.region_id
                        ].handwriting_only
                    )
                    for region in all_regions
                }
                self._writer.json(
                    job_id=extraction.job_id,
                    attempt_number=extraction.attempt_number,
                    name="paddleTextPresence",
                    file_name="paddle_text_presence.json",
                    value={
                        "engine": "paddleocr",
                        "gatingEnabled": False,
                        "classifiedInputCount": len(all_regions),
                        "detectedTextCount": sum(
                            item.detected for item in presence_by_id.values()
                        ),
                        "openAiCropCount": len(all_regions),
                        "regions": [
                            presence_by_id[region.region_id].to_dict(
                                region_id=region.region_id
                            )
                            for region in all_regions
                        ],
                    },
                )
            mapped_cells_payload = self._mapped_cells_payload(
                extraction,
                all_regions,
                mapping_hash=job_snapshot.get("fieldMappingHash"),
            )
            self._writer.json(
                job_id=extraction.job_id,
                attempt_number=extraction.attempt_number,
                name="mappedCells",
                file_name="mapped_cells.json",
                value=mapped_cells_payload,
            )
        request = OcrProviderRequest(
            regions=all_regions,
            template_id=extraction.config.template_id,
            template_version=str(extraction.config.template_version),
            should_cancel=lambda: self._cancel_requested(extraction.job_id),
        )
        if self._cancel_requested(extraction.job_id):
            return
        self._progress(extraction.job_id, "CALLING_OCR", 78)
        response = asyncio.run(provider.extract(request))
        response.document.template_id = request.template_id
        response.document.template_version = request.template_version
        mother_individual_id = job_snapshot.get("motherIndividualId")
        if isinstance(mother_individual_id, str):
            response.document.document.unified_ear_tag = FieldReading(
                raw=mother_individual_id,
                normalized=mother_individual_id,
                status=ReadingStatus.READABLE,
                alternatives=[],
                review_reason=None,
                source_region_id="manual_mother_individual_id",
                bounding_box=None,
            )
        response.document.processing_metadata.model_id = response.model_id
        response.document.processing_metadata.prompt_version = response.prompt_version
        self._progress(extraction.job_id, "VALIDATING_RESULT", 90)
        configured_paths = {
            region.target_path
            for region in all_regions
            if region.target_path
        }
        configured_paths.update(
            self._apply_record_keys(
                response.document,
                job_snapshot.get("recordStructure"),
                configured_paths,
            )
        )
        report = self._validator.validate(
            response.document,
            image_quality=extraction.quality,
            configured_paths=configured_paths,
        )
        response_document = self._validator.merge_review_items(response.document, report)
        if mapped_cells_payload is not None:
            result_json = response_document.model_dump(mode="json", by_alias=True)
            cells = mapped_cells_payload.get("cells")
            if isinstance(cells, list):
                for cell in cells:
                    if not isinstance(cell, dict):
                        continue
                    target_path = cell.get("targetPath")
                    if isinstance(target_path, str):
                        cell["response"] = self._value_at(result_json, target_path)
            mapped_cells_payload["provider"] = response.provider
            mapped_cells_payload["modelId"] = response.model_id
            mapped_cells_payload["apiCallCount"] = response.api_call_count
            self._writer.json(
                job_id=extraction.job_id,
                attempt_number=extraction.attempt_number,
                name="mappedCells",
                file_name="mapped_cells.json",
                value=mapped_cells_payload,
                replace=True,
            )
        response = response.__class__(
            document=response_document,
            provider=response.provider,
            model_id=response.model_id,
            prompt_version=response.prompt_version,
            api_call_count=response.api_call_count,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost=response.estimated_cost,
            request_id=response.request_id,
            duration_ms=response.duration_ms,
        )
        with self._database.session() as session:
            stored = OcrResultRepository(session).save_provider_result(
                job_id=extraction.job_id,
                response=response,
                report=report,
            )
            revision = stored.revision
        self._progress(extraction.job_id, "WRITING_ARTIFACTS", 96)
        self._writer.json(
            job_id=extraction.job_id,
            attempt_number=extraction.attempt_number,
            name="result",
            file_name="result.json",
            value={
                "jobId": extraction.job_id,
                "revision": revision,
                "result": response_document.model_dump(mode="json", by_alias=True),
                "validation": report.to_dict(),
                "quality": extraction.quality,
            },
        )
        logger.info(
            "OCR provider call completed",
            extra={
                "jobId": extraction.job_id,
                "provider": response.provider,
                "modelId": response.model_id,
                "requestId": response.request_id,
                "inputTokens": response.input_tokens,
                "outputTokens": response.output_tokens,
                "durationMs": response.duration_ms,
            },
        )

    def _create_provider(self, provider_name: str) -> OcrProvider:
        if provider_name == "disabled":
            return DisabledOcrProvider()
        if provider_name == "mock":
            return MockOcrProvider()
        if provider_name == "openai":
            return OpenAIOcrProvider(self._settings.ocr)
        raise ValueError(f"Unsupported OCR provider: {provider_name}")

    def _progress(self, job_id: str, stage: str, percent: int) -> None:
        with self._database.session() as session:
            JobRepository(session).update(job_id, progress_stage=stage, progress_percent=percent)

    def _cancel_requested(self, job_id: str) -> bool:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            return job is None or job.cancel_requested

    @staticmethod
    def _apply_record_keys(
        document: OcrDocument,
        raw_structure: object,
        configured_paths: set[str],
    ) -> set[str]:
        if not isinstance(raw_structure, dict):
            return set()
        raw_sections = raw_structure.get("sections")
        raw_groups = raw_structure.get("groups")
        if not isinstance(raw_sections, list) or not isinstance(raw_groups, list):
            return set()
        collection_by_section = {
            section.get("id"): section.get("collectionPath")
            for section in raw_sections
            if isinstance(section, dict)
            and isinstance(section.get("id"), str)
            and section.get("collectionPath") in {"saleCalves", "calvings"}
        }
        added: set[str] = set()
        for raw_group in raw_groups:
            if not isinstance(raw_group, dict):
                continue
            section_id = raw_group.get("sectionId")
            collection = collection_by_section.get(section_id)
            order = raw_group.get("order")
            field = raw_group.get("recordKeyField")
            value = raw_group.get("recordKeyValue")
            group_id = raw_group.get("id")
            if (
                collection not in {"saleCalves", "calvings"}
                or not isinstance(order, int)
                or order < 1
                or field != "parity"
                or not isinstance(value, str)
                or not value.strip()
                or not isinstance(group_id, str)
            ):
                continue
            target_path = f"{collection}.{order - 1}.parity"
            records = (
                document.sale_calves
                if collection == "saleCalves"
                else document.calvings
            )
            if order > len(records):
                continue
            current = records[order - 1].parity
            if target_path in configured_paths:
                # A record key entered in the template is the identity of the
                # record. OpenAI still transcribes the parity crop, but can
                # mistake a sequence such as 2, 3, 4 for 1, 2, 3. Keep a
                # genuinely blank row blank; otherwise make the template key
                # authoritative while retaining the mapped cell as the source
                # so review previews continue to point at the parity crop.
                if current.raw is None and current.normalized is None:
                    continue
                records[order - 1].parity = current.model_copy(
                    update={
                        "raw": value.strip(),
                        "normalized": value.strip(),
                        "status": ReadingStatus.READABLE,
                        "alternatives": [],
                        "review_reason": None,
                    }
                )
                added.add(target_path)
                continue
            box = None
            if all(
                isinstance(raw_group.get(key), int)
                for key in ("x", "y", "width", "height")
            ):
                box = BoundingBox(
                    x=raw_group["x"],
                    y=raw_group["y"],
                    width=raw_group["width"],
                    height=raw_group["height"],
                )
            records[order - 1].parity = FieldReading(
                raw=value.strip(),
                normalized=value.strip(),
                status=ReadingStatus.READABLE,
                source_region_id=f"record:{group_id}",
                bounding_box=box,
            )
            added.add(target_path)
        return added

    @staticmethod
    def _mapped_cells_payload(
        extraction: ExtractionOutput,
        regions: list[OcrRegionInput],
        *,
        mapping_hash: object,
    ) -> dict[str, object]:
        mapping_by_id = {
            mapping.mapping_id: mapping
            for mapping in extraction.config.field_mappings
            if mapping.enabled
        }
        cells: list[dict[str, object]] = []
        for region in regions:
            template_mapping_id = region.source_region_id or region.region_id
            mapping = mapping_by_id[template_mapping_id]
            box = region.bounding_box
            cells.append(
                {
                    "mappingId": template_mapping_id,
                    "openAiMappingId": region.region_id,
                    "displayLabel": mapping.label,
                    "targetPath": region.target_path,
                    "boundingBox": (
                        {
                            "x": box[0],
                            "y": box[1],
                            "width": box[2],
                            "height": box[3],
                        }
                        if box
                        else None
                    ),
                    "alignedCellArtifact": (
                        f"fieldCrop.{template_mapping_id}."
                        + (
                            "handwritingOnly"
                            if region.printed_label_excluded
                            else "alignedColor"
                        )
                    ),
                    "handwritingHintArtifact": (
                        f"fieldCrop.{template_mapping_id}.handwritingOnly"
                    ),
                    "alignedCellSha256": hashlib.sha256(
                        region.image_png
                    ).hexdigest(),
                    "handwritingHintSha256": (
                        hashlib.sha256(region.handwriting_png).hexdigest()
                        if region.handwriting_png
                        else None
                    ),
                    "sentToOpenAi": True,
                    "printedLabelExcludedLocally": region.printed_label_excluded,
                }
            )
        return {
            "source": "TEMPLATE_VERSION_SNAPSHOT",
            "fieldMappingHash": mapping_hash,
            "mappedCellCount": len(cells),
            "openAiCropCount": len(cells),
            "labelCellsSentToOpenAi": False,
            "printedLabelExclusionRuleCount": sum(
                region.printed_label_excluded for region in regions
            ),
            "wholeDocumentSentToOpenAi": False,
            "targetPathsSentToOpenAi": False,
            "paddleGatingEnabled": False,
            "cells": cells,
        }

    @staticmethod
    def _value_at(document: dict[str, object], dotted: str) -> object:
        value: object = document
        for key in dotted.split("."):
            if isinstance(value, list) and key.isdigit():
                index = int(key)
                if index >= len(value):
                    return None
                value = value[index]
            elif isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value
