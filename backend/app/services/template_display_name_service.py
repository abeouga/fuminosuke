"""Item names a person changes on the result screen; display only.

The template's own names (layout, mappings, OCR request labels) and every accepted
job snapshot stay as they are. The result screen shows these names over them.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import Template, TemplateDisplayName
from app.db.session import Database
from app.domain.field_targets import is_supported_field_path


class TemplateDisplayNameService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def names(self, template_id: str) -> dict[str, str]:
        with self._database.session() as session:
            rows = session.scalars(
                select(TemplateDisplayName).where(TemplateDisplayName.template_id == template_id)
            )
            return {row.target_path: row.display_name for row in rows}

    def rename(self, template_id: str, target_path: str, name: str | None) -> dict[str, str]:
        """Set the shown name of one item; an empty name goes back to the template's."""
        if not is_supported_field_path(target_path):
            raise AppError(
                status=422, code="DISPLAY_NAME_TARGET_INVALID", title="Invalid item",
                detail="名前を変える項目を確認できません。",
            )
        cleaned = " ".join((name or "").split())
        if len(cleaned) > 100:
            raise AppError(
                status=422, code="DISPLAY_NAME_TOO_LONG", title="Name too long",
                detail="項目名は100文字以内にしてください。",
            )
        with self._database.transaction() as session:
            if session.get(Template, template_id) is None:
                raise AppError(
                    status=404, code="TEMPLATE_NOT_FOUND", title="Template not found",
                    detail="テンプレートが見つかりません。",
                )
            row = session.scalar(
                select(TemplateDisplayName).where(
                    TemplateDisplayName.template_id == template_id,
                    TemplateDisplayName.target_path == target_path,
                )
            )
            if not cleaned:
                if row is not None:
                    session.delete(row)
            elif row is None:
                session.add(TemplateDisplayName(
                    template_id=template_id, target_path=target_path, display_name=cleaned,
                ))
            else:
                row.display_name = cleaned
        return self.names(template_id)
