import hashlib

import numpy as np
import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.domain.template_config import FieldMappingConfig, build_blank_template_config
from app.extraction.image_variants import ImageVariants
from app.extraction.region_cropper import RegionCropper
from app.layout_analysis.models import LayoutCell, LayoutDefinition, LayoutRelation
from app.services.label_cell_ocr_service import recognize_label_cells
from app.services.layout_service import LayoutService
from app.services.template_definition_service import TemplateDefinitionService
from app.services.template_version_service import fork_layout


def cell(identity="input", role="input", **kwargs):
    return LayoutCell(id=identity, role=role, x=10, y=10, width=40, height=40,
                      confidence=1, source="manual", **kwargs)


def polygon(**kwargs):
    return cell(shape_type="polygon", vertices=[
        {"x": 10, "y": 10}, {"x": 50, "y": 10}, {"x": 10, "y": 50},
    ], **kwargs)


def layout(cells, relations=None):
    return LayoutDefinition(canonical_width=100, canonical_height=100, cells=cells,
                            relations=relations or [])


def relation(**kwargs):
    return LayoutRelation(id="meaning", input_cell_id="input", label_cell_ids=[],
                          axis="horizontal", order=1, target_path="customFields.漢字2", **kwargs)


def test_polygon_layout_mapping_region_roundtrip_and_legacy_rectangle():
    document = layout([polygon()], [relation()])
    restored = LayoutDefinition.model_validate_json(document.model_dump_json(by_alias=True))
    assert restored == document
    mapping = LayoutService._as_field_mappings(restored)[0]
    reloaded = FieldMappingConfig.model_validate_json(mapping.model_dump_json(by_alias=True))
    assert reloaded.vertices == restored.cells[0].vertices
    assert reloaded.as_region().vertices == reloaded.vertices
    assert reloaded.mapping_id == "layout_input"
    assert reloaded.target_path == "customFields.漢字2"
    legacy = layout([cell()], [relation()])
    legacy_mapping = LayoutService._as_field_mappings(legacy)[0]
    assert legacy_mapping.as_region().vertices == []
    assert "vertices" not in legacy_mapping.model_dump(mode="json", by_alias=True)
    assert "vertices" not in legacy_mapping.as_region().model_dump(mode="json", by_alias=True)


def test_optional_polygon_serialization_retains_the_strict_export_schema():
    schema = FieldMappingConfig.model_json_schema(mode="serialization")
    assert schema["additionalProperties"] is False
    required = {"mappingId", "targetPath", "label", "x", "y", "width", "height"}
    assert required <= set(schema["required"])
    assert "vertices" in schema["properties"]
    assert "vertices" not in schema["required"]
    assert "compositeSubfields" in schema["properties"]


def test_generic_definition_conversion_rejects_authoring_metadata_instead_of_losing_it():
    assert TemplateDefinitionService._cells_from_layout(layout([cell()]))[0].id == "input"
    for document in (
        layout([polygon()]),
        layout([cell("virtual", "parent_label", text="記入日", virtual=True)]),
        layout([cell("parent", "parent_label"), cell(parent_label_cell_id="parent")]),
    ):
        with pytest.raises(AppError) as error:
            TemplateDefinitionService._cells_from_layout(document)
        assert error.value.code == "GENERIC_DEFINITION_AUTHORING_UNSUPPORTED"


@pytest.mark.parametrize("vertices", [
    [{"x": 10, "y": 10}, {"x": 50, "y": 50}],
    [{"x": 10, "y": 10}, {"x": 50, "y": 50}, {"x": 10, "y": 50}, {"x": 50, "y": 10}],
    [{"x": 10, "y": 10}, {"x": 30, "y": 30}, {"x": 50, "y": 50}],
    [{"x": 10, "y": 10}, {"x": 50, "y": 10}, {"x": 10, "y": 10}],
    [{"x": 10, "y": 10}, {"x": 60, "y": 10}, {"x": 10, "y": 50}],
])
def test_invalid_polygon_is_rejected(vertices):
    with pytest.raises(ValidationError):
        cell(shape_type="polygon", vertices=vertices)


