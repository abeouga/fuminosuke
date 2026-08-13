from app.domain.sale_notes import canonical_sale_note


def test_sale_note_keeps_a_valid_sex_symbol_and_suffix() -> None:
    assert canonical_sale_note("♂双子", None) == "♂双子"
    assert canonical_sale_note("♀", None) == "♀"


def test_matched_symbol_replaces_a_misread_first_character() -> None:
    assert canonical_sale_note("る", "♂") == "♂"
    assert canonical_sale_note("ト双子", "♀") == "♀双子"


def test_misread_without_a_matched_symbol_is_not_guessed() -> None:
    assert canonical_sale_note("チ", None) is None

