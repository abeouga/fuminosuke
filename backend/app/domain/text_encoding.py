from __future__ import annotations


# PaddleOCR's Japanese model can occasionally emit simplified-Chinese glyphs
# for visually similar Japanese form labels.  Keep this deliberately small and
# OCR-specific: CJK ideographs are shared by Unicode, so a generic
# "Chinese-character detector" would also reject valid Japanese kanji.
_PADDLE_JAPANESE_PHRASE_REPLACEMENTS = {
    "壳却": "売却",
}

_PADDLE_JAPANESE_GLYPH_TRANSLATION = str.maketrans(
    {
        "况": "況",
        "产": "産",
        "卖": "売",
        "价": "価",
        "备": "備",
        "历": "歴",
        "录": "録",
        "种": "種",
        "个": "個",
        "发": "発",
        "买": "買",
        "车": "車",
        "门": "門",
        "间": "間",
        "时": "時",
        "养": "養",
        "长": "長",
        "场": "場",
        "户": "戸",
        "证": "証",
        "认": "認",
        "读": "読",
        "实": "実",
        "应": "応",
        "统": "統",
        "图": "図",
    }
)


def repair_utf8_mojibake(value: str) -> str:
    """Repair UTF-8 bytes that were mistakenly decoded as Latin-1.

    Some Windows PaddleOCR builds return Japanese recognition text in this
    form. Only accept a conversion when it reduces suspicious Latin-1/control
    characters, so ordinary Japanese and ASCII user input remain untouched.
    """

    repaired = value
    for _ in range(2):
        try:
            candidate = repaired.encode("latin-1").decode("utf-8")
        except UnicodeError:
            break
        if _mojibake_score(candidate) >= _mojibake_score(repaired):
            break
        repaired = candidate
    return repaired


def normalize_paddle_japanese_text(value: str) -> str:
    """Normalize Chinese-shaped PaddleOCR mistakes to Japanese form text.

    This is applied only at the PaddleOCR boundary. User-entered text and
    persisted business data are not rewritten by this function.
    """

    normalized = repair_utf8_mojibake(value)
    for source, replacement in _PADDLE_JAPANESE_PHRASE_REPLACEMENTS.items():
        normalized = normalized.replace(source, replacement)
    return normalized.translate(_PADDLE_JAPANESE_GLYPH_TRANSLATION)


def _mojibake_score(value: str) -> int:
    return sum(1 for character in value if 0x80 <= ord(character) <= 0xFF)
