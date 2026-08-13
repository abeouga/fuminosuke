from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from app.db.models import JobArtifact
from app.db.session import Database
from app.repositories.artifact_repository import ArtifactRepository
from app.storage.local_storage import LocalStorageService


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    name: str
    file_name: str
    media_type: str


class ArtifactWriter:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage

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
        relative = f"artifacts/{job_id}/attempts/{attempt_number}/{file_name}"
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


def encode_png(image: NDArray[np.uint8], *, rgb: bool = True) -> bytes:
    encoded_input = image
    if rgb and image.ndim == 3:
        encoded_input = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    success, encoded = cv2.imencode(".png", encoded_input, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not success:
        raise RuntimeError("Failed to encode PNG artifact")
    return encoded.tobytes()
