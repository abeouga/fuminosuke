"""How the documents of a template series are named (2026-10-21).

The template's own ``documentName`` is set while it is made; afterwards a person changes it
in a small dialog without making a new version. The saved rule only proposes the name on
the review screen (the person still types or confirms it); readings, mappings and accepted
snapshots are unchanged. A path the job's template does not have is left out of the name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, inspect, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, FieldError
from app.db.models import Template, TemplateDocumentName, TemplateVersion
from app.db.session import Database
from app.domain.field_targets import is_supported_field_path
from app.domain.template_config import TemplateConfig, parse_template_config
from app.storage.local_storage import LocalStorageService

MAX_FIELDS = 20


def document_name_rule(session: Session, template_version_id: object) -> dict[str, Any] | None:
    """The rule saved for the series of a version, or None (empty before the migration)."""
    if not isinstance(template_version_id, str) or not inspect(session.connection()).has_table(
        TemplateDocumentName.__tablename__
    ):
        return None
    version = session.get(TemplateVersion, template_version_id)
    if version is None:
        return None
    row = session.scalar(
        select(TemplateDocumentName).where(TemplateDocumentName.template_id == version.template_id)
    )
    return dict(row.rule_json) if row else None


class TemplateDocumentNameService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage

    def settings(self, template_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            _require_table(session)
            template = _require_template(session, template_id)
            config = self._config(session, template)
            row = session.scalar(
                select(TemplateDocumentName).where(TemplateDocumentName.template_id == template_id)
            )
            own = (
                config.document_name.model_dump(mode="json", by_alias=True)
                if config and config.document_name else None
            )
            identity = (
                [field.target_path for field in config.document_identity_fields] if config else []
            )
            candidates: list[dict[str, Any]] = [
                {
                    "targetPath": m.target_path, "label": m.label,
                    "identity": m.target_path in identity,
                }
                for m in (config.field_mappings if config else [])
                if m.enabled and m.mapping_type == "text" and is_supported_field_path(m.target_path)
            ]
            candidates.sort(key=lambda c: (not c["identity"],
                                           identity.index(str(c["targetPath"])) if c["identity"] else 0))
            return {
                "rule": dict(row.rule_json) if row else own,
                "templateRule": own,
                "custom": row is not None,
                "candidates": candidates,
            }

    def replace(self, template_id: str, rule: Mapping[str, Any] | None) -> dict[str, Any]:
        cleaned = _validate(rule) if rule is not None else None
        with self._database.transaction() as session:
            _require_table(session)
            _require_template(session, template_id)
            session.execute(
                delete(TemplateDocumentName).where(TemplateDocumentName.template_id == template_id)
            )
            if cleaned is not None:
                session.add(TemplateDocumentName(template_id=template_id, rule_json=cleaned))
        return self.settings(template_id)

    def _config(self, session: Session, template: Template) -> TemplateConfig | None:
        version = session.get(TemplateVersion, template.active_version_id) if (
            template.active_version_id
        ) else session.scalar(
            select(TemplateVersion).where(TemplateVersion.template_id == template.id)
            .order_by(TemplateVersion.version_number.desc()).limit(1)
        )
        if version is None:
            return None
        try:
            return parse_template_config(
                self._storage.read_bytes(version.config_relative_path).decode("utf-8")
            )
        except Exception:
            return None


def _validate(rule: Mapping[str, Any]) -> dict[str, Any]:
    paths = [str(path).strip() for path in rule.get("fieldPaths") or []]
    if not paths:
        raise _invalid("fieldPaths", "ファイル名に入れる欄を1つ以上選んでください。")
    if len(paths) > MAX_FIELDS or len(set(paths)) != len(paths):
        raise _invalid(
            "fieldPaths", f"ファイル名に入れる欄は{MAX_FIELDS}個まで、重複なしで選んでください。"
        )
    if not all(is_supported_field_path(path) for path in paths):
        raise _invalid("fieldPaths", "選べない欄が含まれています。")
    texts = {}
    for key, limit in (("prefix", 100), ("separator", 20), ("suffix", 100)):
        value = str(rule.get(key) or "")
        if len(value) > limit:
            raise _invalid(key, f"{limit}文字以内にしてください。")
        texts[key] = value
    return {"fieldPaths": paths, **texts}


def _invalid(field: str, detail: str) -> AppError:
    return AppError(
        status=422, code="DOCUMENT_NAME_INVALID", title="Invalid document name rule",
        detail=detail, field_errors=[FieldError(field=field, message=detail)],
    )


def _require_table(session: Session) -> None:
    if not inspect(session.connection()).has_table(TemplateDocumentName.__tablename__):
        raise AppError(
            status=503, code="DOCUMENT_NAMES_NOT_MIGRATED", title="Database migration required",
            detail="ファイル名の付け方を変えるにはデータベースの移行が必要です。",
        )


def _require_template(session: Session, template_id: str) -> Template:
    template = session.get(Template, template_id)
    if template is None:
        raise AppError(
            status=404, code="TEMPLATE_NOT_FOUND", title="Template not found",
            detail="指定されたテンプレートが見つかりません。",
        )
    return template
