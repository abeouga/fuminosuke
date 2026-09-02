from __future__ import annotations

import asyncio
import hashlib
import logging

from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import Database
from app.domain.generic_data import build_generic_data, build_generic_values
from app.domain.numeric_values import normalize_document_numbers
from app.domain.ocr_factory import blank_ocr_document
from app.domain.ocr_models import BoundingBox, FieldReading, OcrDocument, ReadingStatus
from app.domain.sale_notes import SALE_NOTE_SEX_SYMBOLS
from app.domain.template_candidates import normalize_candidate_target
from app.domain.template_config import FieldMappingConfig
from app.extraction.region_cropper import RegionCrops
from app.image_processing.artifacts import ArtifactWriter, encode_png
from app.ocr.base import (
    OcrProvider,
    OcrProviderRequest,
    OcrProviderResponse,
    OcrRegionInput,
)
from app.ocr.deterministic_mapper import assign_mapped_reading, get_mapped_reading
from app.ocr.disabled_provider import DisabledOcrProvider
from app.ocr.mock_provider import MockOcrProvider
from app.ocr.openai_provider import OpenAIOcrProvider
from app.ocr.text_presence import PaddleTextPresenceGate
from app.repositories.job_repository import JobRepository
from app.repositories.ocr_result_repository import OcrResultRepository
from app.services.document_reconciliation_service import DocumentReconciliationService
from app.services.extraction_service import ExtractionOutput
from app.services.google_sheets_sync_service import GoogleSheetsSyncService
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
        google_sheets: GoogleSheetsSyncService,
    ) -> None:
        self._settings = settings
        self._database = database
        self._writer = ArtifactWriter(database, storage)
        self._validator = DeterministicValidator()
        self._presence_gate = PaddleTextPresenceGate()
        self._documents = DocumentReconciliationService(self._validator)

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
        sale_note_symbols = (
            tuple(
                value
                for value in job_snapshot.get(
                    "saleNoteSexSymbolPool",
                    SALE_NOTE_SEX_SYMBOLS,
                )
                if value in SALE_NOTE_SEX_SYMBOLS
            )
            or SALE_NOTE_SEX_SYMBOLS
        )
        managed_pool_by_target: dict[
            str,
            tuple[tuple[str, ...], tuple[str, ...]],
        ] = {}
        raw_managed_pools = job_snapshot.get("templateCandidatePools", [])
        if isinstance(raw_managed_pools, list):
            for raw_pool in raw_managed_pools:
                if not isinstance(raw_pool, dict):
                    continue
                target_pattern = raw_pool.get("targetPattern")
                raw_candidates = raw_pool.get("candidates")
                raw_suffixes = raw_pool.get("allowedSuffixes", [])
                if not isinstance(target_pattern, str) or not isinstance(raw_candidates, list):
                    continue
                candidates = tuple(
                    value for value in raw_candidates if isinstance(value, str) and value
                )
                suffix_source = raw_suffixes if isinstance(raw_suffixes, list) else []
                suffixes = tuple(
                    value
                    for value in suffix_source
                    if isinstance(value, str) and value
                )
                if candidates:
                    managed_pool_by_target[target_pattern] = (candidates, suffixes)
        region_configs = (
            extraction.config.mapped_ocr_regions()
            if provider_name == "openai"
            else extraction.config.ocr_regions()
        )
        mapping_by_id = {
            mapping.mapping_id: mapping
            for mapping in extraction.config.field_mappings
            if mapping.enabled
        }
        mark_mappings = [
            mapping
            for mapping in extraction.config.field_mappings
            if mapping.enabled and mapping.mapping_type == "mark"
        ]
        mark_state_by_id = {
            mapping.mapping_id: self._mark_state(mapping, extraction.marks)
            for mapping in mark_mappings
        }
        fixed_calving_keys = (
            self._fixed_calving_record_keys(job_snapshot.get("recordStructure"))
            if provider_name == "openai"
            else {}
        )
        trailing_blank_rows = (
            self._trailing_blank_rows(extraction, mark_state_by_id)
            if provider_name == "openai"
            else set()
        )
        openai_mark_ids: set[str] = set()
        all_regions: list[OcrRegionInput] = []
        preview_text_regions: list[OcrRegionInput] = []
        fixed_record_regions: list[OcrRegionInput] = []
        local_resolution_by_id: dict[str, str] = {}
        document_identity_fields = extraction.config.effective_document_identity_fields()
        for region in region_configs:
            mapping_config = mapping_by_id.get(region.region_id)
            if mapping_config is not None and mapping_config.mapping_type == "mark":
                continue
            if region.region_id not in extraction.crops:
                continue
            target_path = region.expected_fields[0] if len(region.expected_fields) == 1 else None
            collection_row = self._collection_row(target_path)
            fixed_record_key = bool(target_path and target_path in fixed_calving_keys)
            trailing_blank = collection_row in trailing_blank_rows
            sent_to_openai = provider_name != "openai" or not (fixed_record_key or trailing_blank)
            # Preserve the stable template mapping ID. Renumbering the filtered
            # subset as cell_0001..N lets a model-inserted extra list item shift
            # every later transcription onto the wrong rectangle.
            openai_mapping_id = region.region_id
            is_sire_name = bool(
                target_path
                and target_path.startswith("saleCalves.")
                and target_path.endswith(".sireName")
            )
            is_sale_note = bool(
                target_path
                and target_path.startswith("saleCalves.")
                and target_path.endswith(".notes")
            )
            managed_pool = (
                managed_pool_by_target.get(normalize_candidate_target(target_path))
                if target_path
                else None
            )
            transcription_format = self._transcription_format(target_path)
            identity_for_region = next(
                (
                    identity_field
                    for identity_field in document_identity_fields
                    if identity_field.mapping_id == region.region_id
                    and identity_field.target_path == target_path
                ),
                None,
            )
            is_document_identity = identity_for_region is not None
            crop = extraction.crops[region.region_id]
            printed_label_excluded = bool(region.printed_label_text.strip())
            ocr_region = OcrRegionInput(
                region_id=openai_mapping_id,
                image_png=encode_png(
                    crop.handwriting_only if printed_label_excluded else crop.aligned_color
                ),
                handwriting_png=(
                    None if printed_label_excluded else encode_png(crop.handwriting_only)
                ),
                target_path=target_path,
                source_region_id=(region.region_id if provider_name == "openai" else None),
                bounding_box=extraction.crops[region.region_id].bbox,
                candidates=(
                    managed_pool[0]
                    if managed_pool is not None
                    else sire_name_pool
                    if is_sire_name
                    else sale_note_symbols
                    if is_sale_note
                    else ()
                ),
                allowed_suffixes=(
                    managed_pool[1]
                    if managed_pool is not None
                    else sire_name_suffixes
                    if is_sire_name
                    else ()
                ),
                candidate_type=(
                    "managed_text"
                    if managed_pool is not None
                    else "sire_name"
                    if is_sire_name
                    else "sale_calf_note_prefix"
                    if is_sale_note
                    else None
                ),
                transcription_format=transcription_format,
                printed_label_excluded=printed_label_excluded,
                printed_label_text=region.printed_label_text.strip(),
                is_document_identity=is_document_identity,
                identity_value_type=(
                    identity_for_region.value_type
                    if identity_for_region is not None
                    else None
                ),
                identity_exact_length=(
                    identity_for_region.exact_length
                    if identity_for_region is not None
                    else None
                ),
            )
            preview_text_regions.append(ocr_region)
            if sent_to_openai:
                all_regions.append(ocr_region)
            elif fixed_record_key:
                fixed_record_regions.append(ocr_region)
                local_resolution_by_id[region.region_id] = "fixed_record_key"
            else:
                local_resolution_by_id[region.region_id] = "trailing_blank_row"
        if provider_name == "openai":
            for mapping in mark_mappings:
                state = mark_state_by_id[mapping.mapping_id]
                mark_crop = extraction.crops.get(mapping.mapping_id)
                if mapping.mark_recognition_mode == "manual":
                    local_resolution_by_id[mapping.mapping_id] = "manual_choice_required"
                    continue
                if state[0] != ReadingStatus.UNCERTAIN or mark_crop is None:
                    local_resolution_by_id[mapping.mapping_id] = "mark_detector"
                    continue
                openai_mark_ids.add(mapping.mapping_id)
                all_regions.append(self._mark_region_input(mapping, mark_crop))
        mapped_cells_payload: dict[str, object] | None = None
        if provider_name == "openai":
            if (not all_regions and not mark_mappings) or any(
                not region.target_path for region in all_regions
            ):
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
            if self._settings.ocr.paddle_presence_audit_enabled and self._presence_gate.available:
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
                        "detectedTextCount": sum(item.detected for item in presence_by_id.values()),
                        "openAiCropCount": len(all_regions),
                        "regions": [
                            presence_by_id[region.region_id].to_dict(region_id=region.region_id)
                            for region in all_regions
                        ],
                    },
                )
            sent_mapping_ids = {
                region.source_region_id or region.region_id for region in all_regions
            }
            preview_regions = list(preview_text_regions)
            preview_mapping_ids = {
                region.source_region_id or region.region_id for region in preview_regions
            }
            preview_regions.extend(
                region
                for region in all_regions
                if (region.source_region_id or region.region_id) not in preview_mapping_ids
            )
            for mapping in mark_mappings:
                if mapping.mapping_id in sent_mapping_ids:
                    continue
                preview_crop = extraction.crops.get(mapping.mapping_id)
                if preview_crop is None:
                    continue
                preview_regions.append(self._mark_region_input(mapping, preview_crop))
            mapped_cells_payload = self._mapped_cells_payload(
                extraction,
                preview_regions,
                mapping_hash=job_snapshot.get("fieldMappingHash"),
                sent_mapping_ids=sent_mapping_ids,
                local_resolution_by_id=local_resolution_by_id,
                trailing_blank_rows=trailing_blank_rows,
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
            full_document_png=(
                encode_png(extraction.aligned_rgb)
                if provider_name == "openai"
                and job_snapshot.get("sendFullDocumentToOpenAi") is True
                else None
            ),
            template_id=extraction.config.template_id,
            template_version=str(extraction.config.template_version),
            should_cancel=lambda: self._cancel_requested(extraction.job_id),
        )
        if self._cancel_requested(extraction.job_id):
            return
        self._progress(extraction.job_id, "CALLING_OCR", 78)
        response = (
            OcrProviderResponse(
                document=blank_ocr_document(
                    provider="local-mark-detector",
                    source_regions=[mapping.mapping_id for mapping in mark_mappings],
                ),
                provider="local-mark-detector",
                model_id=None,
                prompt_version=None,
                api_call_count=0,
            )
            if provider_name == "openai" and not all_regions
            else asyncio.run(provider.extract(request))
        )
        openai_mapping_diagnostics: dict[str, object] | None = None
        if response.provider == "openai" and response.diagnostics:
            openai_mapping_diagnostics = {
                "schemaVersion": 1,
                "jobId": extraction.job_id,
                "attemptNumber": extraction.attempt_number,
                "calls": list(response.diagnostics),
            }
        if response.provider == "openai":
            total_tokens = (
                response.input_tokens + response.output_tokens
                if response.input_tokens is not None and response.output_tokens is not None
                else None
            )
            with self._database.session() as session:
                JobRepository(session).add_event(
                    job_id=extraction.job_id,
                    level="INFO",
                    event_code="OPENAI_API_USAGE",
                    stage="CALLING_OCR",
                    metadata={
                        "attemptNumber": extraction.attempt_number,
                        "modelId": response.model_id,
                        "requestId": response.request_id,
                        "apiCallCount": response.api_call_count,
                        "inputTokens": response.input_tokens,
                        "outputTokens": response.output_tokens,
                        "totalTokens": total_tokens,
                        "estimatedCost": (
                            str(response.estimated_cost)
                            if response.estimated_cost is not None
                            else None
                        ),
                        "durationMs": response.duration_ms,
                    },
                )
        if provider_name == "openai":
            self._flag_local_ink_conflicts(response.document, all_regions, extraction)
        response.document.template_id = request.template_id
        response.document.template_version = request.template_version
        for fixed_region in fixed_record_regions:
            target_path = fixed_region.target_path
            if target_path is None:
                continue
            value = fixed_calving_keys[target_path]
            box = fixed_region.bounding_box
            assign_mapped_reading(
                response.document,
                target_path,
                FieldReading(
                    raw=value,
                    normalized=value,
                    status=ReadingStatus.READABLE,
                    alternatives=[],
                    review_reason=None,
                    source_region_id=(fixed_region.source_region_id or fixed_region.region_id),
                    bounding_box=self._bounding_box(box),
                ),
            )
        for mapping in mark_mappings:
            if mapping.mapping_id in openai_mark_ids:
                continue
            status, raw, review_reason = mark_state_by_id[mapping.mapping_id]
            assign_mapped_reading(
                response.document,
                mapping.target_path,
                FieldReading(
                    raw=raw,
                    normalized=raw if status is ReadingStatus.READABLE else None,
                    status=status,
                    alternatives=[],
                    review_reason=review_reason,
                    source_region_id=mapping.mapping_id,
                    bounding_box=self._mark_bounding_box(
                        mapping,
                        extraction.crops.get(mapping.mapping_id),
                    ),
                ),
            )
        response.document.generic_data = build_generic_data(
            response.document,
            job_snapshot.get("genericTemplateDefinition"),
        )
        response.document.generic_values = build_generic_values(response.document.generic_data)
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
        configured_paths = {region.target_path for region in all_regions if region.target_path}
        configured_paths.update(fixed_calving_keys)
        configured_paths.update(mapping.target_path for mapping in mark_mappings)
        configured_paths.update(
            subfield.target_path
            for mapping in extraction.config.field_mappings
            for subfield in mapping.composite_subfields
        )
        configured_paths.update(
            self._apply_record_keys(
                response.document,
                job_snapshot.get("recordStructure"),
                configured_paths,
            )
        )
        normalized_document = normalize_document_numbers(response.document)
        report = self._validator.validate(
            normalized_document,
            image_quality=extraction.quality,
            configured_paths=configured_paths,
        )
        response_document = self._validator.merge_review_items(normalized_document, report)
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
        if openai_mapping_diagnostics is not None:
            mapped_responses: list[dict[str, object]] = []
            if mapped_cells_payload is not None:
                cells = mapped_cells_payload.get("cells")
                if isinstance(cells, list):
                    mapped_responses = [
                        {
                            "mappingId": cell.get("mappingId"),
                            "openAiMappingId": cell.get("openAiMappingId"),
                            "targetPath": cell.get("targetPath"),
                            "response": cell.get("response"),
                        }
                        for cell in cells
                        if isinstance(cell, dict) and cell.get("sentToOpenAi") is True
                    ]
            openai_mapping_diagnostics["mappedCellsResponses"] = mapped_responses
            self._writer.json(
                job_id=extraction.job_id,
                attempt_number=extraction.attempt_number,
                name="openAiMappingDiagnostics",
                file_name="openai_mapping_diagnostics.json",
                value=openai_mapping_diagnostics,
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
            diagnostics=response.diagnostics,
        )
        with self._database.session() as session:
            stored = OcrResultRepository(session).save_provider_result(
                job_id=extraction.job_id,
                response=response,
                report=report,
            )
            current_job = JobRepository(session).get(extraction.job_id)
            explicit_action = (
                current_job.config_snapshot_json.get("documentAction")
                if current_job is not None
                else None
            )
            if current_job is not None and explicit_action in {
                "ADD_PAGE",
                "RESCAN_PAGE",
                "NEW_DOCUMENT",
            }:
                stored = self._documents.resolve(
                    session,
                    job=current_job,
                    stored=stored,
                    action=explicit_action,
                    target_document_id=current_job.config_snapshot_json.get("targetDocumentId"),
                    target_job_id=None,
                    target_page_id=current_job.config_snapshot_json.get("targetPageId"),
                    configured_paths=configured_paths,
                )
                session.commit()
            revision = stored.revision
        # A spreadsheet file belongs to a confirmed ledger document. Draft OCR
        # results remain in the database so an abandoned review cannot create
        # an orphan file in Google Drive.
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

    @staticmethod
    def _fixed_calving_record_keys(raw_structure: object) -> dict[str, str]:
        if not isinstance(raw_structure, dict):
            return {}
        raw_sections = raw_structure.get("sections")
        raw_groups = raw_structure.get("groups")
        if not isinstance(raw_sections, list) or not isinstance(raw_groups, list):
            return {}
        calving_sections = {
            section.get("id")
            for section in raw_sections
            if isinstance(section, dict)
            and isinstance(section.get("id"), str)
            and section.get("collectionPath") == "calvings"
        }
        result: dict[str, str] = {}
        for group in raw_groups:
            if not isinstance(group, dict) or group.get("sectionId") not in calving_sections:
                continue
            order = group.get("order")
            value = group.get("recordKeyValue")
            if (
                isinstance(order, int)
                and order >= 1
                and group.get("recordKeyField") == "parity"
                and isinstance(value, str)
                and value.strip()
            ):
                result[f"calvings.{order - 1}.parity"] = value.strip()
        return result

    @staticmethod
    def _collection_row(target_path: str | None) -> tuple[str, int] | None:
        if target_path is None:
            return None
        parts = target_path.split(".")
        if len(parts) == 3 and parts[0] in {"saleCalves", "calvings"} and parts[1].isdigit():
            return parts[0], int(parts[1])
        return None

    @staticmethod
    def _flag_local_ink_conflicts(
        document: OcrDocument,
        regions: list[OcrRegionInput],
        extraction: ExtractionOutput,
    ) -> None:
        for region in regions:
            if region.target_path is None:
                continue
            crop = extraction.crops.get(region.source_region_id or region.region_id)
            if crop is None or crop.handwriting_only.size == 0:
                continue
            dark_pixels = int((crop.handwriting_only.min(axis=2) < 235).sum())
            if dark_pixels < 4:
                continue
            reading = get_mapped_reading(document, region.target_path)
            if reading is None or reading.status is not ReadingStatus.BLANK:
                continue
            assign_mapped_reading(
                document,
                region.target_path,
                reading.model_copy(
                    update={
                        "status": ReadingStatus.UNCERTAIN,
                        "review_reason": "LOCAL_INK_OCR_BLANK",
                    }
                ),
            )

    @staticmethod
    def _transcription_format(target_path: str | None) -> str | None:
        is_era_date = target_path == "document.birthDate" or bool(
            target_path
            and (
                (target_path.startswith("calvings.") and target_path.endswith(".calvingDate"))
                or (target_path.startswith("saleCalves.") and target_path.endswith(".saleDate"))
            )
        )
        return "japanese_era_date_numbers" if is_era_date else None

    @classmethod
    def _trailing_blank_rows(
        cls,
        extraction: ExtractionOutput,
        mark_state_by_id: dict[
            str,
            tuple[ReadingStatus, str | None, str | None],
        ],
    ) -> set[tuple[str, int]]:
        """Skip only rows after the last row with conservative local evidence.

        Individual empty-cell gating is intentionally avoided: faint ditto dots
        and pencil writing can disappear from the handwriting-only derivative.
        A row is considered active when any non-parity text crop contains at
        least a few dark pixels, or a local mark result is non-blank. If a whole
        collection has no active row, nothing is skipped.
        """

        rows_by_collection: dict[str, set[int]] = {}
        active_by_collection: dict[str, set[int]] = {}
        for mapping in extraction.config.field_mappings:
            if not mapping.enabled:
                continue
            collection_row = cls._collection_row(mapping.target_path)
            if collection_row is None:
                continue
            collection, row_index = collection_row
            rows_by_collection.setdefault(collection, set()).add(row_index)
            if mapping.mapping_type == "mark":
                if mapping.mark_recognition_mode == "manual":
                    crop = extraction.crops.get(mapping.mapping_id)
                    if crop is not None and crop.handwriting_only.size > 0:
                        dark_pixels = int((crop.handwriting_only.min(axis=2) < 235).sum())
                        if dark_pixels >= 4:
                            active_by_collection.setdefault(collection, set()).add(row_index)
                    continue
                state = mark_state_by_id.get(mapping.mapping_id)
                if state is not None and state[0] is not ReadingStatus.BLANK:
                    active_by_collection.setdefault(collection, set()).add(row_index)
                continue
            if mapping.target_path.endswith(".parity"):
                continue
            crop = extraction.crops.get(mapping.mapping_id)
            if crop is None or crop.handwriting_only.size == 0:
                continue
            dark_pixels = int((crop.handwriting_only.min(axis=2) < 235).sum())
            if dark_pixels >= 4:
                active_by_collection.setdefault(collection, set()).add(row_index)

        trailing: set[tuple[str, int]] = set()
        for collection, rows in rows_by_collection.items():
            active_rows = active_by_collection.get(collection)
            if not active_rows:
                continue
            last_active = max(active_rows)
            trailing.update(
                (collection, row_index) for row_index in rows if row_index > last_active
            )
        return trailing

    @staticmethod
    def _mark_state(
        mapping: FieldMappingConfig,
        results: list[dict[str, object]],
    ) -> tuple[ReadingStatus, str | None, str | None]:
        if mapping.mark_recognition_mode == "manual":
            return ReadingStatus.UNCERTAIN, None, "MANUAL_CHOICE_REQUIRED"
        mapping_id = mapping.mapping_id
        options = mapping.mark_options
        selection_mode = mapping.mark_selection_mode
        result_by_value = {
            item.get("optionValue"): item
            for item in results
            if item.get("mappingId") == mapping_id and isinstance(item.get("optionValue"), str)
        }
        detected_labels: list[str] = []
        uncertain = False
        for option in options:
            result = result_by_value.get(option.value)
            if result is None or result.get("status") == "uncertain":
                uncertain = True
            elif result.get("status") == "detected":
                detected_labels.append(option.label)
        if selection_mode == "single" and len(detected_labels) > 1:
            uncertain = True
        if uncertain:
            return ReadingStatus.UNCERTAIN, None, "MARK_CHOICE_UNRESOLVED"
        if not detected_labels:
            return ReadingStatus.BLANK, None, None
        value = "・".join(detected_labels)
        return ReadingStatus.READABLE, value, None

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
            records = document.sale_calves if collection == "saleCalves" else document.calvings
            if order > len(records):
                continue
            current = records[order - 1].parity
            if target_path in configured_paths:
                # A record key entered in the template is the identity of the
                # record. OpenAI still transcribes the parity crop, but can
                # mistake a sequence such as 2, 3, 4 for 1, 2, 3. Keep a
                # genuinely blank sale row blank; otherwise make the template
                # key authoritative while retaining the mapped cell as the
                # source so review previews continue to point at the parity
                # crop. Calving-history parity is a printed, fixed row number
                # in the ledger, so it must be materialized even when OCR
                # returns the printed-number crop as blank.
                if collection != "calvings" and current.raw is None and current.normalized is None:
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
            if all(isinstance(raw_group.get(key), int) for key in ("x", "y", "width", "height")):
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
    def _mark_region_input(
        mapping: FieldMappingConfig,
        crop: RegionCrops,
    ) -> OcrRegionInput:
        """Build OCR and review metadata from the same canonical mark crop."""

        return OcrRegionInput(
            region_id=mapping.mapping_id,
            image_png=encode_png(crop.aligned_color),
            handwriting_png=encode_png(crop.handwriting_only),
            target_path=mapping.target_path,
            source_region_id=mapping.mapping_id,
            bounding_box=crop.bbox,
            candidates=tuple(option.label for option in mapping.mark_options),
            candidate_type="mark_choice",
        )

    @staticmethod
    def _bounding_box(box: tuple[int, int, int, int] | None) -> BoundingBox | None:
        if box is None:
            return None
        return BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3])

    @classmethod
    def _mark_bounding_box(
        cls,
        mapping: FieldMappingConfig,
        crop: RegionCrops | None,
    ) -> BoundingBox:
        """Use the actual canonical crop, including configured padding, for review."""

        box = crop.bbox if crop is not None else (
            mapping.x,
            mapping.y,
            mapping.width,
            mapping.height,
        )
        bounding_box = cls._bounding_box(box)
        assert bounding_box is not None
        return bounding_box

    @staticmethod
    def _mapped_cells_payload(
        extraction: ExtractionOutput,
        regions: list[OcrRegionInput],
        *,
        mapping_hash: object,
        sent_mapping_ids: set[str] | None = None,
        local_resolution_by_id: dict[str, str] | None = None,
        trailing_blank_rows: set[tuple[str, int]] | None = None,
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
                    "mappingType": mapping.mapping_type,
                    "markRecognitionMode": mapping.mark_recognition_mode,
                    "markSelectionMode": mapping.mark_selection_mode,
                    "markOptions": [
                        {"label": option.label, "value": option.value}
                        for option in mapping.mark_options
                    ],
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
                        + ("handwritingOnly" if region.printed_label_excluded else "alignedColor")
                    ),
                    "handwritingHintArtifact": (f"fieldCrop.{template_mapping_id}.handwritingOnly"),
                    "alignedCellSha256": hashlib.sha256(region.image_png).hexdigest(),
                    "handwritingHintSha256": (
                        hashlib.sha256(region.handwriting_png).hexdigest()
                        if region.handwriting_png
                        else None
                    ),
                    "sentToOpenAi": (
                        template_mapping_id in sent_mapping_ids
                        if sent_mapping_ids is not None
                        else True
                    ),
                    "localResolution": (
                        local_resolution_by_id.get(template_mapping_id)
                        if local_resolution_by_id is not None
                        else None
                    ),
                    "printedLabelExcludedLocally": region.printed_label_excluded,
                }
            )
        sent_ids = (
            sent_mapping_ids
            if sent_mapping_ids is not None
            else {region.source_region_id or region.region_id for region in regions}
        )
        sent_regions = [
            region
            for region in regions
            if (region.source_region_id or region.region_id) in sent_ids
        ]
        return {
            "source": "TEMPLATE_VERSION_SNAPSHOT",
            "fieldMappingHash": mapping_hash,
            "mappedCellCount": len(cells),
            "openAiCropCount": len(sent_regions),
            "openAiImageCount": sum(
                1 + int(region.handwriting_png is not None) for region in sent_regions
            ),
            "locallyResolvedCellCount": len(cells) - len(sent_regions),
            "labelCellsSentToOpenAi": False,
            "printedLabelExclusionRuleCount": sum(
                region.printed_label_excluded for region in regions
            ),
            "wholeDocumentSentToOpenAi": False,
            "targetPathsSentToOpenAi": False,
            "paddleGatingEnabled": False,
            "trailingBlankRowGatingEnabled": True,
            "trailingBlankRows": [
                {"collection": collection, "rowIndex": row_index}
                for collection, row_index in sorted(trailing_blank_rows or set())
            ],
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
