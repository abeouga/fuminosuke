from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.services.wsl_workbook_geometry import MODEL, apply_dimensions, dimensions


def fods(width="0.389in", height="0.25in", repeat="3", visibility="visible"):
    return f'''<office:document
        xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
        xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0">
      <office:automatic-styles>
        <style:style style:name="co1"><style:table-column-properties
          style:column-width="{width}"/></style:style>
        <style:style style:name="ro1"><style:table-row-properties
          style:row-height="{height}"/></style:style>
      </office:automatic-styles>
      <office:body><office:spreadsheet><table:table table:name="Sample">
        <table:table-column-group><table:table-column table:style-name="co1"
          table:number-columns-repeated="{repeat}" table:visibility="{visibility}"/>
        </table:table-column-group>
        <table:table-row table:style-name="ro1" table:number-rows-repeated="20"/>
      </table:table></office:spreadsheet></office:body></office:document>'''.encode()


@pytest.mark.parametrize(
    "value,points", [("0.389in", 28.008), ("2.54cm", 72), ("25.4mm", 72), ("15pt", 15)]
)
def test_measured_units_are_not_silently_rounded_to_legacy_twips(value, points):
    result = dimensions(fods(width=value), "Sample", 4, 3)
    assert result["model"] == MODEL
    assert result["columns"] == pytest.approx([points] * 3)
    assert result["rows"] == [18] * 4


@pytest.mark.parametrize("visibility", ["collapse", "filter"])
def test_hidden_dimensions_keep_source_indices_with_zero_extent(visibility):
    result = dimensions(fods(visibility=visibility), "Sample", 4, 3)
    assert result["columns"] == [0, 0, 0]
    assert result["hiddenColumns"] == [1, 2, 3]
    assert result["hiddenRows"] == []


@pytest.mark.parametrize(
    "params",
    [
        {"width": "nanin"},
        {"height": "-1in"},
        {"width": "2px"},
        {"repeat": "0"},
        {"repeat": "99999999"},
        {"repeat": "2"},
        {"visibility": "unknown"},
    ],
)
def test_invalid_or_incomplete_measurements_fail_closed(params):
    with pytest.raises(AppError):
        dimensions(fods(**params), "Sample", 4, 3)


def test_source_sheet_identity_and_xml_entity_boundaries():
    for content, sheet in [
        (fods(), "Missing"),
        (b'<!DOCTYPE a [<!ENTITY b "x">]><a>&b;</a>', "Sample"),
    ]:
        with pytest.raises(AppError):
            dimensions(content, sheet, 4, 3)


def test_projection_preserves_source_ownership_and_uses_integer_print_scale():
    import copy

    from app.domain.print_layout import page_plan
    from app.services.print_layout_source import read_source
    from tests.unit.test_print_layout import request, workbook_bytes

    source = read_source(
        workbook_bytes(rows=30),
        "帳票",
        {"cells": [{"id": "xlsx_C2", "role": "input"}, {"id": "xlsx_C20", "role": "input"}]},
    )
    for index, field in enumerate(source["fields"]):
        field["bindings"] = [
            {
                "mappingId": f"meaning-{index}",
                "targetPath": f"customFields.x{index}",
                "sourceRegionId": f"meaning-{index}",
            }
        ]
    original = copy.deepcopy(source)
    evidence = {
        "model": MODEL,
        "sourceSha256": source["sha256"],
        "columns": [10, 251.23, 251.23, 251.23],
        "rows": [15] + [24] * 29,
    }
    measured = apply_dimensions(source, evidence)
    plan = page_plan(measured, request(break_after_rows=[15]))
    assert source == original
    assert plan["commonScale"] * 100 == round(plan["commonScale"] * 100)
    assert plan["printScalePolicy"] == "integer-percent-v1"
    assert [p["range"] for p in plan["pages"]] == ["B2:D15", "B16:D30"]
    assert [
        (f["cellId"], f["sourceRange"], f["bindings"]) for p in plan["pages"] for f in p["fields"]
    ] == [(f["cellId"], f["sourceRange"], f["bindings"]) for f in original["fields"]]
    evidence["columns"][1] = 0  # A visible source column cannot disappear from geometry.
    with pytest.raises(AppError):
        apply_dimensions(source, evidence)
