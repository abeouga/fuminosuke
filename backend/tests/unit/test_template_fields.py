import pytest

from app.domain.template_fields import (
    build_template_field_catalog,
    normalize_template_field_names,
)


def test_builds_a_stable_template_scoped_catalog_without_cattle_defaults() -> None:
    first = build_template_field_catalog(["宛名", "金額"])
    second = build_template_field_catalog(["宛名", "金額"])

    assert first == second
    assert [item["label"] for item in first] == ["宛名", "金額"]
    assert all(str(item["targetPath"]).startswith("customFields.field_") for item in first)
    assert all(item["section"] == "この書類" for item in first)


def test_rejects_duplicate_names_after_unicode_and_whitespace_normalization() -> None:
    with pytest.raises(ValueError, match="重複"):
        normalize_template_field_names(["車台番号", "  車台番号  "])
