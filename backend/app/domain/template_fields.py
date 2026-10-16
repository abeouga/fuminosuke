from __future__ import annotations

import hashlib
import json
import re
import unicodedata

MAX_TEMPLATE_FIELD_COUNT = 200
MAX_TEMPLATE_FIELD_LABEL_LENGTH = 100


def normalize_template_field_names(field_names: list[str]) -> list[str]:
    if len(field_names) > MAX_TEMPLATE_FIELD_COUNT:
        raise ValueError(f"保存する項目は{MAX_TEMPLATE_FIELD_COUNT}件以内で登録してください。")

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in field_names:
        name = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw_name).strip())
        if not name:
            raise ValueError("空の項目名は登録できません。")
        if len(name) > MAX_TEMPLATE_FIELD_LABEL_LENGTH:
            raise ValueError(
                f"項目名は{MAX_TEMPLATE_FIELD_LABEL_LENGTH}文字以内で入力してください。"
            )
        comparison_key = name.casefold()
        if comparison_key in seen:
            raise ValueError(f"同じ項目名が重複しています: {name}")
        seen.add(comparison_key)
        normalized.append(name)
    return normalized


def build_template_field_catalog(field_names: list[str]) -> list[dict[str, object]]:
    names = normalize_template_field_names(field_names)
    return [
        {
            "id": f"template_field_{index + 1}_{_field_digest(name)}",
            "label": name,
            "targetPath": f"fields.field_{_field_digest(name)}",
            "section": "この書類",
            "aliases": [],
            "source": "manual",
        }
        for index, name in enumerate(names)
    ]


def build_spreadsheet_field_catalog(field_names: list[str]) -> list[dict[str, object]]:
    safe_names: list[str] = []
    seen: set[str] = set()
    for raw_name in field_names:
        name = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw_name).strip())
        comparison_key = name.casefold()
        if (
            not name
            or len(name) > MAX_TEMPLATE_FIELD_LABEL_LENGTH
            or comparison_key in seen
        ):
            continue
        seen.add(comparison_key)
        safe_names.append(name)
        if len(safe_names) >= MAX_TEMPLATE_FIELD_COUNT:
            break
    return build_template_field_catalog(safe_names)




def _field_digest(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
