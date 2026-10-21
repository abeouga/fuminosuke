"""Per-item reading settings of a template series (2026-10-19).

An item is one field, or every row cell of a table column (the screen groups them).
Settings belong to the template series like the former candidate sets: saving does
not create a template version, and each new OCR job fixes the settings it was
accepted with in its snapshot (``templateFieldHints``). Accepted jobs never change.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, FieldError
from app.db.models import Template, TemplateFieldHint, TemplateVersion
from app.db.mutation import require_revision, touch_template_version
from app.db.session import Database
from app.domain.template_candidates import (
    MAX_FIELD_HINT_COUNT,
    MAX_FIELD_INSTRUCTION_LENGTH,
    MAX_FIELD_KEYWORD_COUNT,
    MAX_FIELD_KEYWORD_LENGTH,
    clean_candidate_text,
    normalize_candidate_target,
    normalize_candidate_text,
)
from app.domain.template_config import parse_template_config
from app.storage.local_storage import LocalStorageService

SNAPSHOT_KEY = "templateFieldHints"
SNAPSHOT_HASH_KEY = "templateFieldHintsHash"
MAX_TARGETS_PER_ITEM = 300


class TemplateFieldHintService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage

    def get_settings(self, template_id: str, template_version_id: str) -> dict[str, object]:
        with self._database.session() as session:
            _require_table(session)
            _require_template(session, template_id)
            version = _version(session, template_id, template_version_id)
            return _settings(session, template_id, version)

    def replace_settings(
        self,
        template_id: str,
        template_version_id: str,
        items: Sequence[Mapping[str, object]],
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        if len(items) > MAX_FIELD_HINT_COUNT:
            raise _invalid("items", f"設定できる項目は{MAX_FIELD_HINT_COUNT}件までです。")
        with self._database.transaction() as session:
            _require_table(session)
            _require_template(session, template_id)
            version = _version(session, template_id, template_version_id)
            require_revision(version, expected_revision)
            config = parse_template_config(
                self._storage.read_bytes(version.config_relative_path).decode("utf-8")
            )
            labels: dict[str, str] = {}
            for mapping in config.field_mappings:
                if mapping.enabled and mapping.mapping_type == "text":
                    target = normalize_candidate_target(mapping.target_path)
                    labels.setdefault(target, mapping.label)
            prepared = _prepare(items, labels)
            session.execute(
                delete(TemplateFieldHint).where(TemplateFieldHint.template_id == template_id)
            )
            session.flush()
            for item in prepared:
                session.add(TemplateFieldHint(template_id=template_id, **item))
            # Same invalidation as the former candidate settings: open proposals and
            # saves of every version of the series see a new revision.
            for affected in session.scalars(
                select(TemplateVersion).where(TemplateVersion.template_id == template_id)
            ):
                touch_template_version(affected)
            session.flush()
            return _settings(session, template_id, version)

    @staticmethod
    def snapshot(session: Session, template_id: str) -> dict[str, object]:
        if not inspect(session.connection()).has_table(TemplateFieldHint.__tablename__):
            return {SNAPSHOT_KEY: [], SNAPSHOT_HASH_KEY: None}
        hints = [
            {
                "targetPatterns": list(row.target_patterns_json),
                "label": row.field_label,
                "keywords": list(row.keywords_json),
                "keywordsEnabled": bool(row.keywords_enabled),
                "keywordsForced": bool(row.keywords_forced),
                "instruction": row.instruction,
            }
            for row in _rows(session, template_id)
        ]
        if not hints:
            return {SNAPSHOT_KEY: [], SNAPSHOT_HASH_KEY: None}
        canonical = json.dumps(
            hints, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return {SNAPSHOT_KEY: hints, SNAPSHOT_HASH_KEY: hashlib.sha256(canonical).hexdigest()}


def snapshot_hints(job_snapshot: Mapping[str, object]) -> dict[str, dict[str, Any]]:
    """Accepted settings by target pattern; malformed entries are ignored."""
    result: dict[str, dict[str, Any]] = {}
    raw = job_snapshot.get(SNAPSHOT_KEY, [])
    if not isinstance(raw, list):
        return result
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("targetPatterns"), list):
            continue
        keywords = tuple(
            value for value in item.get("keywords") or [] if isinstance(value, str) and value
        )
        instruction = item.get("instruction")
        hint = {
            "keywords": keywords if item.get("keywordsEnabled") is True else (),
            "forced": item.get("keywordsForced") is True,
            "instruction": instruction if isinstance(instruction, str) and instruction else None,
        }
        for target in item["targetPatterns"]:
            if isinstance(target, str):
                result.setdefault(target, hint)
    return result


def _settings(session: Session, template_id: str, version: TemplateVersion) -> dict[str, object]:
    return {
        "templateId": template_id,
        "resourceRevision": version.revision,
        "items": [
            {
                "targetPatterns": list(row.target_patterns_json),
                "label": row.field_label,
                "keywords": list(row.keywords_json),
                "keywordsEnabled": bool(row.keywords_enabled),
                "keywordsForced": bool(row.keywords_forced),
                "instruction": row.instruction or "",
            }
            for row in _rows(session, template_id)
        ],
    }


def _rows(session: Session, template_id: str) -> list[TemplateFieldHint]:
    return list(
        session.scalars(
            select(TemplateFieldHint)
            .where(TemplateFieldHint.template_id == template_id)
            .order_by(TemplateFieldHint.created_at, TemplateFieldHint.id)
        )
    )


def _prepare(
    items: Sequence[Mapping[str, object]], labels: Mapping[str, str]
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        prefix = f"items.{index}"
        raw_targets = item.get("targetPatterns")
        if (
            not isinstance(raw_targets, list)
            or not raw_targets
            or len(raw_targets) > MAX_TARGETS_PER_ITEM
        ):
            raise _invalid(f"{prefix}.targetPatterns", "設定する欄を選んでください。")
        targets: list[str] = []
        for raw_target in raw_targets:
            if not isinstance(raw_target, str):
                raise _invalid(f"{prefix}.targetPatterns", "設定する欄の指定が不正です。")
            target = normalize_candidate_target(raw_target)
            if target not in labels:
                raise _invalid(
                    f"{prefix}.targetPatterns", "このテンプレートの文字の欄を選んでください。"
                )
            if target in seen:
                raise _invalid(
                    f"{prefix}.targetPatterns", "一つの欄に二つの設定は付けられません。"
                )
            seen.add(target)
            targets.append(target)
        keywords = _keywords(item.get("keywords", []), f"{prefix}.keywords")
        raw_instruction = item.get("instruction") or ""
        if not isinstance(raw_instruction, str):
            raise _invalid(f"{prefix}.instruction", "読み取り指示は文字で入力してください。")
        instruction = raw_instruction.strip()
        if len(instruction) > MAX_FIELD_INSTRUCTION_LENGTH:
            raise _invalid(
                f"{prefix}.instruction",
                f"読み取り指示は{MAX_FIELD_INSTRUCTION_LENGTH}文字以内で入力してください。",
            )
        if not keywords and not instruction:
            continue  # nothing set: the item has no settings
        prepared.append(
            {
                "target_patterns_json": targets,
                "field_label": labels[targets[0]][:100],
                "keywords_json": keywords,
                "keywords_enabled": item.get("keywordsEnabled", True) is not False,
                "keywords_forced": item.get("keywordsForced") is True,
                "instruction": instruction or None,
            }
        )
    return prepared


def _keywords(raw: object, field: str) -> list[str]:
    if not isinstance(raw, list) or len(raw) > MAX_FIELD_KEYWORD_COUNT:
        raise _invalid(field, f"候補語は{MAX_FIELD_KEYWORD_COUNT}件以内で入力してください。")
    values: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            raise _invalid(f"{field}.{index}", "候補語は文字で入力してください。")
        if not value.strip():
            continue
        try:
            cleaned = clean_candidate_text(value, maximum=MAX_FIELD_KEYWORD_LENGTH)
        except ValueError:
            raise _invalid(
                f"{field}.{index}",
                f"候補語は1件{MAX_FIELD_KEYWORD_LENGTH}文字以内で入力してください。",
            ) from None
        normalized = normalize_candidate_text(cleaned)
        if normalized not in seen:
            seen.add(normalized)
            values.append(cleaned)
    return values


def _require_table(session: Session) -> None:
    if not inspect(session.connection()).has_table(TemplateFieldHint.__tablename__):
        raise AppError(
            status=503,
            code="FIELD_HINTS_NOT_MIGRATED",
            title="Database migration required",
            detail="項目の読み取り設定を使うにはデータベースの移行が必要です。",
        )


def _require_template(session: Session, template_id: str) -> Template:
    template = session.get(Template, template_id)
    if template is None:
        raise AppError(
            status=404,
            code="TEMPLATE_NOT_FOUND",
            title="Template not found",
            detail="指定されたテンプレートが見つかりません。",
        )
    return template


def _version(session: Session, template_id: str, version_id: str) -> TemplateVersion:
    version = session.get(TemplateVersion, version_id)
    if version is None or version.template_id != template_id:
        raise AppError(
            status=404,
            code="TEMPLATE_VERSION_NOT_FOUND",
            title="Template version not found",
            detail="指定されたテンプレートが見つかりません。",
        )
    return version


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        status=422,
        code="TEMPLATE_FIELD_HINTS_INVALID",
        title="Template field settings are invalid",
        detail=message,
        field_errors=[FieldError(field=field, message=message)],
    )
