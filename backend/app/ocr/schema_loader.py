import json
from pathlib import Path
from typing import Any


def load_output_schema() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "templates"
        / "cow-breeding-ledger-v1"
        / "output-schema.json"
    )
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return value

