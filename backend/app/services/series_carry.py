"""Reading an update of a confirmed document: only previously blank fields go to the provider.

The plan (which mappings keep the predecessor's confirmed reading) is fixed in the accepted
snapshot (`documentSeries.carried`, see document_series). Registration and cropping still run
for every mapping; only the provider request is narrowed. A carried reading keeps the
predecessor's raw/normalized/status/alternatives/reviewReason and takes this job's own crop
(sourceRegionId = the same mappingId, boundingBox = this image's crop), so the review shows the
new paper next to the kept value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from app.domain.ocr_models import BoundingBox, FieldReading
from app.domain.template_config import FieldMappingConfig
from app.extraction.region_cropper import RegionCrops
from app.services.document_series import MODE_BLANK_ONLY, SNAPSHOT_KEY
from app.services.ocr_request_preparation_service import PreparedOcrRequest

CARRIED_RESOLUTION = "carried_from_predecessor"


def carried_plan(job_snapshot: Mapping[str, object]) -> dict[str, dict[str, Any]]:
    """mappingId -> carried confirmed reading, or {} when the job is not a blank_only update."""
    series = job_snapshot.get(SNAPSHOT_KEY)
    if not isinstance(series, dict) or series.get("mode") != MODE_BLANK_ONLY:
        return {}
    carried = series.get("carried")
    return dict(carried) if isinstance(carried, dict) else {}


def without_carried(prepared: PreparedOcrRequest, carried_ids: set[str]) -> PreparedOcrRequest:
    """Drop carried mappings from the provider request; mappedCells keeps every cell."""
    regions = [
        region
        for region in prepared.request.regions
        if (region.source_region_id or region.region_id) not in carried_ids
    ]
    payload = prepared.mapped_cells_payload
    if payload is not None:
        payload = dict(payload)
        sent = [region for region in regions if region.target_path]
        payload["openAiCropCount"] = len(sent)
        payload["openAiImageCount"] = sum(
            1 + int(region.handwriting_png is not None) for region in sent
        )
        cell_count = payload.get("mappedCellCount")
        if isinstance(cell_count, int):
            payload["locallyResolvedCellCount"] = cell_count - len(sent)
        payload["carriedFromPredecessorCount"] = len(carried_ids)
        cells = payload.get("cells")
        if isinstance(cells, list):
            payload["cells"] = [
                {**cell, "sentToOpenAi": False, "localResolution": CARRIED_RESOLUTION}
                if isinstance(cell, dict) and cell.get("mappingId") in carried_ids
                else cell
                for cell in cells
            ]
    return replace(
        prepared,
        request=replace(prepared.request, regions=regions),
        mapped_cells_payload=payload,
        openai_mark_ids=prepared.openai_mark_ids - carried_ids,
        sequence_regions=[
            region for region in prepared.sequence_regions if region.region_id not in carried_ids
        ],
    )


def carried_readings(
    plan: Mapping[str, Mapping[str, Any]],
    mappings: list[FieldMappingConfig],
    crops: Mapping[str, RegionCrops],
) -> dict[str, tuple[str, FieldReading]]:
    """mappingId -> (targetPath, reading) on this job's crops. Mappings must match the plan."""
    by_id = {mapping.mapping_id: mapping for mapping in mappings if mapping.enabled}
    readings: dict[str, tuple[str, FieldReading]] = {}
    for mapping_id, spec in plan.items():
        mapping = by_id.get(mapping_id)
        if mapping is None or mapping.target_path != spec.get("targetPath"):
            raise ValueError("documentSeries.carried does not match the accepted mappings")
        crop = crops.get(mapping_id)
        box = (
            crop.bbox if crop is not None else (mapping.x, mapping.y, mapping.width, mapping.height)
        )
        readings[mapping_id] = (
            mapping.target_path,
            FieldReading.model_validate(
                {
                    "raw": spec.get("raw"),
                    "normalized": spec.get("normalized"),
                    "status": spec.get("status"),
                    "alternatives": spec.get("alternatives") or [],
                    "reviewReason": spec.get("reviewReason"),
                    "sourceRegionId": mapping_id,
                    "boundingBox": BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3]),
                }
            ),
        )
    return readings
