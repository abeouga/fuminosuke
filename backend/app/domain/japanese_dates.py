from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Literal

JapaneseEra = Literal["heisei", "reiwa"]

_WESTERN_DATE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$")


def japanese_era_date_numbers(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    groups = re.findall(r"\d+", normalized)
    if len(groups) != 3:
        return None
    era_year, month, day = (int(item) for item in groups)
    if era_year < 1 or not _valid_month_day(month, day):
        return None
    return f"{era_year} {month} {day}"


def format_japanese_era_date(
    value: str | None,
    era: JapaneseEra,
) -> str | None:
    numeric = japanese_era_date_numbers(value)
    if numeric is None:
        return None
    era_year, month, day = (int(item) for item in numeric.split())
    western_year = era_year + (1988 if era == "heisei" else 2018)
    try:
        converted = date(western_year, month, day)
    except ValueError:
        return None
    if era == "heisei" and not (date(1989, 1, 8) <= converted <= date(2019, 4, 30)):
        return None
    if era == "reiwa" and converted < date(2019, 5, 1):
        return None
    return f"{converted.year}年{converted.month}月{converted.day}日"


def infer_japanese_era(value: str | None) -> JapaneseEra | None:
    if value is None:
        return None
    groups = re.findall(r"\d+", unicodedata.normalize("NFKC", value))
    if len(groups) != 3:
        return None
    if len(groups[0]) == 1:
        return "reiwa"
    if len(groups[0]) == 2:
        return "heisei"
    return None


def canonical_japanese_birth_date(
    raw: str | None,
    normalized: str | None,
) -> tuple[str | None, str | None, str]:
    numeric = japanese_era_date_numbers(raw)
    if numeric is None:
        sanitized = _numeric_groups(raw)
        return sanitized, None, "BIRTH_DATE_NUMBERS_INVALID"
    year, month, day = (int(item) for item in numeric.split())
    if year >= 1868:
        try:
            converted = date(year, month, day)
        except ValueError:
            return numeric, None, "BIRTH_DATE_NUMBERS_INVALID"
        return (
            numeric,
            f"{converted.year}年{converted.month}月{converted.day}日",
            "",
        )
    canonical_normalized = canonical_western_date(normalized)
    if canonical_normalized and canonical_normalized in {
        format_japanese_era_date(numeric, "heisei"),
        format_japanese_era_date(numeric, "reiwa"),
    }:
        return numeric, canonical_normalized, ""
    inferred_era = infer_japanese_era(raw)
    inferred_normalized = (
        format_japanese_era_date(numeric, inferred_era) if inferred_era is not None else None
    )
    if inferred_normalized is not None:
        return numeric, inferred_normalized, ""
    return (
        numeric,
        None,
        ("BIRTH_DATE_ERA_REQUIRED" if inferred_era is None else "BIRTH_DATE_NUMBERS_INVALID"),
    )


def canonical_western_date(value: str | None) -> str | None:
    if value is None:
        return None
    match = _WESTERN_DATE.fullmatch(unicodedata.normalize("NFKC", value).strip())
    if match is None:
        return None
    year, month, day = (int(item) for item in match.groups())
    try:
        converted = date(year, month, day)
    except ValueError:
        return None
    return f"{converted.year}年{converted.month}月{converted.day}日"


def _numeric_groups(value: str | None) -> str | None:
    if value is None:
        return None
    groups = re.findall(r"\d+", unicodedata.normalize("NFKC", value))
    return " ".join(str(int(item)) for item in groups) if groups else None


def _valid_month_day(month: int, day: int) -> bool:
    try:
        date(2000, month, day)
    except ValueError:
        return False
    return True
