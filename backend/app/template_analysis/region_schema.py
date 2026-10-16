"""Constrain generation to issued region ownership without changing its answer model."""

from __future__ import annotations

import re
from typing import Any

from app.template_analysis.region_contracts import RegionAnswer

POLICY = "template-analysis.region-schema.v1"
_CELL = r"^c[1-9][0-9]{0,3}$"
_NEW = r"^n[1-9][0-9]{0,3}$"
_REGION = r"^[cn][1-9][0-9]{0,3}$"
_PATTERNS = {_CELL, _NEW, _REGION}
_LIMITS = dict(properties=5000, depth=10, strings=120000, enums=1000, largeEnumStrings=15000)
_DEFINITIONS = {
    "Add", "InputColumn", "InputField", "InputGroup", "InputSemantics", "InputTable",
    "LabelledInput", "Merge", "NewRectangle", "Rectangle", "RegionAnnotation",
    "RegionEvidence", "RegionHeading", "RegionProblem", "RegionReading", "RegionRow",
    "RegionUnlabelled", "RemoveDuplicate", "Resize", "Split",
}
# Paths identify typed reference slots in the canonical Pydantic schema. New slots
# require an explicit ownership decision rather than falling back to free strings.
_SLOTS: dict[tuple[str | int, ...], str] = {
    ("LabelledInput", "input"): "OwnedSemanticRef",
    ("LabelledInput", "labels", "items"): "EvidenceRef",
    ("RegionUnlabelled", "cell"): "OwnedSemanticRef",
    ("RegionAnnotation", "cell"): "OwnedSemanticRef",
    ("RegionHeading", "cell"): "OwnedSemanticRef",
    ("RegionHeading", "parent", "anyOf", 0): "EvidenceRef",
    ("RegionRow", "cells", "items"): "OwnedSemanticRef",
    ("RegionRow", "keyCell", "anyOf", 0): "OwnedSemanticRef",
    ("RegionProblem", "cell", "anyOf", 0): "OwnedSemanticRef",
    ("RegionReading", "evidenceRefs", "items"): "EvidenceRef",
    ("RegionEvidence", "refs", "items"): "EvidenceRef",
    ("InputGroup", "parent", "anyOf", 0): "EvidenceRef",
    ("InputTable", "parent", "anyOf", 0): "EvidenceRef",
    ("RemoveDuplicate", "cell"): "GeometryTargetRef",
    ("RemoveDuplicate", "survivors", "items"): "EvidenceRef",
    ("Resize", "cell"): "GeometryTargetRef",
    ("Split", "cell"): "GeometryTargetRef",
    ("Merge", "cells", "items"): "GeometryTargetRef",
    ("Merge", "ref"): "NewRef",
    ("Add", "ref"): "NewRef",
    ("NewRectangle", "ref"): "NewRef",
}
_ORDER = ("groups", "tables", "readings", "headings", "annotations", "unresolved")


def policy_parameters() -> dict[str, Any]:
    return dict(
        slots=[[list(path), kind] for path, kind in _SLOTS.items()],
        definitions=sorted(_DEFINITIONS), semanticOrder=list(_ORDER), limits=_LIMITS.copy(),
        existingRefPattern=_CELL, newRefPattern=_NEW,
    )


def _reference_paths(node: Any, path: tuple[str | int, ...] = ()) -> set[tuple[str | int, ...]]:
    if isinstance(node, dict):
        found = {path} if node.get("pattern") in _PATTERNS else set()
        for key, value in node.items():
            found.update(_reference_paths(value, (*path, key)))
        return found
    if isinstance(node, list):
        return set().union(*(_reference_paths(v, (*path, i)) for i, v in enumerate(node)))
    return set()


def _references(values: list[str]) -> dict[str, Any]:
    new = {"type": "string", "pattern": _NEW}
    return {"anyOf": [{"type": "string", "enum": values}, new]} if values else new


def _without_titles(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _without_titles(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_without_titles(v) for v in node]
    return node


def _prune_definitions(schema: dict[str, Any]) -> None:
    definitions = schema["$defs"]
    reached: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].removeprefix("#/$defs/")
                if name not in definitions:
                    raise ValueError("REGION_SCHEMA_SHAPE")
                if name not in reached:
                    reached.add(name)
                    visit(definitions[name])
            for key, value in node.items():
                if key != "$defs":
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    schema["$defs"] = {k: v for k, v in definitions.items() if k in reached}


