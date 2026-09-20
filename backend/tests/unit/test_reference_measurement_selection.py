from copy import deepcopy
from io import BytesIO
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from app.services.reference_measurement_selection import select_measurement_edges
from tests.unit.test_border_probe_diagnostic import S, fixture, probe_copy


def observation(*samples, status="OBSERVED", indices=None):
    return [{"page": 19, "fields": [{"cellId": "explicit-cell", "sourceRange": "Z11:AA14",
              "edges": [{"status": status, "segmentIndices": [0] if indices is None else indices},
                        {"status": "UNOBSERVED"}, {"status": "AMBIGUOUS"},
                        {"status": "OBSERVED", "segmentIndices": [99]}]}],
             "rasterObservation": {"segments": list(samples)},
             "strokeDifferential": [{
                 "segmentIndex": 0, "status": "UNVERIFIED", "paintFootprints": 12,
                 "changedFootprints": 10, "subpixelComponents": {
                     "status": "UNVERIFIED", "paintComponents": 8, "changedComponents": 7,
                     "maxPaintComponentPx": .6,
                     "intersectionCandidates": [{"candidateSegmentIndices": [8]}],
                 },
             }]}]


def test_missing_edges_and_unsampleable_paint_are_selected_by_identity_without_mutation():
    samples = observation({"segmentIndex": 0, "status": "UNVERIFIABLE",
                           "reason": "NO_INTERIOR_PAINT_SAMPLES"})
    before = deepcopy(samples)
    assert select_measurement_edges(samples) == [{"cellId": "explicit-cell",
                                                 "sourceRange": "Z11:AA14",
                                                 "sides": ["left", "right"]}]
    assert samples == before


@pytest.mark.parametrize("sample", [
    None, {"status": "SAMPLED", "inkCoverage": 0},
    {"status": "SAMPLED", "inkCoverage": .99},
    {"status": "UNVERIFIABLE", "reason": "REFERENCE_EDGE_CLIPPED"},
    {"status": "UNVERIFIABLE", "reason": "UNSUPPORTED_DASH_CAP"},
    {"status": "UNVERIFIABLE", "reason": "UNVERIFIED_PATH"},
    {"status": "UNVERIFIABLE", "reason": "NON_AXIAL_PATH"},
    {"status": "SAMPLED", "inkCoverage": 1},
])
def test_absent_ink_unsupported_paths_and_missing_evidence_are_not_repainted(sample):
    value = observation(*([] if sample is None else [{**sample, "segmentIndex": 0}]))
    assert select_measurement_edges(value)[0]["sides"] == ["right"]


@pytest.mark.parametrize("status", ["AMBIGUOUS", "INVALID"])
def test_unsampleable_ink_never_resolves_ambiguous_geometry(status):
    value = observation({"segmentIndex": 0, "status": "UNVERIFIABLE",
                         "reason": "NO_INTERIOR_PAINT_SAMPLES"}, status=status)
    assert select_measurement_edges(value)[0]["sides"] == ["right"]


@pytest.mark.parametrize("coverage", [0, .99, 1])
def test_a_composed_edge_must_not_hide_another_missing_segment(coverage):
    value = observation({"segmentIndex": 0, "status": "UNVERIFIABLE",
                         "reason": "NO_INTERIOR_PAINT_SAMPLES"},
                        {"segmentIndex": 1, "status": "SAMPLED", "inkCoverage": coverage},
                        indices=[0, 1])
    assert select_measurement_edges(value)[0]["sides"] == (
        ["left", "right"] if coverage == 1 else ["right"])


def test_all_pages_and_explicit_ranges_are_retained_even_when_display_positions_match():
    values = observation({"segmentIndex": 0, "status": "UNVERIFIABLE",
                          "reason": "NO_INTERIOR_PAINT_SAMPLES"})
    page2 = deepcopy(values[0])
    page2["page"] = 27
    page2["fields"][0].update(cellId="other-cell", sourceRange="Z21:AA24")
    values.append(page2)
    assert [item["cellId"] for item in select_measurement_edges(values)] == [
        "explicit-cell", "other-cell"]
    assert [item["sourceRange"] for item in select_measurement_edges(values)] == [
        "Z11:AA14", "Z21:AA24"]


