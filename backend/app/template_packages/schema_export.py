from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from app.template_packages.canonical_models import (
    CanonicalOutputLayout,
    CanonicalTemplate,
    CanonicalTemplateQuality,
)
from app.template_packages.manifest_models import TemplatePackageManifest

SCHEMA_DIRECTORY = Path(__file__).with_name("schemas")
SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "manifest-v2.schema.json": TemplatePackageManifest,
    "template-v2.schema.json": CanonicalTemplate,
    "quality-v1.schema.json": CanonicalTemplateQuality,
    "output-layout-v1.schema.json": CanonicalOutputLayout,
}


def render_schemas() -> dict[str, str]:
    return {
        file_name: json.dumps(
            model.model_json_schema(by_alias=True, mode="serialization"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
        for file_name, model in SCHEMA_MODELS.items()
    }


def write_schemas() -> None:
    SCHEMA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for file_name, content in render_schemas().items():
        (SCHEMA_DIRECTORY / file_name).write_text(
            content,
            encoding="utf-8",
            newline="\n",
        )


def schemas_are_current() -> bool:
    return all(
        (SCHEMA_DIRECTORY / file_name).is_file()
        and (SCHEMA_DIRECTORY / file_name).read_text(encoding="utf-8") == content
        for file_name, content in render_schemas().items()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate template package JSON Schemas")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return 0 if schemas_are_current() else 1
    write_schemas()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
