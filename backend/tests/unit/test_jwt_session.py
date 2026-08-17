from __future__ import annotations

import pytest

from app.security import jwt_session
from app.security.jwt_session import JwtValidationError, decode_jwt, encode_jwt


def test_jwt_round_trip_and_signature_validation() -> None:
    token = encode_jwt(
        {"sub": "google-user-1"},
        secret="s" * 32,
        audience="test-session",
        ttl_seconds=300,
    )

    assert decode_jwt(token, secret="s" * 32, audience="test-session")["sub"] == "google-user-1"

    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload}.{signature[:-1]}A"
    with pytest.raises(JwtValidationError):
        decode_jwt(tampered, secret="s" * 32, audience="test-session")


def test_jwt_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jwt_session.time, "time", lambda: 1_000.0)
    token = encode_jwt({}, secret="s" * 32, audience="test-session", ttl_seconds=10)
    monkeypatch.setattr(jwt_session.time, "time", lambda: 1_011.0)

    with pytest.raises(JwtValidationError):
        decode_jwt(token, secret="s" * 32, audience="test-session")
