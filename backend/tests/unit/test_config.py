from pathlib import Path

import pytest

from app.core.config import RawSettings, Settings


def test_wildcard_cors_is_rejected(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    raw = RawSettings(
        _env_file=None,
        frontend_origins="*",
        data_dir=data_dir,
        database_url=f"sqlite:///{(data_dir / 'app.db').as_posix()}",
        log_file=data_dir / "logs" / "app.log",
    )

    with pytest.raises(ValueError, match="Wildcard"):
        Settings.from_raw(raw)


def test_database_must_be_inside_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    raw = RawSettings(
        _env_file=None,
        data_dir=data_dir,
        database_url=f"sqlite:///{(tmp_path / 'outside.db').as_posix()}",
        log_file=data_dir / "logs" / "app.log",
    )

    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings.from_raw(raw)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        (
            {"max_upload_bytes": 2_000, "max_batch_upload_bytes": 1_000},
            "MAX_BATCH_UPLOAD_BYTES",
        ),
        (
            {"max_image_pixels": 2_000, "max_batch_image_pixels": 1_000},
            "MAX_BATCH_IMAGE_PIXELS",
        ),
    ],
)
def test_batch_limits_must_cover_one_valid_image(
    tmp_path: Path,
    override: dict[str, int],
    message: str,
) -> None:
    data_dir = tmp_path / "data"
    raw = RawSettings(
        _env_file=None,
        data_dir=data_dir,
        database_url=f"sqlite:///{(data_dir / 'app.db').as_posix()}",
        log_file=data_dir / "logs" / "app.log",
        **override,
    )

    with pytest.raises(ValueError, match=message):
        Settings.from_raw(raw)
