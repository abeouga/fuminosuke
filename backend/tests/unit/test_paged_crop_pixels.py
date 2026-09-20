from __future__ import annotations

import copy
import hashlib
from dataclasses import replace

import numpy as np
import pytest

from app.core.errors import AppError
from app.domain.template_config import FieldMappingConfig, build_blank_template_config
from app.extraction.image_variants import create_variants
from app.extraction.region_cropper import RegionCropper
from app.services.extraction_service import ExtractionOutput
from app.services.paged_crop_correspondence import ERROR, VARIANTS, verify_pixels


def case(polygon=False):
    config = build_blank_template_config(
        template_id="arbitrary", template_version=1, name="Fixture",
        canonical_width=300, canonical_height=300,
    )
    config.field_mappings = [
        FieldMappingConfig(
            mapping_id=name, target_path=f"customFields.{name}", label="Same label",
            x=x, y=40, width=50, height=40, padding=2,
            **({"vertices": [{"x": x, "y": 40}, {"x": x + 50, "y": 40},
                             {"x": x + 25, "y": 80}]} if polygon else {}),
        )
        for name, x in (("first", 30), ("second", 130))
    ]
    raw = config.model_dump(mode="json", by_alias=True)
    page = {
        "pageId": "page-identity", "inputId": "input-identity", "inputSha256": "a" * 64,
        "templateVersionId": "version", "ocrProvider": "openai", "ocrEnabled": True,
        "templateSnapshot": {"config": raw, "cleanTemplateSha256": "b" * 64},
        "fieldMappings": copy.deepcopy(raw["fieldMappings"]),
    }
    rng = np.random.default_rng(2186)
    aligned, reference, handwriting = (
        rng.integers(0, 256, (300, 300, 3), dtype=np.uint8) for _ in range(3)
    )
    crops = RegionCropper(config).crop(create_variants(aligned, reference, handwriting))
    extraction = ExtractionOutput(
        job_id="job", attempt_number=1, template_version_id="version",
        aligned_rgb=aligned.copy(), template_rgb=reference.copy(), crops=crops,
        marks=[], quality={}, unknown_overlap_regions=[], artifacts={}, config=config,
        page_snapshot=copy.deepcopy(page),
    )
    return extraction, page, dict(aligned=aligned, reference=reference, handwriting=handwriting)


@pytest.mark.parametrize("polygon", [False, True])
def test_all_crop_variants_match_without_changing_any_pixels_or_identity(polygon):
    extraction, page, pixels = case(polygon)
    before = copy.deepcopy(extraction)
    report = verify_pixels(extraction, page, **pixels)
    assert report["status"] == "VERIFIED"
    assert report["inkClearanceVerified"] is False
    assert report["productionQualified"] is False
    assert report["inputId"] == page["inputId"]
    assert extraction.page_snapshot == before.page_snapshot
    assert extraction.config == before.config
    for record in report["regions"]:
        region_id = record["regionId"]
        assert record["bbox"] == list(before.crops[region_id].bbox)
        for name in VARIANTS:
            expected = getattr(before.crops[region_id], name)
            assert np.array_equal(getattr(extraction.crops[region_id], name), expected)
            assert record["pixelSha256"][name] == hashlib.sha256(expected.tobytes()).hexdigest()


@pytest.mark.parametrize("polygon", [False, True])
@pytest.mark.parametrize("variant", VARIANTS)
def test_one_changed_pixel_in_any_variant_is_rejected(polygon, variant):
    extraction, page, pixels = case(polygon)
    crop = extraction.crops["first"]
    changed = getattr(crop, variant).copy()
    changed[0, 0] ^= 255  # Also tests masked-out polygon pixels.
    extraction.crops["first"] = replace(crop, **{variant: changed})
    with pytest.raises(AppError) as caught:
        verify_pixels(extraction, page, **pixels)
    assert caught.value.code == ERROR
    assert caught.value.metadata["reason"] == f"CROP_PIXELS_CHANGED:{variant}"
    assert caught.value.metadata["regionId"] == "first"


@pytest.mark.parametrize("change", [
    "page", "input", "version", "config", "snapshot_config", "missing", "extra",
    "bbox", "region_config", "swapped_crop", "dtype", "shape", "aligned", "reference",
    "handwriting", "canonical_shape", "snapshot_type",
])
def test_no_saved_boolean_or_similar_label_can_override_mismatched_provenance(change):
    extraction, page, pixels = case()
    extraction.quality = {"qualified": True, "cropCorrespondence": {"status": "VERIFIED"}}
    crop = extraction.crops["first"]
    if change in {"page", "input"}:
        extraction.page_snapshot[f"{change}Id"] = "other"
    elif change == "version":
        extraction.template_version_id = "other"
    elif change == "config":
        extraction.config.field_mappings[0].target_path = "customFields.other"
    elif change == "snapshot_config":
        extraction.page_snapshot["templateSnapshot"]["config"]["fieldMappings"][0]["x"] += 1
    elif change == "snapshot_type":
        extraction.page_snapshot["ocrEnabled"] = 1  # Python equality alone accepts True == 1.
    elif change == "missing":
        extraction.crops.pop("first")
    elif change == "extra":
        extraction.crops["third"] = crop
    elif change == "bbox":
        extraction.crops["first"] = replace(crop, bbox=(29, *crop.bbox[1:]))
    elif change == "region_config":
        extraction.crops["first"] = replace(crop, config=extraction.crops["second"].config)
    elif change == "swapped_crop":
        extraction.crops["first"] = replace(
            crop, aligned_color=extraction.crops["second"].aligned_color.copy(),
        )
    elif change == "dtype":
        extraction.crops["first"] = replace(crop, aligned_color=crop.aligned_color.astype(float))
    elif change == "shape":
        extraction.crops["first"] = replace(crop, aligned_color=crop.aligned_color[:-1])
    elif change == "aligned":
        extraction.aligned_rgb[40, 30] ^= 255
    elif change == "reference":
        extraction.template_rgb[40, 30] ^= 255
    elif change == "handwriting":
        pixels["handwriting"][40, 30] ^= 255
    else:
        pixels["aligned"] = pixels["aligned"][:-1]
    with pytest.raises(AppError) as caught:
        verify_pixels(extraction, page, **pixels)
    assert caught.value.code == ERROR


def test_no_mapping_order_or_label_matching_is_used():
    extraction, page, pixels = case()
    extraction.crops = dict(reversed(list(extraction.crops.items())))
    assert verify_pixels(extraction, page, **pixels)["status"] == "VERIFIED"
