from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.base import Base
from app.db.models import DocumentFolder, LedgerDocument, Template
from app.db.session import Database
from app.services.document_library_service import DocumentLibraryService


class _DriveHookStub:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[tuple[object, ...]] = []

    def _call(self, name: str, *values: object) -> None:
        self.calls.append((name, *values))
        if self.fail_on == name:
            raise RuntimeError(f"{name} failed")

    def mirror_library_folder(
        self,
        folder_id: str,
        expected_subject: str | None = None,
    ) -> dict[str, int]:
        del expected_subject
        self._call("create", folder_id)
        return {"mirrored": 1, "rootCount": 1}

    def rename_mirrored_library_folder(
        self,
        folder_id: str,
        name: str,
        expected_subject: str | None = None,
    ) -> None:
        del expected_subject
        self._call("rename", folder_id, name)

    def delete_mirrored_library_folder(
        self,
        folder_id: str,
        expected_subject: str | None = None,
    ) -> None:
        del expected_subject
        self._call("delete", folder_id)

    def move_library_document(
        self,
        document_id: str,
        folder_id: str | None,
        *,
        expected_version: int | None = None,
        expected_subject: str | None = None,
    ) -> None:
        del expected_version, expected_subject
        self._call("move", document_id, folder_id)

    def sync_document_library(
        self,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        del expected_subject
        self._call("sync")
        return {"status": "SYNCED", "conflicts": 0}

    def push_document_library_to_drive(
        self,
        expected_subject: str | None = None,
    ) -> dict[str, Any]:
        del expected_subject
        self._call("push")
        return {
            "mode": "LIBRARY_TO_DRIVE",
            "status": "SYNCED",
            "conflicts": 0,
        }


def _database(settings_factory: Callable[..., Settings]) -> Database:
    database = Database(settings_factory().database)
    Base.metadata.create_all(database.engine)
    return database


def _folder(database: Database, name: str = "変更前") -> str:
    with database.session() as session:
        folder = DocumentFolder(
            name=name,
            normalized_name=name.casefold(),
        )
        session.add(folder)
        session.commit()
        return folder.id


def _document(database: Database) -> str:
    with database.session() as session:
        template = Template(name="テスト", document_type="test")
        session.add(template)
        session.flush()
        document = LedgerDocument(
            template_id=template.id,
            individual_id="1234567890",
            current_document_json={},
        )
        session.add(document)
        session.commit()
        return document.id


def test_folder_create_keeps_stable_local_id_when_drive_mirror_fails(
    settings_factory: Callable[..., Settings],
) -> None:
    database = _database(settings_factory)
    try:
        service = DocumentLibraryService(
            database,
            google_drive=_DriveHookStub(fail_on="create"),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="create failed"):
            service.create_folder(name="作成失敗", parent_id=None)

        with database.session() as session:
            folder = session.query(DocumentFolder).one()
            assert folder.name == "作成失敗"
    finally:
        database.dispose()


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_folder_mutation_does_not_advance_local_db_when_drive_fails(
    settings_factory: Callable[..., Settings],
    operation: str,
) -> None:
    database = _database(settings_factory)
    try:
        folder_id = _folder(database)
        service = DocumentLibraryService(
            database,
            google_drive=_DriveHookStub(fail_on=operation),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match=f"{operation} failed"):
            if operation == "rename":
                service.rename_folder(folder_id, name="変更後")
            else:
                service.delete_folder(folder_id)

        with database.session() as session:
            folder = session.get(DocumentFolder, folder_id)
            assert folder is not None
            assert folder.name == "変更前"
    finally:
        database.dispose()


def test_document_move_does_not_advance_version_when_drive_fails(
    settings_factory: Callable[..., Settings],
) -> None:
    database = _database(settings_factory)
    try:
        folder_id = _folder(database)
        document_id = _document(database)
        service = DocumentLibraryService(
            database,
            google_drive=_DriveHookStub(fail_on="move"),  # type: ignore[arg-type]
        )

        with pytest.raises(RuntimeError, match="move failed"):
            service.move_document(document_id, folder_id=folder_id, expected_version=1)

        with database.session() as session:
            document = session.get(LedgerDocument, document_id)
            assert document is not None
            assert document.library_folder_id is None
            assert document.library_location_version == 1
    finally:
        database.dispose()


def test_sync_delegates_to_drive_and_route_exposes_result(
    client: TestClient,
) -> None:
    drive = _DriveHookStub()
    service = DocumentLibraryService(
        client.app.state.database,
        google_drive=drive,  # type: ignore[arg-type]
    )

    assert service.sync_document_library() == {"status": "SYNCED", "conflicts": 0}
    client.app.state.document_library_service = service
    response = client.post("/api/v1/document-library/sync")

    assert response.status_code == 200
    assert response.json() == {"status": "SYNCED", "conflicts": 0}
    assert drive.calls == [("sync",), ("sync",)]


def test_push_to_drive_delegates_to_dedicated_operation_and_route(
    client: TestClient,
) -> None:
    drive = _DriveHookStub()
    service = DocumentLibraryService(
        client.app.state.database,
        google_drive=drive,  # type: ignore[arg-type]
    )

    assert service.push_document_library_to_drive() == {
        "mode": "LIBRARY_TO_DRIVE",
        "status": "SYNCED",
        "conflicts": 0,
    }
    client.app.state.document_library_service = service
    response = client.post("/api/v1/document-library/sync-to-drive")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "LIBRARY_TO_DRIVE",
        "status": "SYNCED",
        "conflicts": 0,
    }
    assert drive.calls == [("push",), ("push",)]
