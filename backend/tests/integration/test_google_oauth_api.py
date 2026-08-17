from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.base import Base
from app.db.models import GoogleOAuthAccount
from app.db.session import Database
from app.main import create_app


class _TokenResponse:
    ok = True
    status_code = 200

    def json(self) -> dict[str, str]:
        return {
            "access_token": "short-lived-access-token",
            "refresh_token": "stored-refresh-token",
            "id_token": "signed-google-id-token",
            "scope": "openid email profile https://www.googleapis.com/auth/spreadsheets",
        }


@contextmanager
def _oauth_client(settings_factory: Callable[..., Settings]) -> Iterator[TestClient]:
    settings = settings_factory(
        google_oauth_enabled=True,
        google_oauth_client_id="client-id.apps.googleusercontent.com",
        google_oauth_client_secret="client-secret",
        auth_jwt_secret="local-test-jwt-secret-that-is-at-least-32-characters",
    )
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    database.dispose()
    with TestClient(create_app(settings)) as client:
        yield client


def test_google_oauth_login_uses_encrypted_refresh_token_and_jwt_cookie(
    settings_factory: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.google_oauth_service.requests.post",
        lambda *args, **kwargs: _TokenResponse(),
    )
    verified_claims: dict[str, Any] = {
        "sub": "google-user-1",
        "email": "owner@example.com",
        "email_verified": True,
        "name": "台帳 管理者",
        "picture": "https://example.com/avatar.png",
    }
    monkeypatch.setattr(
        "app.services.google_oauth_service.id_token.verify_oauth2_token",
        lambda *args, **kwargs: verified_claims,
    )

    with _oauth_client(settings_factory) as client:
        unauthorized = client.get("/api/v1/templates")
        assert unauthorized.status_code == 401
        assert unauthorized.json()["code"] == "AUTHENTICATION_REQUIRED"

        started = client.get(
            "/api/v1/auth/google/start",
            params={"returnTo": "http://127.0.0.1:5173/?job=job-1"},
            follow_redirects=False,
        )
        assert started.status_code == 302
        authorization_url = started.headers["location"]
        assert urlparse(authorization_url).netloc == "accounts.google.com"
        state = parse_qs(urlparse(authorization_url).query)["state"][0]

        completed = client.get(
            "/api/v1/auth/google/callback",
            params={"code": "one-time-code", "state": state},
            follow_redirects=False,
        )
        assert completed.status_code == 302
        assert completed.headers["location"] == "http://127.0.0.1:5173/?job=job-1"
        assert "ledger_session=" in completed.headers["set-cookie"]
        assert "HttpOnly" in completed.headers["set-cookie"]

        session_response = client.get("/api/v1/auth/session")
        assert session_response.json() == {
            "required": True,
            "configured": True,
            "authenticated": True,
            "user": {
                "subject": "google-user-1",
                "email": "owner@example.com",
                "displayName": "台帳 管理者",
                "pictureUrl": "https://example.com/avatar.png",
            },
        }
        assert client.get("/api/v1/templates").status_code == 200

        with client.app.state.database.session() as database_session:
            account = database_session.get(GoogleOAuthAccount, "google-user-1")
            assert account is not None
            assert account.encrypted_refresh_token != "stored-refresh-token"
            assert "stored-refresh-token" not in account.encrypted_refresh_token

        credentials = client.app.state.google_oauth.credentials()
        assert credentials.refresh_token == "stored-refresh-token"
        assert client.app.state.google_oauth.spreadsheet_target() == (None, None)
        client.app.state.google_oauth.save_spreadsheet_target(
            "user-sheet-id",
            "https://docs.google.com/spreadsheets/d/user-sheet-id/edit",
        )
        assert client.app.state.google_oauth.spreadsheet_target() == (
            "user-sheet-id",
            "https://docs.google.com/spreadsheets/d/user-sheet-id/edit",
        )

        logged_out = client.post("/api/v1/auth/logout")
        assert logged_out.status_code == 200
        assert client.get("/api/v1/templates").status_code == 401


def test_oauth_return_url_cannot_leave_configured_frontend(
    settings_factory: Callable[..., Settings],
) -> None:
    with _oauth_client(settings_factory) as client:
        started = client.get(
            "/api/v1/auth/google/start",
            params={"returnTo": "https://attacker.example/steal"},
            follow_redirects=False,
        )
        state = parse_qs(urlparse(started.headers["location"]).query)["state"][0]
        payload = client.app.state.google_oauth._safe_return_to("https://attacker.example/steal")
        assert state
        assert payload == "http://127.0.0.1:5173/"
