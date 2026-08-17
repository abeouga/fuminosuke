from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


class JwtValidationError(ValueError):
    pass


def encode_jwt(
    payload: dict[str, Any],
    *,
    secret: str,
    audience: str,
    ttl_seconds: int,
) -> str:
    now = int(time.time())
    body = {
        **payload,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    header_part = _encode_json({"alg": "HS256", "typ": "JWT"})
    payload_part = _encode_json(body)
    signing_input = f"{header_part}.{payload_part}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_base64url(signature)}"


def decode_jwt(token: str, *, secret: str, audience: str) -> dict[str, Any]:
    try:
        header_part, payload_part, signature_part = token.split(".")
        header = json.loads(_decode_base64url(header_part))
        payload = json.loads(_decode_base64url(payload_part))
        signature = _decode_base64url_bytes(signature_part)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise JwtValidationError("JWTの形式が不正です。") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise JwtValidationError("JWTの署名方式が不正です。")
    if not isinstance(payload, dict):
        raise JwtValidationError("JWT payloadが不正です。")
    signing_input = f"{header_part}.{payload_part}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected) or not hmac.compare_digest(
        signature_part,
        _base64url(expected),
    ):
        raise JwtValidationError("JWT署名を確認できません。")
    if payload.get("aud") != audience:
        raise JwtValidationError("JWT audienceが一致しません。")
    expires_at = payload.get("exp")
    if not isinstance(expires_at, int) or expires_at <= int(time.time()):
        raise JwtValidationError("JWTの有効期限が切れています。")
    return payload


def _encode_json(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return _base64url(encoded)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode_base64url(value: str) -> str:
    return _decode_base64url_bytes(value).decode()


def _decode_base64url_bytes(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
