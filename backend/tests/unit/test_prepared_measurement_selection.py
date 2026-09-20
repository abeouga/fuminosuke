"""Do not apply the rejected broader measurement-copy experiment to saved geometry."""
import pytest

from app.services.prepared_reference_geometry import _selection
from tests.unit.test_reference_measurement_selection import observation


def test_unverified_crossings_do_not_change_existing_border_styles():
    page = observation({"segmentIndex": 0, "status": "UNVERIFIABLE",
                        "reason": "NO_INTERIOR_PAINT_SAMPLES"})
    assert _selection(page) == [{"cellId": "explicit-cell", "sourceRange": "Z11:AA14",
                                 "sides": ["right"]}]


@pytest.mark.parametrize("status", ["observed", "zero", "missing"])
def test_existing_causal_success_or_absent_contribution_does_not_request_a_copy(status):
    page = observation({"segmentIndex": 0, "status": "UNVERIFIABLE",
                        "reason": "NO_INTERIOR_PAINT_SAMPLES"})
    difference = page[0]["strokeDifferential"][0]
    if status == "observed":
        difference.update(status="OBSERVED", changedFootprints=difference["paintFootprints"])
    elif status == "zero":
        difference["subpixelComponents"]["changedComponents"] = 0
    else:
        page[0]["strokeDifferential"].clear()
    assert _selection(page) == [{"cellId": "explicit-cell", "sourceRange": "Z11:AA14",
                                 "sides": ["right"]}]
