"""Arithmetic/identity fixtures, not evidence of native qualification."""
from copy import deepcopy
from fractions import Fraction

import pytest

from app.domain.calc_print_grid import MODEL
from app.services.calc_reference_precision import compare_coordinates


@pytest.fixture
def sample():
    # twip 72 -> mm100 127; twip 144 -> mm100 254. Margin 10 mm ->
    # twip 567 -> Calc frame origin 999 mm100 at 100%, without centering.
    field = {"cellId": "merged-arbitrary", "sourceRange": "B3:C3",
             "startRow": 3, "endRow": 3, "startCol": 2, "endCol": 3,
             "rectangle": {"x": 66, "y": 74, "width": 15, "height": 15},
             "bindings": [{"mappingId": "mapping-arbitrary", "sourceRegionId": "region-a",
                           "targetPath": "customFields.any"}]}
    page = {"page": 7, "sheetName": "Form", "startRow": 2, "endRow": 3,
            "startCol": 1, "endCol": 3, "fields": [field]}
    plan = {"geometryModelVersion": MODEL, "dpi": 150, "sheetName": "Form",
            "commonScale": 1.0, "paperSize": "A4", "orientation": "portrait",
            "margins": dict.fromkeys(("left", "right", "top", "bottom"), 10),
            "horizontalCentering": False, "verticalCentering": False,
            "geometryEvidence": {"columns": [3.6] * 3, "rows": [7.2] * 3,
                                 "hiddenColumns": [], "hiddenRows": []},
            "pages": [page]}
    height = Fraction(8000009, 10000)
    box = [0, 0, 600, float(height)]
    vector = {"renderedPage": 1, "rotation": 0, "userUnit": 1,
              "mediaBox": box, "cropBox": box, "effectiveBox": box}
    # Independently calculated integer PDF command coordinates.
    pts = [Fraction(31918, 1000), Fraction(39118, 1000),
           height - Fraction(764482, 1000), height - Fraction(757282, 1000)]
    observation = {"page": 7, "fields": [{
        **{k: deepcopy(field[k]) for k in ("cellId", "sourceRange", "bindings")},
        "edges": [{"status": "OBSERVED", "positionPx": float(p * 150 / 72)} for p in pts],
    }]}
    return plan, page, vector, observation


def test_exact_coordinates_preserve_meaning_and_do_not_certify(sample):
    before = deepcopy(sample)
    report = compare_coordinates(*sample)
    assert sample == before
    assert report["status"] == "MATCH" and report["codes"] == []
    assert report["page"] == 7  # A logical page ID is not the PDF ordinal.
    assert report["productionQualified"] is report["visibilityVerified"] is False
    assert report["fields"][0]["bindings"] == sample[1]["fields"][0]["bindings"]
    assert [e["expectedMillipoint"] for e in report["fields"][0]["edges"]] == [
        31918, 39118, 764482, 757282]


@pytest.mark.parametrize('model', [None, 'lo-import-twip-v1', 'wsl-fods-physical-length-v1'])
def test_old_model_is_never_reinterpreted(sample, model):
    sample[0]["geometryModelVersion"] = model
    assert compare_coordinates(*sample) is None


@pytest.mark.parametrize('key,value', [('rotation', 90), ('rotation', False),
                                     ('userUnit', 2), ('userUnit', True)])
def test_other_pdf_coordinate_paths_stay_unverified(sample, key, value):
    sample[2][key] = value
    result = compare_coordinates(*sample)
    assert result["status"] == "UNVERIFIED"
    assert result["codes"] == ["PDF_COORDINATE_PATH_UNSUPPORTED"]


def test_cropbox_offset_is_not_hidden_by_a_translation(sample):
    sample[2]['effectiveBox'] = sample[2]['cropBox'] = [1, 0, 600, 800]
    assert compare_coordinates(*sample)['status'] == 'UNVERIFIED'


@pytest.mark.parametrize('delta', [.001, -.001, 1])
def test_subpixel_mismatch_is_not_rescued_by_old_two_pixel_budget(sample, delta):
    sample[3]['fields'][0]['edges'][0]['positionPx'] += delta
    report = compare_coordinates(*sample)
    assert report['status'] == 'UNVERIFIED'
    assert report['codes'] == ['PDF_COORDINATE_MISMATCH']


def test_ambiguous_edge_is_not_replaced_with_predicted_data(sample):
    sample[3]['fields'][0]['edges'][0] = {'status': 'AMBIGUOUS'}
    report = compare_coordinates(*sample)
    assert report['codes'] == ['PDF_COORDINATE_UNOBSERVED']
    assert report['fields'][0]['edges'][0]['differencePx'] is None


def test_saved_rectangle_rounding_is_checked_separately(sample):
    sample[1]['fields'][0]['rectangle']['x'] += 1
    report = compare_coordinates(*sample)
    assert report['codes'] == ['SAVED_EDGE_ROUNDING_MISMATCH']
    assert all(e['differencePx'] == 0 for e in report['fields'][0]['edges'])


@pytest.mark.parametrize('change', ['mapping', 'range', 'missing', 'duplicate', 'page',
                                  'sheet', 'outside', 'float_rectangle', 'scale', 'boolean_extent'])
def test_identity_and_invalid_input_rejected_without_partial_success(sample, change):
    plan, page, _, observation = sample
    field = page['fields'][0]
    if change == 'mapping':
        observation['fields'][0]['bindings'][0]['mappingId'] = 'other'
    elif change == 'range':
        observation['fields'][0]['sourceRange'] = 'C3:D3'
    elif change == 'missing':
        observation['fields'] = []
    elif change == 'duplicate':
        observation['fields'].append(deepcopy(observation['fields'][0]))
    elif change == 'page':
        observation['page'] = 8
    elif change == 'sheet':
        page['sheetName'] = 'other'
    elif change == 'outside':
        field['endCol'] = 4
    elif change == 'float_rectangle':
        field['rectangle']['x'] = 66.0
    elif change == 'scale':
        plan['commonScale'] = .695
    else:
        field['startRow'] = True
    with pytest.raises(ValueError):
        compare_coordinates(*sample)


def test_tiny_extent_minimum_one_expansion_is_not_half_pixel_rounding(sample):
    sample[0]['geometryEvidence']['rows'] = [.0576] * 3  # one twip per visible row
    report = compare_coordinates(*sample)
    assert report['status'] == 'UNVERIFIED'
    assert 'COLLAPSED_FIELD_EXTENT' in report['codes']


def test_non_ocr_heading_is_excluded_only_by_explicit_empty_bindings(sample):
    sample[1]['fields'].append({'cellId': 'heading-arbitrary', 'bindings': []})
    assert compare_coordinates(*sample)['status'] == 'MATCH'
