"""Numbers are value syntax. Labels, manual edits and existing data never enter here."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.editing.domain.numbers import digit_sequence, integer_quantity
from app.editing.domain.planning import InvalidPlan, PlanningIssue, check_evidence

_KANJI = str.maketrans({c: str(n) for n, c in enumerate("零一二三四五六七八九")})
_KANJI[ord("\u3007")] = "0"


def numeric_literal(value: str) -> str | None:
    text = unicodedata.normalize("NFKC", value).strip()
    if re.fullmatch(r"[0-9零\u3007一二三四五六七八九]+", text):
        return text.translate(_KANJI)
    digits = digit_sequence(text, text)
    if digits != text and digits.isascii() and digits.isdecimal():
        return digits
    return integer_quantity(text)


def ordinal(value: str) -> int | None:
    text = numeric_literal(value)
    return int(text) if text and 0 < int(text) <= 9999 else None


def literal_value(value: str, quote: str) -> str:
    # A quoted string is literal even when its content looks numeric.
    if any(left + value + right in quote for left, right in (("「", "」"), ('"', '"'))):
        return value
    return numeric_literal(value) or value


_AMOUNT_NAMES = ("価格", "金額", "代金", "料金", "単価", "費用", "円")


def fit_value(value: str | None, target: dict[str, Any]) -> str | None:
    """六万円 / 1,200 become digits only for a field that holds a number (価格=540000).

    A note field (備考) keeps 6万円 as written.
    """
    digits = amount(value) if value else None
    if digits is None:
        return value
    current = str(target.get("currentValue") or "")
    cell = target.get("recordCell") or {}
    names = str(target.get("name") or "") + str(cell.get("columnName") or "")
    numeric = bool(re.fullmatch(r"[0-9,]+", current)) or any(w in names for w in _AMOUNT_NAMES)
    return digits if numeric else value


def amount(value: str) -> str | None:
    """A spoken amount: 六万円 / 6万円 / 1,200円 / 1,200 as plain digits, else None.

    Other units (kg, 頭, キロ) are not amounts and stay text.
    """
    text = unicodedata.normalize("NFKC", value).strip()
    text = re.sub(r"\s*円$", "", text)
    if re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3})+", text):
        text = text.replace(",", "")
    if text == unicodedata.normalize("NFKC", value).strip():
        return None
    return numeric_literal(text)


def resolve_value(intent: dict[str, Any], turns: dict[str, str], clause: str) -> str | None:
    kind = intent["kind"]
    if kind == "missing":
        raise PlanningIssue("value")
    quote = check_evidence(intent["evidence"], turns)
    if quote not in clause:
        raise InvalidPlan("Value evidence belongs to another operation")
    if kind == "blank":
        if not any(word in quote for word in ("空欄", "空白", "消去", "消して", "削除")):
            raise InvalidPlan("Blank value needs explicit erasure evidence")
        return None
    source = intent["text"] if kind == "text" else intent["spoken"]
    if source not in quote:
        raise InvalidPlan("Value was generated rather than quoted")
    result = literal_value(source, clause)
    if kind in {"digits", "integer"} and not result.isascii():
        raise InvalidPlan("Unsupported numeric expression")
    if kind in {"digits", "integer"} and not result.isdecimal():
        raise InvalidPlan("Numeric intent contains non-digit content")
    # The source must be the complete token: 6万 is not evidence for 6万kg.
    for match in re.finditer(re.escape(source), clause):
        suffix = clause[match.end() :]
        prefix = clause[: match.start()]
        if (
            not suffix
            or re.match(r'[\s。、,」"\n]|(?:に|へ)(?:変更|修正|直|設定|して|する)', suffix)
        ) and (
            not prefix
            or re.search(r'[\s「"をは:]$', prefix)
            or (
                numeric_literal(source) is not None
                and not re.search(
                    r"[0-9\uFF10-\uFF19零\u3007一二三四五六七八九十百千万億兆]$", prefix
                )
            )
        ):
            return result
    raise InvalidPlan("Value evidence is only a substring of the instructed value")
