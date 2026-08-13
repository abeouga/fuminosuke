from app.domain.japanese_dates import (
    canonical_japanese_birth_date,
    format_japanese_era_date,
    japanese_era_date_numbers,
)


def test_birth_date_ocr_is_reduced_to_three_numeric_groups() -> None:
    assert japanese_era_date_numbers("26 9 27 生") == "26 9 27"


def test_era_selection_converts_to_gregorian_japanese_display() -> None:
    assert format_japanese_era_date("26 9 27", "heisei") == "2014年9月27日"
    assert format_japanese_era_date("6 9 27", "reiwa") == "2024年9月27日"


def test_birth_date_requires_an_era_until_normalized_value_matches() -> None:
    assert canonical_japanese_birth_date("26 9 27 生", None) == (
        "26 9 27",
        None,
        "BIRTH_DATE_ERA_REQUIRED",
    )
    assert canonical_japanese_birth_date("26 9 27", "2014年9月27日") == (
        "26 9 27",
        "2014年9月27日",
        "",
    )


def test_era_boundary_is_validated() -> None:
    assert format_japanese_era_date("31 5 1", "heisei") is None
    assert format_japanese_era_date("1 4 30", "reiwa") is None

