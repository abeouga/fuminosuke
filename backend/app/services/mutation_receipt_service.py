from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError, FieldError
from app.db.models import utc_now
from app.db.mutation_models import MutationRequest, ResourceOperation
from app.db.session import Database


class MutationReceiptService:
    """A durable reservation followed by one atomic business transaction.

    PENDING means no successful commit has been recorded. While execution owns
    SQLite's write lock another caller waits, then observes the same receipt.
    A crash before commit rolls back the business update and leaves PENDING;
    a crash after commit leaves SUCCEEDED. Never mint a new key to resolve a timeout.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _conflict() -> AppError:
        return AppError(
            status=409,
            code="IDEMPOTENCY_KEY_CONFLICT",
            title="Idempotency conflict",
            detail="同じ操作キーを別の要求に使用できません。",
        )

    @staticmethod
    def fingerprint(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()

    def get(self, key: str, owner_id: str) -> dict[str, Any] | None:
        with self.database.session() as session:
            row = session.get(MutationRequest, key)
            if row is None or row.owner_id != owner_id:
                return None
            return {
                "key": row.key,
                "state": row.state,
                "result": row.result_json,
                "error": row.error_json,
            }

    @staticmethod
    def _stored_error(row: MutationRequest) -> AppError:
        stored = dict(row.error_json or {})
        stored["field_errors"] = [
            FieldError.model_validate(item) for item in stored.get("field_errors", [])
        ]
        return AppError(**stored)

    def run(
        self,
        *,
        key: str,
        owner_id: str,
        resource_kind: str,
        resource_id: str,
        payload: dict[str, Any],
        apply: Callable[[Session], dict[str, Any]],
        prepare: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if not key or len(key) > 128 or not owner_id or len(owner_id) > 100:
            raise self._conflict()
        request_hash = self.fingerprint(
            {
                "contractVersion": 1,
                "resourceKind": resource_kind,
                "resourceId": resource_id,
                "payload": payload,
            }
        )
        with self.database.transaction() as session:
            row = session.get(MutationRequest, key)
            if row is None:
                session.add(
                    MutationRequest(
                        key=key,
                        owner_id=owner_id,
                        resource_kind=resource_kind,
                        resource_id=resource_id,
                        request_hash=request_hash,
                    )
                )
            elif row.owner_id != owner_id or row.request_hash != request_hash:
                raise self._conflict()
        try:
            if prepare is not None:
                # Editing publishes immutable config outside the write lock.
                # Terminal retries must not prepare against a newer revision.
                with self.database.session() as session:
                    row = session.get(MutationRequest, key)
                    terminal = row is not None and row.state != "PENDING"
                if not terminal:
                    prepare()
            with self.database.transaction() as session:
                row = session.get(MutationRequest, key)
                if row is None or row.owner_id != owner_id or row.request_hash != request_hash:
                    raise self._conflict()
                # Receipt lookup precedes any current resource/revision validation.
                if row.state == "SUCCEEDED":
                    return row.result_json or {}
                if row.state == "FAILED":
                    raise self._stored_error(row)
                result = apply(session)
                row.state, row.result_json, row.completed_at = "SUCCEEDED", result, utc_now()
                session.flush()
                return result
        except AppError as error:
            # Business validation failed without committing. Record a terminal
            # failure in a separate transaction; never overwrite a concurrent success.
            with self.database.transaction() as session:
                row = session.get(MutationRequest, key)
                if row is not None and row.state == "SUCCEEDED":
                    return row.result_json or {}
                if row is not None and row.state == "FAILED":
                    raise self._stored_error(row) from None
                elif row is not None and row.state == "PENDING":
                    row.state, row.completed_at = "FAILED", utc_now()
                    row.error_json = {
                        "status": error.status,
                        "code": error.code,
                        "title": error.title,
                        "detail": error.detail,
                        "job_id": error.job_id,
                        "field_errors": [
                            item.model_dump(mode="json") for item in error.field_errors
                        ],
                        **({"metadata": error.metadata} if error.metadata else {}),
                    }
            raise

    @staticmethod
    def record_operation(
        session: Session,
        *,
        request_key: str,
        resource_kind: str,
        resource_id: str,
        operation: str,
        before_revision: int,
        after_revision: int,
        before: dict[str, Any],
        after: dict[str, Any],
        inverse: dict[str, Any] | None = None,
        undo_of_id: str | None = None,
    ) -> ResourceOperation:
        record = ResourceOperation(
            request_key=request_key,
            resource_kind=resource_kind,
            resource_id=resource_id,
            operation=operation,
            before_revision=before_revision,
            after_revision=after_revision,
            before_json=before,
            after_json=after,
            inverse_json=inverse,
            undo_of_id=undo_of_id,
        )
        session.add(record)
        session.flush()
        return record
