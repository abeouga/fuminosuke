from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

PLAIN_INTEGER = re.compile(r"^\d+$")
COMMA_GROUPED_INTEGER = re.compile(r"^\d{1,3}(?:,\d{3})+$")
DOT_GROUPED_INTEGER = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
HYPHEN_GROUPED_INTEGER = re.compile(r"^\d{1,3}[-‐‑‒–—−]\d{3}$")
PLAIN_DECIMAL = re.compile(r"^\d+(?:\.\d+)?$")
COMMA_GROUPED_DECIMAL = re.compile(r"^\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def normalize_integer(value: str | None, *, grouped: bool = False) -> str | None:
    compact = _compact(value)
    if compact is None:
        return None
    if PLAIN_INTEGER.fullmatch(compact):
        return compact
    if grouped and (
        COMMA_GROUPED_INTEGER.fullmatch(compact) or DOT_GROUPED_INTEGER.fullmatch(compact)
    ):
        return compact.replace(",", "").replace(".", "")
    return None


def normalize_yen(value: str | None) -> str | None:
    compact = _compact(value)
    if compact is None:
        return None
    compact = compact.removesuffix("円").strip()
    if HYPHEN_GROUPED_INTEGER.fullmatch(compact):
        return re.sub(r"[-‐‑‒–—−]", "", compact)
    return normalize_integer(compact, grouped=True)


def normalize_decimal(value: str | None) -> str | None:
    compact = _compact(value)
    if compact is None:
        return None
    if PLAIN_DECIMAL.fullmatch(compact):
        return compact
    if COMMA_GROUPED_DECIMAL.fullmatch(compact):
        return compact.replace(",", "")
    return None


def decimal_value(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _compact(value: str | None) -> str | None:
    if value is None:
        return None
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    return compact or None
