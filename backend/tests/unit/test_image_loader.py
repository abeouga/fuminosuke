import io
from collections.abc import Callable

import pytest
from PIL import Image, ImageOps

from app.core.config import Settings
from app.core.errors import AppError
from app.image_processing.image_loader import ImageLoader


def png_bytes(width: int = 32, height: int = 24) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_loads_png_and_calculates_metadata(settings_factory: Callable[..., Settings]) -> None:
    settings = settings_factory()
    loaded = ImageLoader(settings.storage, settings.image_processing).load(
        png_bytes(), "image/png"
    )

    assert loaded.rgb.shape == (24, 32, 3)
    assert loaded.metadata.format == "png"
    assert len(loaded.metadata.sha256) == 64


@pytest.mark.parametrize(
    ("content", "code"),
    [(b"", "EMPTY_IMAGE"), (b"not an image", "UNSUPPORTED_IMAGE_FORMAT")],
)
def test_rejects_invalid_image(
    settings_factory: Callable[..., Settings], content: bytes, code: str
) -> None:
    settings = settings_factory()
    with pytest.raises(AppError) as caught:
        ImageLoader(settings.storage, settings.image_processing).load(content)
    assert caught.value.code == code


def test_rejects_mime_mismatch(settings_factory: Callable[..., Settings]) -> None:
    settings = settings_factory()
    with pytest.raises(AppError) as caught:
        ImageLoader(settings.storage, settings.image_processing).load(
            png_bytes(), "image/jpeg"
        )
    assert caught.value.code == "UNSUPPORTED_IMAGE_FORMAT"


@pytest.mark.parametrize(
    ("settings_overrides", "content", "code"),
    [
        ({"max_image_edge": 32}, png_bytes(width=33), "IMAGE_TOO_LARGE"),
        (
            {"max_image_pixels": 700},
            png_bytes(width=32, height=24),
            "PIXEL_LIMIT_EXCEEDED",
        ),
    ],
)
def test_rejects_oversized_header_before_expanding_pixels(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    settings_overrides: dict[str, int],
    content: bytes,
    code: str,
) -> None:
    settings = settings_factory(**settings_overrides)

    def fail_if_expanded(_image: Image.Image) -> Image.Image:
        raise AssertionError("pixel expansion must not run for an oversized header")

    monkeypatch.setattr(ImageOps, "exif_transpose", fail_if_expanded)
    with pytest.raises(AppError) as caught:
        ImageLoader(settings.storage, settings.image_processing).load(content)

    assert caught.value.code == code