@pytest.mark.parametrize("change", ["missing", "duplicate", "zero", "no_crossing",
                                    "incomplete", "large", "complete"])
def test_only_partial_subpixel_contributions_at_known_crossings_need_a_measurement(change):
    value = observation({"segmentIndex": 0, "status": "UNVERIFIABLE",
                         "reason": "NO_INTERIOR_PAINT_SAMPLES"})
    differences = value[0]["strokeDifferential"]
    components = differences[0]["subpixelComponents"]
    if change == "missing":
        differences.clear()
    elif change == "duplicate":
        differences.append(deepcopy(differences[0]))
    elif change == "zero":
        components["changedComponents"] = 0
    elif change == "no_crossing":
        components["intersectionCandidates"][0]["candidateSegmentIndices"] = []
    elif change == "incomplete":
        components["paintComponents"] += 1
    elif change == "large":
        components["maxPaintComponentPx"] = 1
    else:
        differences[0].update(status="OBSERVED", changedFootprints=12)
    assert select_measurement_edges(value)[0]["sides"] == ["right"]


def test_independently_corroborated_crossings_do_not_trigger_another_copy():
    value = observation({"segmentIndex": 0, "status": "UNVERIFIABLE",
                         "reason": "NO_INTERIOR_PAINT_SAMPLES"},
                        {"segmentIndex": 8, "status": "SAMPLED", "inkCoverage": 1,
                         "contrastCoverage": 1})
    assert select_measurement_edges(value)[0]["sides"] == ["right"]


@pytest.mark.parametrize("style", ["dotted", "dashed", "dashDot", "hair", "thin", "medium"])
def test_measurement_of_existing_border_preserves_original_style_and_other_xml(style):
    _, parts = fixture()
    original_side = f'<top style="{style}"><color rgb="FF001122"/></top>'.encode()
    parts["xl/styles.xml"] = parts["xl/styles.xml"].replace(b"<top/>", original_side)
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    original = stream.getvalue()
    selection = [{"cellId": "explicit-field", "sourceRange": "A1:B1", "sides": ["top"]}]
    copied, report = probe_copy(original, "Form", selection)
    assert original == stream.getvalue()
    assert report["productionQualified"] is False
    assert copied == probe_copy(original, "Form", selection)[0]
    with ZipFile(BytesIO(copied)) as archive:
        for name in parts.keys() - {"xl/styles.xml", "xl/worksheets/sheet1.xml"}:
            assert archive.read(name) == parts[name]
        styles = archive.read("xl/styles.xml")
        assert original_side in styles  # Original style bytes were not rewritten.
        prior = ET.fromstring(parts["xl/styles.xml"])
        current = ET.fromstring(styles)
        assert ET.tostring(prior.find(S + "borders")[0]) == ET.tostring(
            current.find(S + "borders")[0])
        added = current.find(S + "borders")[1]
        assert added.find(S + "top").attrib == {"style": "hair"}
        assert added.find(S + "top").find(S + "color").attrib == {"rgb": "FFFF0000"}
        for side in ("left", "right", "bottom", "diagonal"):
            assert ET.tostring(added.find(S + side)) == ET.tostring(
                prior.find(S + "borders")[0].find(S + side))
        # This fixture has one style. Only the two selected existing cells change.
        expected_sheet = parts["xl/worksheets/sheet1.xml"].replace(b'r="A1" s="0"',
                                                                  b'r="A1" s="1"').replace(
            b'r="B1" s="0"', b'r="B1" s="1"')
        assert archive.read("xl/worksheets/sheet1.xml") == expected_sheet
