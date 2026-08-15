from __future__ import annotations


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


def _mojibake_score(value: str) -> int:
    return sum(1 for character in value if 0x80 <= ord(character) <= 0xFF)
