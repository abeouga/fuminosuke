"""Pure numeric syntax shared by the legacy and v2 adapters."""

import re
import unicodedata

_DIGITS = {
    "ゼロ": "0",
    "ぜろ": "0",
    "れい": "0",
    "レイ": "0",
    "零": "0",
    "\u3007": "0",
    "いち": "1",
    "イチ": "1",
    "一": "1",
    "に": "2",
    "ニ": "2",
    "二": "2",
    "さん": "3",
    "サン": "3",
    "三": "3",
    "よん": "4",
    "ヨン": "4",
    "四": "4",
    "ご": "5",
    "ゴ": "5",
    "五": "5",
    "ろく": "6",
    "ロク": "6",
    "六": "6",
    "なな": "7",
    "ナナ": "7",
    "しち": "7",
    "シチ": "7",
    "七": "7",
    "はち": "8",
    "ハチ": "8",
    "八": "8",
    "きゅう": "9",
    "キュウ": "9",
    "九": "9",
    **{str(value): str(value) for value in range(10)},
}
_TOKENS = sorted(_DIGITS, key=len, reverse=True)
_ZERO = ("ゼロ", "ぜろ", "零", "\u3007")
_NUMERALS = "0-9\u3007零一二三四五六七八九十百千万億兆"
_TRANSLATE = str.maketrans({char: str(index) for index, char in enumerate("零一二三四五六七八九")})
_TRANSLATE[ord("\u3007")] = "0"


def integer_quantity(value: str) -> str | None:
    """Read a complete integer quantity, rejecting units, quotes and invalid order."""
    text = unicodedata.normalize("NFKC", value)
    if len(text) > 64 or not re.fullmatch(f"[{_NUMERALS}]+", text):
        return None
    if not re.search("[十百千万億兆]", text):
        return None

    def section(part: str) -> int | None:
        total, digits, last_unit = 0, "", 10000
        for char in part.translate(_TRANSLATE):
            if char in "0123456789":
                digits += char
            elif char in "十百千":
                unit = {"十": 10, "百": 100, "千": 1000}[char]
                if unit >= last_unit:
                    return None
                total += (int(digits) if digits else 1) * unit
                digits, last_unit = "", unit
            else:
                return None
        return total + (int(digits) if digits else 0)

    total, offset, last_unit = 0, 0, 10**16
    for match in re.finditer("[万億兆]", text):
        unit = {"万": 10**4, "億": 10**8, "兆": 10**12}[match[0]]
        if unit >= last_unit or (match.start() == offset and offset):
            return None
        part = section(text[offset : match.start()]) if match.start() > offset else 1
        if part is None:
            return None
        total += part * unit
        offset, last_unit = match.end(), unit
    tail = section(text[offset:])
    return str(total + tail) if tail is not None else None


def quantity_value(value: str, instruction: str) -> str:
    quantity = integer_quantity(value)
    if quantity is None:
        return value
    source = unicodedata.normalize("NFKC", instruction).rstrip(" 、。\n")
    literal = unicodedata.normalize("NFKC", value)
    if f"「{literal}」" in source or f'"{literal}"' in source:
        return value
    # A quantity must be the entire explicit value, not a substring of 6万円 or 「6万」.
    pattern = rf"(?<![{_NUMERALS}])([{_NUMERALS}]+)\s*(?=(?:に|へ)(?:変更|直して|設定|して)|$)"
    return (
        quantity
        if any(integer_quantity(match[1]) == quantity for match in re.finditer(pattern, source))
        else value
    )


def digit_sequence(value: str, instruction: str) -> str:
    """Keep text/names intact unless the value explicitly contains a spoken zero.

    A spoken zero is the unambiguous numeric cue for this path. Do not infer that
    names such as ナナ or ordinary Japanese quantities should become identifiers.
    Every character must belong to a digit token; no partial replacement occurs.
    """
    text = unicodedata.normalize("NFKC", value)
    source = unicodedata.normalize("NFKC", instruction)
    if not any(token in text for token in _ZERO) or not any(token in source for token in _ZERO):
        return value
    digits = []
    offset = 0
    while offset < len(text):
        token = next((item for item in _TOKENS if text.startswith(item, offset)), None)
        if token is None:
            return value
        digits.append(_DIGITS[token])
        offset += len(token)
    return "".join(digits)
