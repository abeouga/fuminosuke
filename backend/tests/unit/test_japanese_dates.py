from app.domain.japanese_dates import (
    canonical_japanese_birth_date,
    format_japanese_era_date,
    infer_japanese_era,
    japanese_era_date_numbers,
)


def test_birth_date_ocr_is_reduced_to_three_numeric_groups() -> None:
    assert japanese_era_date_numbers("26 9 27 生") == "26 9 27"


def test_era_selection_converts_to_gregorian_japanese_display() -> None:
    assert format_japanese_era_date("26 9 27", "heisei") == "2014年9月27日"
    assert format_japanese_era_date("6 9 27", "reiwa") == "2024年9月27日"


def test_era_is_inferred_from_the_era_year_digit_count() -> None:
    assert canonical_japanese_birth_date("26 9 27 生", None) == (
        "26 9 27",
        "2014年9月27日",
        "",
    )
    assert canonical_japanese_birth_date("7 7 25", None) == (
        "7 7 25",
        "2025年7月25日",
        "",
    )
    assert infer_japanese_era("28 7 25") == "heisei"
    assert infer_japanese_era("7 7 25") == "reiwa"


def test_era_boundary_is_validated() -> None:
    assert format_japanese_era_date("31 5 1", "heisei") is None
    assert format_japanese_era_date("1 4 30", "reiwa") is None
