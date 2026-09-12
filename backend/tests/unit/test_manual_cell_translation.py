import hashlib

import numpy as np

from app.domain.template_config import build_blank_template_config
from app.extraction.image_variants import ImageVariants
from app.extraction.region_cropper import RegionCropper
from app.layout_analysis.models import LayoutDefinition
from app.services.layout_service import LayoutService
from tests.unit.test_layout_authoring_extensions import cell, layout, polygon, relation


def test_translated_polygon_roundtrip_keeps_owner_and_crops_the_new_position():
    original = layout([polygon(), cell("heading", "parent_label", text="仮想", virtual=True)],
                      [relation()])
    raw = original.model_dump(mode="json", by_alias=True)
    raw["cells"][0].update(x=30, y=20)
    for point in raw["cells"][0]["vertices"]:
        point["x"] += 20
        point["y"] += 10
    raw["cells"][1].update(x=50, y=50)
    moved = LayoutDefinition.model_validate(raw)
    restored = LayoutDefinition.model_validate_json(moved.model_dump_json(by_alias=True))
    before = LayoutService._as_field_mappings(original)
    after = LayoutService._as_field_mappings(restored)
    assert len(before) == len(after) == 1  # The moved virtual heading is still not OCR input.
    assert before[0].mapping_id == after[0].mapping_id == "layout_input"
    assert before[0].target_path == after[0].target_path == "customFields.漢字2"
    assert original.relations == restored.relations
    assert after[0].as_region().region_id == before[0].as_region().region_id
    image = np.arange(100 * 100 * 3, dtype=np.uint8).reshape((100, 100, 3))
    gray = np.arange(100 * 100, dtype=np.uint8).reshape((100, 100))
    variants = ImageVariants(image, image, image, gray, gray)
    source_sha = hashlib.sha256(image.tobytes() + gray.tobytes()).hexdigest()
    config = build_blank_template_config(template_id="test", template_version=1,
                                        name="test", canonical_width=100, canonical_height=100)
    old_crop = RegionCropper(config, [before[0].as_region()]).crop(variants)["layout_input"]
    new_crop = RegionCropper(config, [after[0].as_region()]).crop(variants)["layout_input"]
    assert old_crop.bbox == (6, 6, 48, 48)
    assert new_crop.bbox == (26, 16, 48, 48)
    np.testing.assert_array_equal(new_crop.aligned_color[9, 9], image[25, 35])
    assert np.all(new_crop.aligned_color[39, 39] == 255)
    assert hashlib.sha256(new_crop.aligned_color.tobytes()).hexdigest() != hashlib.sha256(
        old_crop.aligned_color.tobytes()).hexdigest()
    assert hashlib.sha256(image.tobytes() + gray.tobytes()).hexdigest() == source_sha
