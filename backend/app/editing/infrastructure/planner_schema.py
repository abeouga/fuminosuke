"""Model-only selectors and evidence; business operations are generated from shared DTOs."""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from pydantic import Field

from app.editing.operation_contracts import Contract, Operation
from app.editing.schema_tools import strict_schema


class Operations(Contract):
    operations: list[Operation] = Field(min_length=1, max_length=50)


_SHAPE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["change"]},
                        "change": {"$ref": "#/$defs/ChangePlan"},
                    },
                    "required": ["kind", "change"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["clarify"]},
                        "knownChange": {
                            "anyOf": [{"$ref": "#/$defs/ChangePlan"}, {"type": "null"}]
                        },
                        "missing": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {
                                        "type": "string",
                                        "enum": [
                                            "target",
                                            "table",
                                            "row",
                                            "column",
                                            "value",
                                            "meaning",
                                            "name",
                                            "operation",
                                            "scope",
                                        ],
                                    },
                                    "operationIndex": {
                                        "anyOf": [
                                            {"type": "integer", "minimum": 0, "maximum": 49},
                                            {"type": "null"},
                                        ]
                                    },
                                    "selectorId": {
                                        "anyOf": [
                                            {"type": "string", "pattern": "^s[1-9][0-9]{0,3}$"},
                                            {"type": "null"},
                                        ]
                                    },
                                    "focusQuote": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 1000,
                                    },
                                    "candidateRefs": {
                                        "type": "array",
                                        "items": {
                                            "type": "string",
                                            "pattern": "^[A-Za-z][A-Za-z0-9_]{0,39}$",
                                        },
                                        "minItems": 0,
                                        "maxItems": 5,
                                    },
                                },
                                "required": [
                                    "kind",
                                    "operationIndex",
                                    "selectorId",
                                    "focusQuote",
                                    "candidateRefs",
                                ],
                                "additionalProperties": False,
                            },
                            "minItems": 1,
                            "maxItems": 10,
                        },
                    },
                    "required": ["kind", "knownChange", "missing"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["undo_previous"]},
                        "evidence": {"$ref": "#/$defs/Evidence"},
                    },
                    "required": ["kind", "evidence"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["retarget_previous"]},
                        "selector": {"$ref": "#/$defs/Selector"},
                        "evidence": {"$ref": "#/$defs/Evidence"},
                    },
                    "required": ["kind", "selector", "evidence"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["no_action"]},
                        "reason": {
                            "type": "string",
                            "enum": ["not_an_edit", "cancel_pending", "unclear_instruction"],
                        },
                    },
                    "required": ["kind", "reason"],
                    "additionalProperties": False,
                },
            ]
        }
    },
    "required": ["decision"],
    "additionalProperties": False,
    "$defs": {
        "TargetRef": {
            "type": "object",
            "properties": {"targetRef": {"type": "string", "pattern": "^s[1-9][0-9]{0,3}$"}},
            "required": ["targetRef"],
            "additionalProperties": False,
        },
        "Evidence": {
            "type": "object",
            "properties": {
                "turnId": {"type": "string", "minLength": 1, "maxLength": 128},
                "quote": {"type": "string", "minLength": 1, "maxLength": 4000},
            },
            "required": ["turnId", "quote"],
            "additionalProperties": False,
        },
        "ValueIntent": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["text"]},
                        "text": {"type": "string", "minLength": 1, "maxLength": 500},
                        "evidence": {"$ref": "#/$defs/Evidence"},
                    },
                    "required": ["kind", "text", "evidence"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["digits", "integer"]},
                        "spoken": {"type": "string", "minLength": 1, "maxLength": 500},
                        "evidence": {"$ref": "#/$defs/Evidence"},
                    },
                    "required": ["kind", "spoken", "evidence"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["blank"]},
                        "evidence": {"$ref": "#/$defs/Evidence"},
                    },
                    "required": ["kind", "evidence"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {"kind": {"type": "string", "enum": ["missing"]}},
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            ]
        },
        "Selector": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "pattern": "^s[1-9][0-9]{0,3}$"},
                "address": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["catalog"]},
                                "ref": {
                                    "type": "string",
                                    "pattern": "^[A-Za-z][A-Za-z0-9_]{0,39}$",
                                },
                                "evidence": {"$ref": "#/$defs/Evidence"},
                            },
                            "required": ["kind", "ref", "evidence"],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["query"]},
                                "targetKind": {
                                    "type": "string",
                                    "enum": [
                                        "cell",
                                        "field",
                                        "meaning",
                                        "relation",
                                        "heading",
                                        "record_section",
                                        "record_group",
                                        "mark_set",
                                        "mark_option",
                                        "region",
                                        "identity_group",
                                        "page",
                                    ],
                                },
                                "mention": {"type": "string", "minLength": 1, "maxLength": 300},
                                "heading": {
                                    "anyOf": [
                                        {"type": "string", "minLength": 1, "maxLength": 100},
                                        {"type": "null"},
                                    ]
                                },
                                "pageRef": {
                                    "anyOf": [
                                        {
                                            "type": "string",
                                            "pattern": "^[A-Za-z][A-Za-z0-9_]{0,39}$",
                                        },
                                        {"type": "null"},
                                    ]
                                },
                                "position": {
                                    "anyOf": [
                                        {
                                            "type": "string",
                                            "enum": ["top", "bottom", "left", "right"],
                                        },
                                        {"type": "null"},
                                    ]
                                },
                                "evidence": {"$ref": "#/$defs/Evidence"},
                            },
                            "required": [
                                "kind",
                                "targetKind",
                                "mention",
                                "heading",
                                "pageRef",
                                "position",
                                "evidence",
                            ],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["record_cell"]},
                                "tableRef": {
                                    "anyOf": [
                                        {
                                            "type": "string",
                                            "pattern": "^[A-Za-z][A-Za-z0-9_]{0,39}$",
                                        },
                                        {"type": "null"},
                                    ]
                                },
                                "tableMention": {
                                    "anyOf": [
                                        {"type": "string", "minLength": 1, "maxLength": 100},
                                        {"type": "null"},
                                    ]
                                },
                                "row": {"type": "integer", "minimum": 1},
                                "column": {
                                    "anyOf": [
                                        {
                                            "type": "object",
                                            "properties": {
                                                "kind": {"type": "string", "enum": ["index"]},
                                                "index": {"type": "integer", "minimum": 1},
                                            },
                                            "required": ["kind", "index"],
                                            "additionalProperties": False,
                                        },
                                        {
                                            "type": "object",
                                            "properties": {
                                                "kind": {"type": "string", "enum": ["name"]},
                                                "name": {
                                                    "type": "string",
                                                    "minLength": 1,
                                                    "maxLength": 100,
                                                },
                                            },
                                            "required": ["kind", "name"],
                                            "additionalProperties": False,
                                        },
                                        {
                                            "type": "object",
                                            "properties": {
                                                "kind": {"type": "string", "enum": ["catalog"]},
                                                "ref": {
                                                    "type": "string",
                                                    "pattern": "^[A-Za-z][A-Za-z0-9_]{0,39}$",
                                                },
                                            },
                                            "required": ["kind", "ref"],
                                            "additionalProperties": False,
                                        },
                                    ]
                                },
                                "evidence": {"$ref": "#/$defs/Evidence"},
                            },
                            "required": [
                                "kind",
                                "tableRef",
                                "tableMention",
                                "row",
                                "column",
                                "evidence",
                            ],
                            "additionalProperties": False,
                        },
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["recent_target"]},
                                "evidence": {"$ref": "#/$defs/Evidence"},
                            },
                            "required": ["kind", "evidence"],
                            "additionalProperties": False,
                        },
                    ]
                },
            },
            "required": ["id", "address"],
            "additionalProperties": False,
        },
        "ChangePlan": {
            "type": "object",
            "properties": {
                "selectors": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Selector"},
                    "minItems": 0,
                    "maxItems": 1000,
                },
                "operations": {
                    "type": "array",
                    "items": {
                        "anyOf": [
                            {"$ref": "#/$defs/OcrSetValue"},
                            {"$ref": "#/$defs/CellSetRole"},
                            {"$ref": "#/$defs/CellRename"},
                            {"$ref": "#/$defs/CellSetReading"},
                            {"$ref": "#/$defs/CellMerge"},
                            {"$ref": "#/$defs/CellDelete"},
                            {"$ref": "#/$defs/MeaningCreate"},
                            {"$ref": "#/$defs/MeaningUpdate"},
                            {"$ref": "#/$defs/MeaningDelete"},
                            {"$ref": "#/$defs/MeaningBind"},
                            {"$ref": "#/$defs/MeaningUnbind"},
                            {"$ref": "#/$defs/HeadingCreate"},
                            {"$ref": "#/$defs/HeadingUpdate"},
                            {"$ref": "#/$defs/HeadingAttach"},
                            {"$ref": "#/$defs/HeadingDetach"},
                            {"$ref": "#/$defs/HeadingDelete"},
                            {"$ref": "#/$defs/RecordsDefine"},
                            {"$ref": "#/$defs/RecordsReplaceRows"},
                            {"$ref": "#/$defs/RecordsConfigure"},
                            {"$ref": "#/$defs/RecordsDissolve"},
                            {"$ref": "#/$defs/MarkConfigure"},
                            {"$ref": "#/$defs/MarkClear"},
                            {"$ref": "#/$defs/IdentityConfigure"},
                            {"$ref": "#/$defs/DocumentNameConfigure"},
                        ]
                    },
                    "minItems": 1,
                    "maxItems": 50,
                },
                "operationEvidence": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/Evidence"},
                    "minItems": 1,
                    "maxItems": 50,
                },
            },
            "required": ["selectors", "operations", "operationEvidence"],
            "additionalProperties": False,
        },
    },
}


@lru_cache(maxsize=1)
def planner_schema() -> dict[str, Any]:
    schema = copy.deepcopy(_SHAPE)
    definitions = strict_schema(Operations)["$defs"]

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            if value.get("pattern") == r"^t_[a-f0-9]{64}$":
                value.clear()
                value["$ref"] = "#/$defs/TargetRef"
                return
            for child in value.values():
                visit(child)

    visit(definitions)
    definitions["OcrSetValue"]["properties"]["value"] = {"$ref": "#/$defs/ValueIntent"}
    schema["$defs"].update(definitions)
    return schema
