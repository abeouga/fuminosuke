from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import ImageProcessingSettings, StorageSettings
from app.core.errors import AppError


@dataclass(frozen=True, slots=True)
class ImageMetadata:
    media_type: Literal["image/jpeg", "image/png"]
    format: Literal["jpeg", "png"]
    extension: Literal["jpg", "png"]
    width: int
    height: int
    file_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class LoadedImage:
    rgb: NDArray[np.uint8]
    metadata: ImageMetadata


def verified_image_size(content: bytes) -> tuple[int, int]:
    """Read and verify image metadata without expanding the pixel buffer."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                image.verify()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValueError("Image metadata could not be verified") from exc
    return width, height


class ImageLoader:
    def __init__(
        self,
        storage_settings: StorageSettings,
        image_settings: ImageProcessingSettings,
    ) -> None:
        self._max_bytes = storage_settings.max_upload_bytes
        self._max_pixels = image_settings.max_image_pixels
        self._max_edge = image_settings.max_image_edge

    def load(self, content: bytes, declared_media_type: str | None = None) -> LoadedImage:
        detected, _, _ = self._validated_header(content, declared_media_type)

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    oriented = ImageOps.exif_transpose(image)
                    if "A" in oriented.getbands() or "transparency" in oriented.info:
                        rgba = oriented.convert("RGBA")
                        background = Image.new("RGBA", rgba.size, "white")
                        normalized = Image.alpha_composite(background, rgba).convert("RGB")
                    else:
                        normalized = oriented.convert("RGB")
                    rgb = np.asarray(normalized, dtype=np.uint8).copy()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
            raise self._error("CORRUPTED_IMAGE", "画像を正常に読み込めません。") from exc
        except Image.DecompressionBombWarning as exc:
            raise self._error(
                "PIXEL_LIMIT_EXCEEDED", "画像の総画素数が上限を超えています。"
            ) from exc

        metadata = ImageMetadata(
            media_type=detected[0],
            format=detected[1],
            extension=detected[2],
            width=rgb.shape[1],
            height=rgb.shape[0],
            file_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        return LoadedImage(rgb=rgb, metadata=metadata)

    def inspect_dimensions(
        self,
        content: bytes,
        declared_media_type: str | None = None,
    ) -> tuple[int, int]:
        _, width, height = self._validated_header(content, declared_media_type)
        return width, height

    def _validated_header(
        self,
        content: bytes,
        declared_media_type: str | None,
    ) -> tuple[
        tuple[
            Literal["image/jpeg", "image/png"],
            Literal["jpeg", "png"],
            Literal["jpg", "png"],
        ],
        int,
        int,
    ]:
        if not content:
            raise self._error("EMPTY_IMAGE", "画像ファイルが空です。")
        if len(content) > self._max_bytes:
            raise self._error("IMAGE_TOO_LARGE", "画像ファイルサイズが上限を超えています。")
        detected = self._detect_type(content)
        if declared_media_type and declared_media_type not in {
            detected[0],
            "application/octet-stream",
        }:
            raise self._error(
                "UNSUPPORTED_IMAGE_FORMAT", "申告されたMIME形式と画像内容が一致しません。"
            )

        try:
            width, height = verified_image_size(content)
        except ValueError as exc:
            raise self._error("CORRUPTED_IMAGE", "画像を正常に読み込めません。") from exc
        self._validate_dimensions(width, height)
        return detected, width, height

    def _detect_type(
        self, content: bytes
    ) -> tuple[
        Literal["image/jpeg", "image/png"],
        Literal["jpeg", "png"],
        Literal["jpg", "png"],
    ]:
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", "jpeg", "jpg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", "png", "png"
        raise self._error("UNSUPPORTED_IMAGE_FORMAT", "JPEGまたはPNG画像だけを使用できます。")

    def _validate_dimensions(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise self._error("EMPTY_IMAGE", "画像の幅または高さが0です。")
        if width > self._max_edge or height > self._max_edge:
            raise self._error("IMAGE_TOO_LARGE", "画像の一辺が上限を超えています。")
        if width * height > self._max_pixels:
            raise self._error("PIXEL_LIMIT_EXCEEDED", "画像の総画素数が上限を超えています。")

    @staticmethod
    def _error(code: str, detail: str) -> AppError:
        return AppError(status=422, code=code, title="Image validation failed", detail=detail)
