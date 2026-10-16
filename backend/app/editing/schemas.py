"""Generate HTTP, strict tools and TypeScript from shared request/response types."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.editing.contracts import (
    Batch,
    BatchResult,
    CatalogResult,
    CloseResult,
    ContextResult,
    ContextUpdate,
    EditingProblem,
    EditingStatus,
    EmptyArgs,
    HistoryResult,
    ReceiptArgs,
    ReceiptResult,
    SessionCreate,
    SessionResult,
    TargetSearch,
    ToolBatch,
    Undo,
)
from app.editing.manual_contracts import ManualEdit, ManualReceipt, ManualResult, ManualView
from app.editing.registry import OPERATIONS
from app.editing.schema_tools import strict_schema as strict_schema
from app.editing.schema_tools import typescript as typescript


def tool_definitions(
    *, capabilities: tuple[str, ...] | None = None, paged: bool = False
) -> list[dict[str, Any]]:
    definitions = (
        (
            "get_editing_context",
            "Get the current resource revision, finite capabilities and catalog. No mutation.",
            EmptyArgs,
        ),
        (
            "find_targets",
            "Find existing targets by names, aliases, tableName/tableId and 1-based row/column. "
            "Clarify multiple matches.",
            TargetSearch,
        ),
        (
            "apply_edit_batch",
            "Apply one atomic edit group using current handles. No arbitrary paths or layouts.",
            ToolBatch,
        ),
        (
            "undo_edit",
            "Undo the latest group. The controller binds its ID and revision.",
            EmptyArgs,
        ),
        (
            "get_edit_receipt",
            "Read a prior request receipt; never create a different request after a timeout.",
            ReceiptArgs,
        ),
    )
    result: list[dict[str, Any]] = [
        {
            "type": "function",
            "name": name,
            "description": description,
            "strict": True,
            "parameters": strict_schema(model),
        }
        for name, description, model in definitions
    ]
    schema = result[2]["parameters"]
    variants = schema["properties"]["operations"]["items"]["anyOf"]
    available = set(OPERATIONS if capabilities is None else capabilities)
    schema["properties"]["operations"]["items"]["anyOf"] = [
        ref
        for ref in variants
        if schema["$defs"][ref["$ref"].split("/")[-1]]["properties"]["op"]["enum"][0] in available
    ]
    if paged:
        schema["$defs"]["MarkSetSpec"]["properties"]["recognitionMode"]["enum"] = ["checklist"]
        schema["$defs"]["MarkSetSpec"]["properties"]["selectionMode"]["enum"] = ["multiple"]
    if not schema["properties"]["operations"]["items"]["anyOf"]:
        return [d for d in result if d["name"] not in {"apply_edit_batch", "undo_edit"}]
    used: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, dict):
            if "$ref" in value:
                key = value["$ref"].split("/")[-1]
                if key not in used:
                    used.add(key)
                    collect(schema["$defs"][key])
            for child in value.values():
                collect(child)

    collect(schema["properties"])
    schema["$defs"] = {key: value for key, value in schema["$defs"].items() if key in used}
    return result


def export(output: Path) -> None:
    from app.assistant.conversation_contracts import (
        ConversationStart,
        ResolveTurn,
        StartedTurn,
        TurnResolution,
        TurnStatus,
    )

    output.mkdir(parents=True, exist_ok=True)
    models = (
        SessionCreate,
        ContextUpdate,
        TargetSearch,
        Batch,
        Undo,
        SessionResult,
        ContextResult,
        CatalogResult,
        BatchResult,
        ReceiptResult,
        HistoryResult,
        EditingStatus,
        CloseResult,
        EditingProblem,
        ConversationStart,
        ResolveTurn,
        StartedTurn,
        TurnResolution,
        TurnStatus,
    )
    manifest: dict[str, Any] = {
        "contractVersion": "editing.v1",
        "operations": list(OPERATIONS),
        "http": {},
        "manualHttp": {
            model.__name__: model.model_json_schema(by_alias=True)
            for model in (ManualEdit, ManualView, ManualResult, ManualReceipt)
        },
    }
    declarations = []
    written: set[str] = set()
    for model in models:
        schema = model.model_json_schema(by_alias=True)
        manifest["http"][model.__name__] = schema
        # Keep each generated alias once across the shared definitions.
        for line in typescript(schema).splitlines():
            if line not in written:
                written.add(line)
                declarations.append(line)
    (output / "editing-http.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "editing-tools.json").write_text(
        json.dumps(tool_definitions(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "editing-contracts.ts").write_text("\n".join(declarations) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    export(parser.parse_args().output)
