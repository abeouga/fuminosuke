"""Translate compiler-issued identities without rewriting labels or option values.

Mark identities are scoped to their physical cell and set. This map is built
only from ManualCompiler substitutions; it cannot grant editing capabilities.
"""

from __future__ import annotations

import json
from typing import Any


class DraftIdentityMap:
    def __init__(self, substitutions: dict[str, str]) -> None:
        self.references: dict[str, str] = {}
        self.sets: dict[tuple[str, ...], str] = {}
        self.options: dict[tuple[str, ...], str] = {}
        for source, target in substitutions.items():
            if source.startswith(("mark_set:", "mark_option:")):
                kind, encoded = source.split(":", 1)
                parts = json.loads(encoded)
                size = 2 if kind == "mark_set" else 3
                if not isinstance(parts, list) or len(parts) != size or any(
                    not isinstance(p, str) for p in parts
                ):
                    raise ValueError("DRAFT_IDENTITY_INVALID")
                (self.sets if size == 2 else self.options)[tuple(parts)] = target
            else:
                self.references[source] = target
        self.reverse_references = self._invert(self.references)
        self.reverse_sets = {
            (self.references.get(cell, cell), target): source
            for (cell, source), target in self.sets.items()
        }
        self.reverse_options = {
            (self.references.get(cell, cell), self.sets.get((cell, mark), mark), target): source
            for (cell, mark, source), target in self.options.items()
        }
        if (
            len(self.reverse_sets) != len(self.sets)
            or len(self.reverse_options) != len(self.options)
        ):
            raise ValueError("DRAFT_IDENTITY_AMBIGUOUS")

    @staticmethod
    def _invert(values: dict[str, str]) -> dict[str, str]:
        inverse = {v: k for k, v in values.items()}
        if len(inverse) != len(values):
            raise ValueError("DRAFT_IDENTITY_AMBIGUOUS")
        return inverse

    def apply(self, value: Any, *, reverse: bool = False) -> Any:
        from app.editing.manual_compiler import REFERENCE_FIELDS

        refs = self.reverse_references if reverse else self.references
        sets = self.reverse_sets if reverse else self.sets
        options = self.reverse_options if reverse else self.options
        reference_fields = REFERENCE_FIELDS | {"targetPath", "fieldPaths"}

        def mark(value: dict[str, Any], cell: str) -> dict[str, Any]:
            result = {k: visit(v, k) for k, v in value.items()}
            result["id"] = sets.get((cell, value["id"]), value["id"])
            result["options"] = [
                {
                    **{k: visit(v, k) for k, v in option.items()},
                    "id": options.get((cell, value["id"], option["id"]), option["id"]),
                }
                for option in value["options"]
            ]
            return result

        def visit(value: Any, field: str = "") -> Any:
            if isinstance(value, list):
                return [visit(v, field) for v in value]
            if isinstance(value, dict):
                return {
                    k: [mark(m, value["id"]) for m in v]
                    if k == "markSets" and "id" in value else visit(v, k)
                    for k, v in value.items()
                }
            if isinstance(value, str) and field in reference_fields:
                return refs.get(value, value)
            return value

        return visit(value)
