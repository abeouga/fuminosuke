from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldTarget:
    path: str
    label: str
    section: str


FIELD_PREFIX = "fields."
FIELD_KEY_PATTERN = re.compile(r"^[^\s.\[\]]{1,100}$")


def field_key(path: str) -> str | None:
    if not path.startswith(FIELD_PREFIX):
        return None
    key = path.removeprefix(FIELD_PREFIX)
    return key if FIELD_KEY_PATTERN.fullmatch(key) else None


def is_supported_field_path(path: str) -> bool:
    return field_key(path) is not None
