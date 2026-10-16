from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

import httpx
import jwt

from app.cloud.config import CloudSettings
from app.cloud.registry import AccessDenied


class Cognito:
    """Fixed issuer and algorithm; no untrusted discovery URL or browser bearer tokens."""

    def __init__(self, config: CloudSettings, client: httpx.AsyncClient) -> None:
        self.config, self.client = config, client
        self.keys: dict[str, Any] = {}
        self.refreshed = float("-inf")
        self.lock = asyncio.Lock()

    async def identity(self, code: str, verifier: str, nonce: str) -> tuple[str, int]:
        try:
            response = await self.client.post(self.config.cognito_domain + "/oauth2/token", data={
                "grant_type": "authorization_code", "client_id": self.config.cognito_client_id,
                "redirect_uri": self.config.callback, "code": code, "code_verifier": verifier,
            })
            response.raise_for_status()
            token = response.json()["id_token"]
            if not isinstance(token, str) or len(token) > 16_384:
                raise AccessDenied
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise AccessDenied
            async with self.lock:
                # Bound unknown-kid refreshes to avoid an upstream request per bad token.
                age = time.monotonic() - self.refreshed
                if age > 300 or (header["kid"] not in self.keys and age > 10):
                    jwks = await self.client.get(self.config.issuer + "/.well-known/jwks.json")
                    jwks.raise_for_status()
                    self.keys = {
                        key["kid"]: jwt.PyJWK.from_dict(key, algorithm="RS256").key
                        for key in jwks.json()["keys"]
                        if key.get("kty") == "RSA" and key.get("alg") == "RS256"
                        and key.get("use") == "sig" and isinstance(key.get("kid"), str)
                    }
                    self.refreshed = time.monotonic()
            if header["kid"] not in self.keys:
                raise AccessDenied
            claims = jwt.decode(
                token, self.keys[header["kid"]], algorithms=["RS256"],
                audience=self.config.cognito_client_id, issuer=self.config.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce", "token_use"]},
            )
            if claims["token_use"] != "id" or not secrets.compare_digest(claims["nonce"], nonce):
                raise AccessDenied
            lifetime = min(self.config.session_seconds, int(claims["exp"] - time.time()))
            if lifetime <= 0:
                raise AccessDenied
            return str(claims["sub"]), lifetime
        except (httpx.HTTPError, jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise AccessDenied from exc
