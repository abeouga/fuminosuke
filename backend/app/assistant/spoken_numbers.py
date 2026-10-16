"""Normalize explicit numeric expressions only inside voice value commands."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

from app.editing.contracts import OcrSetValue, ToolBatch
from app.editing.domain.numbers import (
    digit_sequence as digit_sequence,
)
from app.editing.domain.numbers import (
    integer_quantity as integer_quantity,
)
from app.editing.domain.numbers import (
    quantity_value as quantity_value,
)


def normalize_values(
    batch: ToolBatch, instruction: str,
    value_source: Callable[[str], str | None] | None = None,
) -> ToolBatch:
    # Mutate a private copy before the controller freezes its command digest.
    result = batch.model_copy(deep=True)
    for operation in result.operations:
        if isinstance(operation, OcrSetValue) and operation.value is not None:
            source = (value_source(operation.target) if value_source else None) or instruction
            literal = unicodedata.normalize("NFKC", operation.value)
            normalized_source = unicodedata.normalize("NFKC", source)
            if f"「{literal}」" in normalized_source or f'"{literal}"' in normalized_source:
                continue
            operation.value = quantity_value(
                digit_sequence(operation.value, source), source
            )
    return result