def schema_budget(schema: dict[str, Any]) -> dict[str, int]:
    """Count shared definitions once; expand references only for nesting depth."""
    counts = dict(properties=0, depth=0, strings=0, enums=0)

    def count(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties", {})
            counts["properties"] += len(properties)
            counts["strings"] += sum(len(k) for k in properties)
            counts["strings"] += sum(len(k) for k in node.get("$defs", {}))
            if node.get("type") == "object" and (
                node.get("additionalProperties") is not False
                or set(node.get("required", [])) != set(properties)
            ):
                raise ValueError("REGION_SCHEMA_SHAPE")
            enum = node.get("enum", [])
            if "enum" in node and not enum:
                raise ValueError("REGION_SCHEMA_SHAPE")
            counts["enums"] += len(enum)
            size = sum(len(v) for v in enum if isinstance(v, str))
            counts["strings"] += size
            if len(enum) > 250 and size > _LIMITS["largeEnumStrings"]:
                raise ValueError("REGION_SCHEMA_LIMIT")
            if isinstance(node.get("const"), str):
                counts["strings"] += len(node["const"])
            for value in node.values():
                count(value)
        elif isinstance(node, list):
            for value in node:
                count(value)

    def depth(node: Any, level: int, chain: frozenset[str]) -> None:
        if isinstance(node, dict):
            level += node.get("type") in {"object", "array"}
            counts["depth"] = max(counts["depth"], level)
            if "$ref" in node:
                name = node["$ref"].removeprefix("#/$defs/")
                if name in chain or name not in schema["$defs"]:
                    raise ValueError("REGION_SCHEMA_SHAPE")
                depth(schema["$defs"][name], level, chain | {name})
            for key, value in node.items():
                if key != "$defs":
                    depth(value, level, chain)
        elif isinstance(node, list):
            for value in node:
                depth(value, level, chain)

    count(schema)
    depth(schema, 0, frozenset())
    if any(counts[k] > _LIMITS[k] for k in counts):
        raise ValueError("REGION_SCHEMA_LIMIT")
    return counts


def region_schema(metadata: dict[str, Any]) -> dict[str, Any]:
    schema = RegionAnswer.model_json_schema(by_alias=True)
    definitions = schema["$defs"]
    paths = {
        (name, key, *tail)
        for name, definition in definitions.items()
        for key, value in definition.get("properties", {}).items()
        for tail in _reference_paths(value)
    }
    if set(definitions) != _DEFINITIONS or paths != set(_SLOTS):
        raise ValueError("REGION_SCHEMA_SHAPE")
    visible = [c["ref"] for c in metadata["cells"]]
    owned = set(metadata["writeRefs"])
    readonly = set(metadata["readOnlyRefs"])
    editable = set(metadata["geometryEditable"])
    if (
        len(visible) != len(set(visible))
        or any(not isinstance(r, str) or not re.fullmatch(_CELL, r) for r in visible)
        or owned & readonly or owned | readonly != set(visible)
        or not editable <= set(visible)
    ):
        raise ValueError("REGION_SCHEMA_SCOPE")
    replacements = {
        "OwnedSemanticRef": _references(sorted(owned)),
        "EvidenceRef": _references(sorted(visible)),
        "NewRef": {"type": "string", "pattern": _NEW},
    }
    geometry = sorted(editable & owned)
    if geometry:
        replacements["GeometryTargetRef"] = {"type": "string", "enum": geometry}
    for path, kind in _SLOTS.items():
        node = definitions[path[0]]["properties"][path[1]]
        if len(path) == 2:
            definitions[path[0]]["properties"][path[1]] = {"$ref": "#/$defs/" + kind}
        else:
            for part in path[2:-1]:
                node = node[part]
            node[path[-1]] = {"$ref": "#/$defs/" + kind}
    definitions.update(replacements)
    if not geometry:
        schema["properties"]["geometry"]["items"] = {"$ref": "#/$defs/Add"}
    props = definitions["InputSemantics"]["properties"]
    definitions["InputSemantics"]["properties"] = {k: props[k] for k in _ORDER}
    definitions["InputSemantics"]["required"] = list(_ORDER)
    _prune_definitions(schema)
    result: dict[str, Any] = _without_titles(schema)
    schema_budget(result)
    return result
