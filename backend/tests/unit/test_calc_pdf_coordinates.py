from fractions import Fraction

import pytest

from app.domain.calc_pdf_coordinates import (
    point_millipoints,
    representation_bounds,
    round_away,
)


@pytest.mark.parametrize(('value', 'expected'), [
    (Fraction(0), 0), (Fraction(1, 2), 1), (Fraction(-1, 2), -1),
    (Fraction(3, 2), 2), (Fraction(-3, 2), -2),
    (Fraction(499, 1000), 0), (Fraction(501, 1000), 1),
    (Fraction(-499, 1000), 0), (Fraction(-501, 1000), -1),
])
def test_cpp_ties_are_not_python_bankers_rounding(value, expected):
    assert round_away(value) == expected


def test_exact_units_and_page_height_truncation_are_separate():
    # One inch at 50%, and an independently specified fractional page height.
    assert point_millipoints(2540, 2540, 50, Fraction(8000009, 10000)) == (36000, 764000)


@pytest.mark.parametrize('dpi', [96, 150, 300])
def test_source_derived_bounds_over_all_integer_percentage_phases(dpi):
    bounds = representation_bounds(dpi)
    # 127 mm100 phases cover the denominator of the exact point conversion.
    # Include negative coordinates rather than concealing clipping candidates.
    for zoom in range(10, 101):
        for coordinate in range(-127, 128):
            for page_height in (Fraction(800), Fraction(8000009, 10000)):
                x, y = point_millipoints(coordinate, coordinate, zoom, page_height)
                continuous = Fraction(coordinate * zoom * 72, 100 * 2540)
                actual_x = Fraction(x, 1000)
                actual_y = page_height - Fraction(y, 1000)
                assert abs(actual_x - continuous) <= Fraction(1, 2000)
                assert abs(actual_y - continuous) < Fraction(3, 2000)
                raster_x = actual_x * dpi / 72
                raster_y = actual_y * dpi / 72
                saved_edge = round(continuous * dpi / 72)
                assert abs(raster_x - saved_edge) <= bounds['xTotalPx']
                assert abs(raster_y - saved_edge) < bounds['yTotalPxExclusive']


@pytest.mark.parametrize('args', [
    (True, 0, 100, Fraction(800)), (0.0, 0, 100, Fraction(800)),
    (0, False, 100, Fraction(800)), (0, 0, True, Fraction(800)),
    (0, 0, 9, Fraction(800)), (0, 0, 101, Fraction(800)),
    (0, 0, 100, 800.0), (0, 0, 100, Fraction(0)),
    (0, 0, 100, Fraction(-800)), (2**50, 0, 100, Fraction(800)),
    (0, -(2**50), 100, Fraction(800)),
    (0, 2540 * 2**31, 100, Fraction(72 * 2**31)),
    (0, 0, 100, Fraction(1, 10000)),
])
def test_invalid_or_overflowing_coordinates_fail_closed(args):
    with pytest.raises(ValueError):
        point_millipoints(*args)


@pytest.mark.parametrize('dpi', [True, 150.0, 0, 72, 600])
def test_unknown_dpi_is_not_generalized(dpi):
    with pytest.raises(ValueError):
        representation_bounds(dpi)


def test_no_implicit_float_coordinate_conversion():
    with pytest.raises(ValueError):
        round_away(.5)
