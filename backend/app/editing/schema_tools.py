from __future__ import annotations

import copy
import json
from functools import cache
from typing import Any

from pydantic import BaseModel


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    # Callers may mutate the result, so hand out a copy of the cached schema.
    return copy.deepcopy(_strict_schema(model))


@cache
def _strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = copy.deepcopy(model.model_json_schema(by_alias=True))

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key in ("title", "default", "discriminator"):
                value.pop(key, None)
            if "const" in value:
                value["enum"] = [value.pop("const")]
            if "oneOf" in value:
                value["anyOf"] = value.pop("oneOf")
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)

    visit(schema)
    return schema


def typescript(schema: dict[str, Any]) -> str:
    def expression(node: dict[str, Any]) -> str:
        if not node:
            return "unknown"
        if "$ref" in node:
            return str(node["$ref"]).split("/")[-1]
        if "const" in node:
            return json.dumps(node["const"])
        if "enum" in node:
            return " | ".join(json.dumps(v, ensure_ascii=False) for v in node["enum"])
        if "anyOf" in node or "oneOf" in node:
            return " | ".join(expression(v) for v in node.get("anyOf", node.get("oneOf", [])))
        kind = node.get("type")
        if kind == "object":
            if node.get("additionalProperties") is True:
                return "Record<string, unknown>"
            if isinstance(node.get("additionalProperties"), dict):
                return f"Record<string, {expression(node['additionalProperties'])}>"
            required = set(node.get("required", []))
            properties = [
                f"{json.dumps(k)}{'' if k in required else '?'}: {expression(v)};"
                for k, v in node.get("properties", {}).items()
            ]
            return "{ " + " ".join(properties) + " }"
        if kind == "array":
            return f"Array<{expression(node['items'])}>"
        return {
            "integer": "number",
            "number": "number",
            "string": "string",
            "boolean": "boolean",
            "null": "null",
        }.get(str(kind), "never")

    rows = ["// Generated from app.editing.contracts; do not edit by hand."]
    for name, definition in schema.get("$defs", {}).items():
        rows.append(f"export type {name} = {expression(definition)};")
    rows.append(f"export type {schema['title']} = {expression(schema)};")
    return "\n".join(rows) + "\n"
