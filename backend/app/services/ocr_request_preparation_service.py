from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.core.errors import AppError
from app.domain.ocr_models import ReadingStatus
from app.domain.paged_document_metadata import request_identity_fields
from app.domain.template_candidates import normalize_candidate_target
from app.domain.template_config import FieldMappingConfig, RegionConfig
from app.extraction.region_cropper import RegionCrops
from app.image_processing.artifacts import encode_png
from app.ocr.base import OcrProviderRequest, OcrRegionInput
from app.services.extraction_service import ExtractionOutput

MarkState = tuple[ReadingStatus, str | None, str | None]


@dataclass(frozen=True, slots=True)
class PreparedOcrRequest:
    request: OcrProviderRequest
    mapped_cells_payload: dict[str, object] | None
    mark_mappings: list[FieldMappingConfig]
    mark_state_by_id: dict[str, MarkState]
    openai_mark_ids: set[str]
    sequence_regions: list[RegionConfig]


class OcrRequestPreparer:
    """Builds the provider request and its identity-preserving local context."""

    def prepare(
        self,
        *,
        extraction: ExtractionOutput,
        job_snapshot: Mapping[str, object],
        provider_name: str,
        should_cancel: Callable[[], bool] | None,
    ) -> PreparedOcrRequest:
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
                    value for value in suffix_source if isinstance(value, str) and value
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
        openai_mark_ids: set[str] = set()
        sequence_regions: list[RegionConfig] = []
        all_regions: list[OcrRegionInput] = []
        preview_text_regions: list[OcrRegionInput] = []
        local_resolution_by_id: dict[str, str] = {}
        document_identity_fields = request_identity_fields(extraction.config, job_snapshot)
        for region in region_configs:
            mapping_config = mapping_by_id.get(region.region_id)
            if mapping_config is not None and mapping_config.mapping_type == "mark":
                continue
            if region.region_id not in extraction.crops:
                raise AppError(
                    status=409,
                    code="OCR_CROP_BINDING_MISSING",
                    title="Configured crop is missing",
                    detail="設定した読み取り欄の画像がありません。対応関係を確認してください。",
                )
            target_path = region.expected_fields[0] if len(region.expected_fields) == 1 else None
            sent_to_openai = region.sequence_number is None
            # Keep the stable template mapping ID. Renumbering the filtered
            # subset would allow one inserted provider item to shift every
            # later transcription onto a different physical cell.
            openai_mapping_id = region.region_id
            managed_pool = (
                managed_pool_by_target.get(normalize_candidate_target(target_path))
                if target_path
                else None
            )
            transcription_format = (
                "printed_or_handwritten_number" if region.reading_mode == "number" else None
            )
            identity_for_region = next(
                (
                    identity_field
                    for identity_field in document_identity_fields
                    if identity_field.mapping_id == region.region_id
                    and identity_field.target_path == target_path
                ),
                None,
            )
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
                bounding_box=crop.bbox,
                candidates=managed_pool[0] if managed_pool is not None else (),
                allowed_suffixes=managed_pool[1] if managed_pool is not None else (),
                candidate_type="managed_text" if managed_pool is not None else None,
                transcription_format=transcription_format,
                printed_label_excluded=printed_label_excluded,
                printed_label_text=region.printed_label_text.strip(),
                is_document_identity=identity_for_region is not None,
                identity_value_type=(
                    identity_for_region.value_type if identity_for_region is not None else None
                ),
                identity_exact_length=(
                    identity_for_region.exact_length if identity_for_region is not None else None
                ),
            )
            preview_text_regions.append(ocr_region)
            if sent_to_openai:
                all_regions.append(ocr_region)
            elif region.sequence_number is not None:
                sequence_regions.append(region)
                local_resolution_by_id[region.region_id] = "automatic_sequence_number"

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
            if (not all_regions and not mark_mappings and not sequence_regions) or any(
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
            )

        request = OcrProviderRequest(
            regions=all_regions,
            full_document_png=(
                encode_png(extraction.aligned_rgb)
                if provider_name == "openai"
                and job_snapshot.get("sendFullDocumentToOpenAi") is True
                and not sequence_regions
                else None
            ),
            template_id=extraction.config.template_id,
            template_version=str(extraction.config.template_version),
            should_cancel=should_cancel,
        )
        return PreparedOcrRequest(
            request=request,
            mapped_cells_payload=mapped_cells_payload,
            mark_mappings=mark_mappings,
            mark_state_by_id=mark_state_by_id,
            openai_mark_ids=openai_mark_ids,
            sequence_regions=sequence_regions,
        )

    @staticmethod
    def _mark_state(
        mapping: FieldMappingConfig,
        results: list[dict[str, object]],
    ) -> MarkState:
        if mapping.mark_recognition_mode == "manual":
            return ReadingStatus.UNCERTAIN, None, "MANUAL_CHOICE_REQUIRED"
        if mapping.mark_recognition_mode in {"symbol", "checklist"}:
            # Visual symbols/checklists are read from the whole mapped crop.
            # Even a blank crop needs a visual reading; never infer it from marks.
            return ReadingStatus.UNCERTAIN, None, "MARK_CHOICE_UNRESOLVED"
        result_by_value = {
            item.get("optionValue"): item
            for item in results
            if item.get("mappingId") == mapping.mapping_id
            and isinstance(item.get("optionValue"), str)
        }
        detected_labels: list[str] = []
        uncertain = False
        for option in mapping.mark_options:
            result = result_by_value.get(option.value)
            if result is None or result.get("status") == "uncertain":
                uncertain = True
            elif result.get("status") == "detected":
                detected_labels.append(option.label)
        if mapping.mark_selection_mode == "single" and len(detected_labels) > 1:
            uncertain = True
        if uncertain:
            return ReadingStatus.UNCERTAIN, None, "MARK_CHOICE_UNRESOLVED"
        if not detected_labels:
            return ReadingStatus.BLANK, None, None
        return ReadingStatus.READABLE, "・".join(detected_labels), None

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
            candidate_type=(
                "checklist" if mapping.mark_recognition_mode == "checklist" else
                "symbol_choice" if mapping.mark_recognition_mode == "symbol" else "mark_choice"
            ),
        )

    @staticmethod
    def _mapped_cells_payload(
        extraction: ExtractionOutput,
        regions: list[OcrRegionInput],
        *,
        mapping_hash: object,
        sent_mapping_ids: set[str] | None = None,
        local_resolution_by_id: dict[str, str] | None = None,
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
        }
