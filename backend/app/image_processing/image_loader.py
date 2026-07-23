from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass
from typing import Literal

import cv2
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
            decoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            if decoded is None:
                raise self._error("CORRUPTED_IMAGE", "OpenCVで画像を読み込めません。")
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    image.verify()
                with Image.open(io.BytesIO(content)) as image:
                    width, height = image.size
                    self._validate_dimensions(width, height)
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
