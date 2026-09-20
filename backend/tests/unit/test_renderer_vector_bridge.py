import io
from copy import deepcopy
from zipfile import ZipFile

import pytest

from app.services import renderer_glyph_bridge as glyph
from app.services import renderer_vector_bridge as bridge
from app.services.renderer_pdf_strokes import inventory
from tests.unit.test_renderer_glyph_bridge import evidence, reference

reference = reference


@pytest.fixture
def sample(reference):
    pdf, renderer = reference
    renderer["imageSizes"] = [[417, 625]]
    report = evidence(glyph.input_manifest(pdf, renderer))
    strokes = inventory([([10, 20], b"m"), ([30, 20], b"l"), ([], b"S")])
    checked = report["glyphs"]["pages"][0]
    report["vectors"] = {
        "policy": bridge.POLICY, "pdfSha256": renderer["pdfSha256"], "pageCount": 1,
        "productionQualified": False, "visibilityVerified": False, "segmentCount": 1,
        "pages": [{"renderedPage": 1, "contentSha256": checked["contentSha256"],
                   "contentOperations": checked["contentOperations"],
                   "mediaBox": [0, 0, 200, 300], "cropBox": [0, 0, 200, 300],
                   "effectiveBox": [0, 0, 200, 300], "rotation": 0, "userUnit": 1,
                   "strokes": strokes}],
    }
    return report, renderer


def test_inventory_dimensions_match_the_exact_raster_reference(sample):
    bridge.validate_vectors(*sample)
    report, reference = deepcopy(sample)
    report["vectors"]["pages"][0]["rotation"] = 90
    reference["imageSizes"][0].reverse()
    bridge.validate_vectors(report, reference)


@pytest.mark.parametrize("change", [None, "sha", "font", "count", "policy", "flag", "status"])
def test_optional_text_transcript_is_validated_when_present(sample, change):
    report, reference = deepcopy(sample)
    trace = {"policy": "wsl-pdf-nonstroke-text-program-v1", "status": "RECORDED",
             "programSha256": "a" * 64, "fontResourcesSha256": "b" * 64,
             "retainedOperations": 1, "visibilityVerified": False}
    report["vectors"]["pages"][0]["textProgram"] = trace
    if change == "sha":
        trace["programSha256"] = "not-a-hash"
    elif change == "font":
        trace.pop("fontResourcesSha256")
    elif change == "count":
        trace["retainedOperations"] = True
    elif change == "policy":
        trace["policy"] = "unknown"
    elif change == "flag":
        trace["visibilityVerified"] = True
    elif change == "status":
        trace["status"] = "QUALIFIED"
    if change is None:
        bridge.validate_vectors(report, reference)
    else:
        with pytest.raises(ValueError, match="text transcript"):
            bridge.validate_vectors(report, reference)


@pytest.mark.parametrize("distance", [None, -1, True, float("inf"), "missing"])
def test_missing_or_invalid_straight_subpath_distance_is_rejected(sample, distance):
    report, reference = deepcopy(sample)
    line = report["vectors"]["pages"][0]["strokes"]["segments"][0]
    if distance == "missing":
        line.pop("subpathDistance")
    else:
        line["subpathDistance"] = distance
    with pytest.raises(ValueError):
        bridge.validate_vectors(report, reference)


@pytest.mark.parametrize("change", [
    "page_count", "page_id", "content", "operations", "box", "effective", "rotation",
    "unit", "size", "visibility", "production", "total", "segment", "width", "dash",
    "color", "ordinal", "boolean", "path_count", "coordinate", "matrix", "space", "singular",
])
def test_report_changes_are_rejected(sample, change):
    report, reference = deepcopy(sample)
    value = report["vectors"]
    page = value["pages"][0]
    strokes = page["strokes"]
    line = strokes["segments"][0]
    if change == "page_count":
        value["pageCount"] = True
    elif change == "page_id":
        page["renderedPage"] = True
    elif change == "content":
        page["contentSha256"] = "0" * 64
    elif change == "operations":
        page["contentOperations"] += 1
    elif change == "box":
        page["mediaBox"] = [0, 0, 0, 300]
    elif change == "effective":
        page["effectiveBox"][0] = 1
    elif change == "rotation":
        page["rotation"] = 45
    elif change == "unit":
        page["userUnit"] = True
    elif change == "size":
        reference["imageSizes"][0][0] += 1
    elif change == "visibility":
        strokes["visibilityVerified"] = True
    elif change == "production":
        value["productionQualified"] = True
    elif change == "total":
        value["segmentCount"] = 2
    elif change == "segment":
        strokes["segments"][0] = None
    elif change == "width":
        line["width"] = -1
    elif change == "dash":
        line["dash"] = [0, 0]
    elif change == "color":
        line["color"] = [1, 0, 0]
    elif change == "ordinal":
        line["operation"] = 5
    elif change == "boolean":
        line["clippingUnverified"] = 1
    elif change == "path_count":
        strokes["curvedStrokePaths"] = 2
    elif change == "coordinate":
        line["from"][0] = float("nan")
    elif change == "space":
        line["colorSpace"] = []
    elif change == "singular":
        line["strokeMatrix"] = [1, 0, 0, 0, 0, 0]
    else:
        line["strokeMatrix"] = [1, 0]
    with pytest.raises(ValueError):
        bridge.validate_vectors(report, reference)


def test_vector_bundle_is_fixed_and_does_not_mutate_glyph_bundle():
    before = glyph.code_bundle()
    result = bridge.code_bundle()
    assert glyph.code_bundle() == before and result != before
    assert result == bridge.code_bundle()
    with ZipFile(io.BytesIO(result)) as archive:
        assert archive.read("__main__.py") == (
            b"from app.services.renderer_vector_worker import main\nmain()\n")
        assert archive.namelist() == ["__main__.py", "app/__init__.py", "app/services/__init__.py",
            *[f"app/services/{name}.py" for name in (*glyph.CODE_MODULES,
                "renderer_pdf_strokes", "renderer_vector_pdf", "renderer_stroke_probe_pdf",
                "renderer_stroke_probe_pixels", "renderer_stroke_probe_worker",
                "renderer_splash_calibration", "renderer_vector_worker")]]
