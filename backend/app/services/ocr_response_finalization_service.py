from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.generic_data import build_generic_data, build_generic_values
from app.domain.ocr_models import BoundingBox, FieldReading, OcrDocument, ReadingStatus
from app.domain.template_config import FieldMappingConfig
from app.extraction.region_cropper import RegionCrops
from app.ocr.base import OcrProviderResponse, OcrRegionInput
from app.ocr.deterministic_mapper import assign_mapped_reading, get_mapped_reading
from app.ocr.sequence_numbers import apply_sequence_numbers
from app.services.extraction_service import ExtractionOutput
from app.services.ocr_request_preparation_service import PreparedOcrRequest
from app.validation.business_validators import DeterministicValidator, ValidationReport


@dataclass(frozen=True, slots=True)
class FinalizedOcrComputation:
    response: OcrProviderResponse
    validation_report: ValidationReport
    mapped_cells_payload: dict[str, object] | None
    diagnostics_payload: dict[str, object] | None
    configured_paths: set[str]


class OcrResponseFinalizer:
    """Computes the persistence-ready OCR result without external side effects."""

    def __init__(self, validator: DeterministicValidator) -> None:
        self._validator = validator

    def finalize(
        self,
        *,
        prepared: PreparedOcrRequest,
        provider_response: OcrProviderResponse,
        extraction: ExtractionOutput,
        job_snapshot: Mapping[str, object],
        provider_name: str,
        carried: Mapping[str, tuple[str, FieldReading]] | None = None,
    ) -> FinalizedOcrComputation:
        request = prepared.request
        all_regions = request.regions
        mapped_cells_payload = prepared.mapped_cells_payload
        openai_mapping_diagnostics: dict[str, object] | None = None
        if provider_response.provider == "openai" and provider_response.diagnostics:
            openai_mapping_diagnostics = {
                "schemaVersion": 1,
                "jobId": extraction.job_id,
                "attemptNumber": extraction.attempt_number,
                "calls": list(provider_response.diagnostics),
            }
        if provider_name == "openai":
            self._flag_local_ink_conflicts(
                provider_response.document,
                all_regions,
                extraction,
            )
        for carried_path, carried_reading in (carried or {}).values():
            # Kept confirmed readings of an update (series_carry); assigned before labels,
            # validation and mappedCells so they are treated like every other field.
            assign_mapped_reading(provider_response.document, carried_path, carried_reading)
            source_regions = provider_response.document.processing_metadata.source_regions
            if carried_reading.source_region_id not in source_regions:
                source_regions.append(carried_reading.source_region_id)
        provider_response.document.template_id = request.template_id
        provider_response.document.template_version = request.template_version
        provider_response.document.field_labels = {
            mapping.target_path.removeprefix("fields."): mapping.label
            for mapping in extraction.config.field_mappings
            if mapping.enabled
            and mapping.target_path.removeprefix("fields.") in provider_response.document.fields
        }
        for mapping in prepared.mark_mappings:
            if carried and mapping.mapping_id in carried:
                continue
            if mapping.mapping_id in prepared.openai_mark_ids:
                reading = get_mapped_reading(provider_response.document, mapping.target_path)
                if (
                    mapping.mark_selection_mode == "single"
                    and reading is not None
                    and reading.status is ReadingStatus.READABLE
                    and reading.normalized not in {option.label for option in mapping.mark_options}
                ):
                    # Local detection already enforces this constraint. A provider
                    # result must not turn a conflicting single choice into a fact.
                    assign_mapped_reading(
                        provider_response.document,
                        mapping.target_path,
                        reading.model_copy(
                            update={
                                "normalized": None,
                                "status": ReadingStatus.UNCERTAIN,
                                "review_reason": "MARK_CHOICE_UNRESOLVED",
                            }
                        ),
                    )
                continue
            status, raw, review_reason = prepared.mark_state_by_id[mapping.mapping_id]
            assign_mapped_reading(
                provider_response.document,
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
        from app.domain.checklist import apply_write_ins, flag_symbol_write_ins

        dumped_mappings = [
            mapping.model_dump(mode="json", by_alias=True)
            for mapping in extraction.config.field_mappings
        ]
        apply_write_ins(provider_response.document, dumped_mappings)
        flag_symbol_write_ins(provider_response.document, dumped_mappings)
        apply_sequence_numbers(
            provider_response.document,
            prepared.sequence_regions,
            extraction.crops,
        )
        provider_response.document.generic_data = build_generic_data(
            provider_response.document,
            job_snapshot.get("genericTemplateDefinition"),
        )
        provider_response.document.generic_values = build_generic_values(
            provider_response.document.generic_data
        )
        provider_response.document.processing_metadata.model_id = provider_response.model_id
        provider_response.document.processing_metadata.prompt_version = (
            provider_response.prompt_version
        )
        configured_paths = {region.target_path for region in all_regions if region.target_path}
        configured_paths.update(mapping.target_path for mapping in prepared.mark_mappings)
        configured_paths.update(
            path for region in prepared.sequence_regions for path in region.expected_fields
        )
        configured_paths.update(
            subfield.target_path
            for mapping in extraction.config.field_mappings
            for subfield in mapping.composite_subfields
        )
        configured_paths.update(path for path, _ in (carried or {}).values())
        normalized_document = provider_response.document
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
            mapped_cells_payload["provider"] = provider_response.provider
            mapped_cells_payload["modelId"] = provider_response.model_id
            mapped_cells_payload["apiCallCount"] = provider_response.api_call_count
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
        response = provider_response.__class__(
            document=response_document,
            provider=provider_response.provider,
            model_id=provider_response.model_id,
            prompt_version=provider_response.prompt_version,
            api_call_count=provider_response.api_call_count,
            input_tokens=provider_response.input_tokens,
            output_tokens=provider_response.output_tokens,
            estimated_cost=provider_response.estimated_cost,
            request_id=provider_response.request_id,
            duration_ms=provider_response.duration_ms,
            diagnostics=provider_response.diagnostics,
        )
        return FinalizedOcrComputation(
            response=response,
            validation_report=report,
            mapped_cells_payload=mapped_cells_payload,
            diagnostics_payload=openai_mapping_diagnostics,
            configured_paths=configured_paths,
        )

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

        box = (
            crop.bbox
            if crop is not None
            else (
                mapping.x,
                mapping.y,
                mapping.width,
                mapping.height,
            )
        )
        bounding_box = cls._bounding_box(box)
        assert bounding_box is not None
        return bounding_box

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
