from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def validate_package_path(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    if not normalized or len(normalized) > 240:
        raise ValueError("Package paths must contain between 1 and 240 characters")
    if normalized != value:
        raise ValueError("Package paths must use NFC Unicode normalization")
    if "\\" in normalized or ":" in normalized or _CONTROL_CHARACTERS.search(normalized):
        raise ValueError("Package paths contain a forbidden character")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Package paths must be normalized relative POSIX paths")
    for part in path.parts:
        if part.endswith((" ", ".")):
            raise ValueError("Package path segments cannot end with a space or period")
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise ValueError("Package paths cannot use Windows reserved names")
    return normalized


def path_collision_key(value: str) -> str:
    return unicodedata.normalize("NFC", validate_package_path(value)).casefold()
