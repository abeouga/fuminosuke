"""Generate v2 HTTP and planner contracts from shared operation definitions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import Field, RootModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.v2.editing_contracts import (
    CommandRequest,
    CommandResult,
    ContextResult,
    ContextUpdate,
    Event,
    ManualAuthoring,
    ManualOcr,
    ScopeAnswer,
    ScopeSnapshot,
    SessionCreate,
    SessionResult,
)
from app.api.v2.voice_contracts import Control
from app.editing.infrastructure.planner_schema import planner_schema
from app.editing.operation_contracts import Contract, Operation
from app.editing.schema_tools import strict_schema, typescript


class Operations(Contract):
    operations: list[Operation] = Field(min_length=1, max_length=50)


class ContextTransport(RootModel[ContextUpdate | ScopeSnapshot]):
    pass


class EditingV2Transport(
    RootModel[
        SessionCreate
        | SessionResult
        | ContextUpdate
        | ContextResult
        | CommandRequest
        | CommandResult
        | ManualAuthoring
        | ManualOcr
        | Event
        | ScopeAnswer
    ]
):
    pass


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    output = root / "docs/contracts/editing-v2"
    for name, model in (
        ("operations", Operations),
        ("commands", CommandRequest),
        ("events", Event),
        ("context", ContextTransport),
        ("http", EditingV2Transport),
    ):
        value = model.model_json_schema(by_alias=True) if name == "http" else strict_schema(model)
        value["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        (output / f"{name}.schema.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    value = EditingV2Transport.model_json_schema(by_alias=True)
    planner = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$comment": (
            "Generated model-only plan. Shared operations are bound and validated before saving."
        ),
        **planner_schema(),
    }
    (output / "planner.schema.json").write_text(
        json.dumps(planner, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    types = typescript(value).replace("app.editing.contracts", "app.api.v2.editing_contracts")
    (root / "frontend/src/editing/v2.generated.ts").write_text(
        types, encoding="utf-8", newline="\n"
    )
    voice = Control.json_schema(by_alias=True)
    voice["title"] = "VoiceV2Control"
    (output / "voice.schema.json").write_text(
        json.dumps(
            {"$schema": "https://json-schema.org/draft/2020-12/schema", **voice},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "frontend/src/editing/voice-v2.generated.ts").write_text(
        typescript(voice), encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
