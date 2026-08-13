from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, FieldError
from app.db.models import SireName
from app.db.session import Database
from app.domain.sire_names import (
    MAX_SIRE_NAME_POOL_SIZE,
    SIRE_NAME_ALLOWED_SUFFIXES,
    clean_sire_name,
    normalize_sire_name,
)


class SireNameService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get_pool(self) -> dict[str, object]:
        with self._database.session() as session:
            items = list(
                session.scalars(
                    select(SireName).order_by(SireName.created_at, SireName.id)
                )
            )
            return {
                "items": [self._view(item) for item in items],
                "count": len(items),
                "allowedSuffixes": list(SIRE_NAME_ALLOWED_SUFFIXES),
            }

    def create(self, value: str) -> dict[str, object]:
        try:
            name = clean_sire_name(value)
            normalized_name = normalize_sire_name(name)
        except ValueError as exc:
            raise AppError(
                status=422,
                code="INVALID_SIRE_NAME",
                title="Invalid sire name",
                detail="種雄牛名を1〜100文字で入力してください。",
                field_errors=[
                    FieldError(field="name", message="種雄牛名を入力してください。")
                ],
            ) from exc
        with self._database.session() as session:
            count = session.scalar(select(func.count()).select_from(SireName)) or 0
            if count >= MAX_SIRE_NAME_POOL_SIZE:
                raise AppError(
                    status=409,
                    code="SIRE_NAME_POOL_LIMIT_REACHED",
                    title="Sire name pool limit reached",
                    detail=f"種雄牛名は最大{MAX_SIRE_NAME_POOL_SIZE}件まで登録できます。",
                )
            item = SireName(name=name, normalized_name=normalized_name)
            session.add(item)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise AppError(
                    status=409,
                    code="SIRE_NAME_ALREADY_EXISTS",
                    title="Sire name already exists",
                    detail=f"「{name}」はすでに登録されています。",
                ) from exc
            session.refresh(item)
            return self._view(item)

    def delete(self, sire_name_id: str) -> None:
        with self._database.session() as session:
            item = session.get(SireName, sire_name_id)
            if item is None:
                raise AppError(
                    status=404,
                    code="SIRE_NAME_NOT_FOUND",
                    title="Sire name not found",
                    detail="指定された種雄牛名が見つかりません。",
                )
            session.delete(item)
            session.commit()

    @staticmethod
    def snapshot_names(session: Session) -> list[str]:
        return list(
            session.scalars(
                select(SireName.name).order_by(SireName.created_at, SireName.id)
            )
        )

    @staticmethod
    def _view(item: SireName) -> dict[str, object]:
        return {
            "id": item.id,
            "name": item.name,
            "createdAt": item.created_at,
        }
