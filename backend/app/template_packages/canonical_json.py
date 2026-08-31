from __future__ import annotations

import json
import math
import unicodedata
from enum import Enum
from typing import Any

from pydantic import BaseModel


class CanonicalJsonError(ValueError):
    pass


def canonical_json_bytes(value: object) -> bytes:
    normalized = _normalize_json_value(value, path="$")
    try:
        serialized = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalJsonError("Value cannot be represented as canonical JSON") from exc
    return serialized.encode("utf-8")


def _normalize_json_value(value: object, *, path: str) -> Any:
    if isinstance(value, BaseModel):
        return _normalize_json_value(
            value.model_dump(mode="json", by_alias=True, exclude_none=False),
            path=path,
        )
    if isinstance(value, Enum):
        return _normalize_json_value(value.value, path=path)
    if value is None or isinstance(value, str | bool | int):
        return unicodedata.normalize("NFC", value) if isinstance(value, str) else value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalJsonError(f"Non-finite number at {path}")
        return 0.0 if value == 0 else value
    if isinstance(value, list | tuple):
        return [
            _normalize_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise CanonicalJsonError(f"Non-string object key at {path}")
            key = unicodedata.normalize("NFC", raw_key)
            if key in normalized:
                raise CanonicalJsonError(f"Duplicate object key after NFC normalization at {path}")
            normalized[key] = _normalize_json_value(item, path=f"{path}.{key}")
        return normalized
    raise CanonicalJsonError(f"Unsupported JSON value at {path}: {type(value).__name__}")
