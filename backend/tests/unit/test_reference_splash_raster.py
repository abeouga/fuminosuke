from copy import deepcopy

import pytest

from app.services.reference_splash_raster import adjusted_normal_pixels
from tests.unit.test_reference_vector_measurement import paths


def vector_line(center, *, width=.2, vertical=True, reverse=False):
    # paths() converts these raster positions to PDF on a 72pt square.
    endpoints = ((center, 20), (center, 80)) if vertical else (
        (20, center), (80, center))
    if reverse:
        endpoints = endpoints[::-1]
    line = paths([endpoints])["segments"][0]
    line["width"] = width * .48
    vector = {"effectiveBox": [0, 0, 72, 72], "rotation": 0, "userUnit": 1,
              "strokes": {"segments": [line]}}
    return line, vector


@pytest.mark.parametrize("center,expected", [
    (20.1, [20]), (20.25, [20]), (20.49, [20]), (20.5, [20]),
    (20.59, [20]), (20.61, [21]), (20.75, [21]), (20.9, [21]),
])
@pytest.mark.parametrize("vertical,reverse", [(True, False), (True, True),
                                            (False, False), (False, True)])
def test_splash_rounds_opposing_edges_not_the_line_center(center, expected, vertical, reverse):
    args = vector_line(center, vertical=vertical, reverse=reverse)
    before = deepcopy(args)
    assert adjusted_normal_pixels(*args) == expected
    assert args == before


@pytest.mark.parametrize("change", ["dash", "cap", "zero", "wide", "clip", "curve",
                                   "continuation", "compound", "shear", "diagonal"])
def test_unsupported_stroke_adjust_hints_are_not_guessed(change):
    line, vector = vector_line(20.7)
    if change == "dash":
        line["dash"] = [.1, .1]
    elif change == "cap":
        line["lineCap"] = 1
    elif change == "zero":
        line["width"] = 0
    elif change == "wide":
        line["width"] = .48
    elif change == "clip":
        line["clippingUnverified"] = True
    elif change == "curve":
        line["sharesPathWithCurve"] = True
    elif change == "continuation":
        line["subpathDistance"] = 1
    elif change == "compound":
        vector["strokes"]["segments"].append(deepcopy(line))
    elif change == "shear":
        line["strokeMatrix"][2] = .2
    else:
        line["to"][0] += 1
    assert adjusted_normal_pixels(line, vector) is None
