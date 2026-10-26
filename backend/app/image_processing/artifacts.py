from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath

import cv2
import numpy as np
from numpy.typing import NDArray

from app.db.models import JobArtifact
from app.db.session import Database
from app.repositories.artifact_repository import ArtifactRepository
from app.storage.local_storage import LocalStorageService

# ArtifactWriter has a bytes() method, which hides the builtin inside the class body.
Content = bytes


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    name: str
    file_name: str
    media_type: str


class ArtifactWriter:
    def __init__(
        self, database: Database, storage: LocalStorageService, *, page_id: str | None = None,
    ) -> None:
        self._database = database
        self._storage = storage
        self._page_id = page_id

    def image(
        self,
        *,
        job_id: str,
        attempt_number: int,
        name: str,
        file_name: str,
        image: NDArray[np.uint8],
        rgb: bool = True,
        replace: bool = False,
    ) -> JobArtifact:
        content = encode_png(image, rgb=rgb)
        return self.bytes(
            job_id=job_id,
            attempt_number=attempt_number,
            name=name,
            file_name=file_name,
            media_type="image/png",
            content=content,
            width=image.shape[1],
            height=image.shape[0],
            replace=replace,
        )

    def json(
        self,
        *,
        job_id: str,
        attempt_number: int,
        name: str,
        file_name: str,
        value: object,
        replace: bool = False,
    ) -> JobArtifact:
        content = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        return self.bytes(
            job_id=job_id,
            attempt_number=attempt_number,
            name=name,
            file_name=file_name,
            media_type="application/json",
            content=content,
            replace=replace,
        )

    def bytes(
        self,
        *,
        job_id: str,
        attempt_number: int,
        name: str,
        file_name: str,
        media_type: str,
        content: bytes,
        width: int | None = None,
        height: int | None = None,
        replace: bool = False,
    ) -> JobArtifact:
        name, relative = self._location(job_id, attempt_number, name, file_name, content, replace)
        stored = self._storage.write_bytes(relative, content)
        with self._database.session() as session:
            repository = ArtifactRepository(session)
            write = repository.upsert if replace else repository.create
            artifact = write(
                job_id=job_id,
                attempt_number=attempt_number,
                artifact_name=name,
                media_type=media_type,
                relative_path=stored,
                sha256=hashlib.sha256(content).hexdigest(),
                width=width,
                height=height,
            )
            session.expunge(artifact)
            return artifact


    def _location(
        self, job_id: str, attempt_number: int, name: str, file_name: str, content: Content,
        replace: bool,
    ) -> tuple[str, str]:
        if self._page_id is not None:
            name = f"page.{self._page_id}.{name}"
            # Keep full page identity in the DB name, without pushing nested crop
            # paths beyond Windows' temporary-file path limit. The filename key
            # retains all 256 hash bits; it is not a truncated page identifier.
            key = hashlib.sha256(f"{self._page_id}/{file_name}".encode()).digest()
            token = base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")
            file_name = f"p/{token}{PurePosixPath(file_name).suffix}"
        relative = f"artifacts/{job_id}/attempts/{attempt_number}/{file_name}"
        if replace and self._storage.requires_immutable_paths:
            path = PurePosixPath(relative)
            content_sha = hashlib.sha256(content).hexdigest()
            relative = str(path.with_name(f"{path.stem}-{content_sha}{path.suffix}"))
        return name, relative

    def images(
        self,
        *,
        job_id: str,
        attempt_number: int,
        images: list[tuple[str, str, NDArray[np.uint8], bool]],
    ) -> list[JobArtifact]:
        """image(..., replace=True) for many images: the same files and records, stored with
        one storage batch and one database transaction. Items are (name, file_name, image, rgb).
        """
        return self.encoded_images(
            job_id=job_id,
            attempt_number=attempt_number,
            images=[
                (name, file_name, encode_png(image, rgb=rgb), image.shape[1], image.shape[0])
                for name, file_name, image, rgb in images
            ],
        )

    def encoded_images(
        self,
        *,
        job_id: str,
        attempt_number: int,
        images: list[tuple[str, str, Content, int, int]],
    ) -> list[JobArtifact]:
        """images() for PNGs already encoded with encode_png (name, file_name, png, width, height),
        e.g. by the extraction Lambda."""
        files: list[tuple[str, bytes]] = []
        rows: list[dict[str, object]] = []
        for name, file_name, content, width, height in images:
            stored_name, relative = self._location(
                job_id, attempt_number, name, file_name, content, True
            )
            files.append((relative, content))
            rows.append({
                "job_id": job_id,
                "attempt_number": attempt_number,
                "artifact_name": stored_name,
                "media_type": "image/png",
                "sha256": hashlib.sha256(content).hexdigest(),
                "width": width,
                "height": height,
            })
        for row, stored in zip(rows, self._storage.write_many(files), strict=True):
            row["relative_path"] = stored
        with self._database.session() as session:
            artifacts = ArtifactRepository(session).upsert_many(rows)
            for artifact in artifacts:
                session.expunge(artifact)
            return artifacts


def encode_png(image: NDArray[np.uint8], *, rgb: bool = True) -> bytes:
    encoded_input = image
    if rgb and image.ndim == 3:
        encoded_input = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    success, encoded = cv2.imencode(".png", encoded_input, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not success:
        raise RuntimeError("Failed to encode PNG artifact")
    return encoded.tobytes()
