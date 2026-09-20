"""Integer arithmetic fixtures are not evidence of real Office qualification."""
import math
from copy import deepcopy

import pytest

from app.domain import calc_print_grid as grid


def test_fixed_export_roundtrip_identifies_lossless_twips_and_rejects_overflow_range():
    for twips in range(1, 65536):
        mm100 = math.floor(twips * 127 / 72 + .5)
        points = round(mm100 / 2540, 4) * 72
        if mm100 <= 65535:
            assert grid.exported_twips(points) == twips
        else:
            with pytest.raises(ValueError):
                grid.exported_twips(points)


@pytest.mark.parametrize("value", [20, 20.01, 11.6, 37154 / 20])
def test_approximate_or_overflowing_lengths_are_not_fixed_export_evidence(value):
    with pytest.raises(ValueError):
        grid.exported_twips(value)


@pytest.mark.parametrize("value", [True, None, "20", -1, float("nan"), float("inf"),
                                  3277, 20.025])
def test_invalid_or_ambiguous_exported_lengths_are_rejected(value):
    with pytest.raises(ValueError):
        grid.exported_twips(value)


@pytest.mark.parametrize("twips,expected", [(0, 1), (1, 1), (72, 127), (232, 409),
                                          (390, 687), (403, 710), (65535, 65535)])
def test_each_visible_dimension_is_truncated_and_clamped_before_accumulation(twips, expected):
    assert grid.grid_step(twips) == expected
    assert grid.grid_step(twips, hidden=True) == 0


def test_hidden_dimensions_and_source_order_are_not_compacted_or_reassigned():
    args = ([0, 11.592, 20.1528, 20.1528], [1])
    before = deepcopy(args)
    result = grid.dimension_grid(*args)
    assert args == before
    assert result["twips"] == [0, 232, 403, 403]
    assert result["stepsMm100"] == [0, 409, 710, 710]
    assert result["boundariesMm100"] == [0, 0, 409, 1119, 1829]
    assert result["productionQualified"] is False
    assert result["boundariesMm100"][-1] != math.floor(sum(result["twips"]) * 127 / 72)


@pytest.mark.parametrize("lengths,hidden", [([], []), ([0], []), ([1], [1]),
    ([1], [0]), ([1], [2]), ([1], [True]), ([0], [1, 1]), ([0, 0], [2, 1]),
    ([.001], []), ([1] * 20001, [])])
def test_invalid_dimension_identity_or_visibility_is_rejected(lengths, hidden):
    with pytest.raises(ValueError):
        grid.dimension_grid(lengths, hidden)


def test_page_origin_keeps_margin_integer_divisions_and_inclusive_extent():
    assert grid.frame_origin(11906, 1020, 1020, 13800, 69, centered=True) == 3045
    assert grid.frame_origin(16838, 1077, 1077, 20943, 69, centered=False) == 2750
    assert grid.frame_origin(100, 0, 0, 100, 100, centered=False) == -1
    assert grid.frame_origin(100, 0, 0, 98, 100, centered=True) == 0


@pytest.mark.parametrize("args", [(True, 0, 0, 0, 100), (0, 0, 0, 0, 100),
    (100, -1, 0, 1, 100), (100, 50, 50, 1, 100), (100, 0, 0, 101, 100),
    (100, 0, 0, 1, 9), (100, 0, 0, 1, 101), (100, 0, 0, 1, 69.0)])
def test_unsupported_origin_inputs_are_not_silently_adjusted(args):
    with pytest.raises(ValueError):
        grid.frame_origin(*args, centered=False)