def test_polygon_mask_preserves_inside_pixels_bbox_and_rectangle_bytes():
    image = np.arange(100 * 100 * 3, dtype=np.uint8).reshape((100, 100, 3))
    gray = np.arange(100 * 100, dtype=np.uint8).reshape((100, 100))
    variants = ImageVariants(image, image, image, gray, gray)
    before = hashlib.sha256(image.tobytes() + gray.tobytes()).hexdigest()
    config = build_blank_template_config(template_id="test", template_version=1,
                                        name="test", canonical_width=100, canonical_height=100)
    poly = LayoutService._as_field_mappings(layout([polygon()], [relation()]))[0].as_region()
    rect = poly.model_copy(update={"region_id": "rectangle", "vertices": []})
    crops = RegionCropper(config, [poly, rect]).crop(variants)
    masked, legacy = crops[poly.region_id], crops[rect.region_id]
    assert masked.bbox == legacy.bbox == (6, 6, 48, 48)
    # Global 15,15 is inside; 45,45 is within the bbox but outside the triangle.
    for name in (
        "aligned_color", "clean_reference", "handwriting_only", "enhanced_gray", "ink_difference",
    ):
        actual = getattr(masked, name)
        original = getattr(variants, name)
        np.testing.assert_array_equal(actual[9, 9], original[15, 15])
        np.testing.assert_array_equal(getattr(legacy, name), original[6:54, 6:54])
        assert np.all(actual[39, 39] == (0 if name == "ink_difference" else 255))
    assert hashlib.sha256(image.tobytes() + gray.tobytes()).hexdigest() == before


def test_heading_hierarchy_virtual_and_fork_keep_explicit_ids():
    document = layout([
        cell("A", "parent_label", text="振込情報"),
        cell("B", "parent_label", text="記入日", virtual=True, parent_label_cell_id="A"),
        cell("label", "label", text="漢字", parent_label_cell_id="B"),
        polygon(parent_label_cell_id="B"),
    ], [relation()])
    restored = LayoutDefinition.model_validate_json(document.model_dump_json(by_alias=True))
    assert restored == document
    before = LayoutService._as_field_mappings(layout([polygon()], [relation()]))
    assert LayoutService._as_field_mappings(restored) == before
    forked, replacements = fork_layout(restored.model_dump(mode="json", by_alias=True))
    forked = LayoutDefinition.model_validate(forked)
    assert forked.cells[1].parent_label_cell_id == replacements["A"]
    assert forked.cells[2].parent_label_cell_id == replacements["B"]
    assert forked.relations[0].target_path == "customFields.漢字2"
    assert forked.cells[-1].vertices == document.cells[-1].vertices


@pytest.mark.parametrize("parents", [("A", None, None), ("B", "A", None), ("B", "C", "A")])
def test_heading_self_and_cycles_rejected(parents):
    with pytest.raises(ValidationError, match="cycles"):
        layout([cell(name, "parent_label", parent_label_cell_id=parent)
                for name, parent in zip(("A", "B", "C"), parents, strict=True)])


def test_legacy_parent_relations_participate_in_cycle_validation():
    with pytest.raises(ValidationError, match="cycles"):
        layout([cell("A", "parent_label", parent_label_cell_id="B"), cell("B", "label"), cell()],
               [LayoutRelation(id="old", input_cell_id="input", label_cell_ids=["B"],
                               parent_label_cell_id="A", target_path="customFields.漢字",
                               axis="horizontal", order=1)])


def test_virtual_heading_is_not_sent_to_label_ocr_or_field_mappings():
    class Detector:
        available = True

        def detect(self, image):
            raise AssertionError("Virtual heading must never be OCRed")

    heading = cell("heading", "parent_label", virtual=True, text="記入日")
    result = recognize_label_cells(np.zeros((100, 100, 3), dtype=np.uint8), [heading],
                                   text_detector=Detector())
    assert result.cells == [heading]
    assert result.recognized_count == 0
    assert LayoutService._as_field_mappings(layout([heading])) == []
    with pytest.raises(ValidationError, match="Virtual cells"):
        cell(virtual=True, text="記入日")
