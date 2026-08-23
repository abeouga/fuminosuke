from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core.config import GoogleOAuthSettings, RawSettings, Settings


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


def test_local_env_file_overrides_versioned_env_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                f"DATA_DIR={data_dir.as_posix()}",
                f"DATABASE_URL=sqlite:///{(data_dir / 'app.db').as_posix()}",
                f"LOG_FILE={(data_dir / 'logs' / 'app.log').as_posix()}",
                "GOOGLE_PICKER_APP_ID=versioned-value",
            )
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env.local").write_text(
        "GOOGLE_PICKER_APP_ID=local-value\n",
        encoding="utf-8",
    )

    settings = Settings.load(env_file)

    assert settings.google_oauth.picker_app_id == "local-value"


def test_picker_project_must_match_oauth_client_project() -> None:
    settings = GoogleOAuthSettings(
        enabled=True,
        client_id="111111111111-client.apps.googleusercontent.com",
        client_secret=SecretStr("client-secret"),
        jwt_secret=SecretStr("test-jwt-secret-that-is-long-enough"),
        redirect_uri="http://127.0.0.1:8000/api/v1/auth/google/callback",
        frontend_url="http://127.0.0.1:5173/",
        session_ttl_seconds=604_800,
        state_ttl_seconds=600,
        secure_cookie=False,
        picker_api_key="browser-key",
        picker_app_id="222222222222",
    )

    assert settings.picker_configured is False
    assert settings.picker_configuration_error is not None
    assert "一致していません" in settings.picker_configuration_error
