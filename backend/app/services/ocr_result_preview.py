from __future__ import annotations

from typing import Any

from app.db.models import ProcessingJob


def _field_previews(
    job: ProcessingJob,
    document: dict[str, Any],
) -> list[dict[str, object]]:
    snapshot = dict(job.config_snapshot_json)
    raw_mappings = snapshot.get("fieldMappings")
    raw_structure = snapshot.get("recordStructure")
    binding_by_mapping: dict[str, dict[str, object]] = {}
    if isinstance(raw_structure, dict):
        raw_bindings = raw_structure.get("fieldBindings")
        if isinstance(raw_bindings, list):
            binding_by_mapping = {
                mapping_id: raw
                for raw in raw_bindings
                if isinstance(raw, dict) and isinstance((mapping_id := raw.get("mappingId")), str)
            }
    previews: list[dict[str, object]] = []
    current_path_by_mapping: dict[str, str] = {}
    current_box_by_mapping: dict[str, dict[str, object] | None] = {}

    def collect_current_readings(value: object, path: str) -> None:
        if isinstance(value, list):
            for index, child in enumerate(value):
                collect_current_readings(child, f"{path}.{index}")
            return
        if not isinstance(value, dict):
            return
        source_region_id = value.get("sourceRegionId")
        if "status" in value and isinstance(source_region_id, str):
            if source_region_id != "unmapped":
                current_path_by_mapping[source_region_id] = path
                box = value.get("boundingBox")
                current_box_by_mapping[source_region_id] = box if isinstance(box, dict) else None
            return
        for key, child in value.items():
            collect_current_readings(child, f"{path}.{key}" if path else key)

    collect_current_readings(document.get("fields"), "fields")

    if isinstance(raw_mappings, list):
        for raw in raw_mappings:
            if not isinstance(raw, dict):
                continue
            mapping_id = raw.get("mappingId")
            target_path = raw.get("targetPath")
            if not isinstance(mapping_id, str) or not isinstance(target_path, str):
                continue
            binding = binding_by_mapping.get(mapping_id, {})
            current_box = current_box_by_mapping.get(mapping_id)
            previews.append(
                {
                    # The reading's source cell is authoritative. This also
                    # remains correct if a collection row is moved or
                    # compacted after the template snapshot was created.
                    "targetPath": current_path_by_mapping.get(mapping_id, target_path),
                    "mappingId": mapping_id,
                    "artifactUrl": (
                        f"/api/v1/jobs/{job.id}/artifacts/fieldCrop.{mapping_id}.alignedColor"
                    ),
                    "boundingBox": current_box
                    if mapping_id in current_box_by_mapping
                    else {
                        "x": raw.get("x"),
                        "y": raw.get("y"),
                        "width": raw.get("width"),
                        "height": raw.get("height"),
                    },
                    "recordGroupId": binding.get("recordGroupId"),
                    "fieldKey": binding.get("fieldKey"),
                    "mappingType": raw.get("mappingType", "text"),
                    "markRecognitionMode": raw.get("markRecognitionMode", "automatic"),
                    "markSelectionMode": raw.get("markSelectionMode", "multiple"),
                    "markOptions": [
                        {
                            "label": option.get("label"),
                            "value": option.get("value"),
                        }
                        for option in raw.get("markOptions", [])
                        if isinstance(option, dict)
                        and isinstance(option.get("label"), str)
                        and isinstance(option.get("value"), str)
                    ],
                }
            )
        if previews:
            return previews
    return previews
