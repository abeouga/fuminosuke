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
            return await self._verify(response.json()["id_token"], nonce)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AccessDenied from exc

    async def password_identity(self, username: str, password: str) -> tuple[str, int]:
        """The app's own login form: USER_PASSWORD_AUTH with the public client, server side.

        Any challenge (new password, MFA) is refused; users are created with permanent passwords.
        """
        try:
            response = await self.client.post(
                f"https://cognito-idp.{self.config.cognito_region}.amazonaws.com/",
                headers={
                    "Content-Type": "application/x-amz-json-1.1",
                    "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                },
                json={
                    "AuthFlow": "USER_PASSWORD_AUTH", "ClientId": self.config.cognito_client_id,
                    "AuthParameters": {"USERNAME": username, "PASSWORD": password},
                },
            )
            if response.status_code != 200:
                raise AccessDenied
            return await self._verify(response.json()["AuthenticationResult"]["IdToken"], None)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AccessDenied from exc

    async def _verify(self, token: object, nonce: str | None) -> tuple[str, int]:
        try:
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
            required = ["exp", "iat", "iss", "aud", "sub", "token_use"]
            claims = jwt.decode(
                token, self.keys[header["kid"]], algorithms=["RS256"],
                audience=self.config.cognito_client_id, issuer=self.config.issuer,
                options={"require": required + (["nonce"] if nonce is not None else [])},
            )
            if claims["token_use"] != "id":
                raise AccessDenied
            if nonce is not None and not secrets.compare_digest(claims["nonce"], nonce):
                raise AccessDenied
            lifetime = min(self.config.session_seconds, int(claims["exp"] - time.time()))
            if lifetime <= 0:
                raise AccessDenied
            return str(claims["sub"]), lifetime
        except (httpx.HTTPError, jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
            raise AccessDenied from exc
