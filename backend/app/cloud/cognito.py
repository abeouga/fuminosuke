from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from app.cloud.config import CloudSettings
from app.cloud.registry import AccessDenied


class CognitoUnavailable(Exception):
    """Cognito could not answer (network, 5xx); not a verdict on the user."""


@dataclass(frozen=True)
class Login:
    subject: str
    # Seconds the verified ID token is still valid; the session is renewed after it.
    lifetime: int
    refresh_token: str | None


class Cognito:
    """Fixed issuer and algorithm; no untrusted discovery URL or browser bearer tokens."""

    def __init__(self, config: CloudSettings, client: httpx.AsyncClient) -> None:
        self.config, self.client = config, client
        self.keys: dict[str, Any] = {}
        self.refreshed = float("-inf")
        self.lock = asyncio.Lock()

    async def identity(self, code: str, verifier: str, nonce: str) -> Login:
        try:
            response = await self.client.post(self.config.cognito_domain + "/oauth2/token", data={
                "grant_type": "authorization_code", "client_id": self.config.cognito_client_id,
                "redirect_uri": self.config.callback, "code": code, "code_verifier": verifier,
            })
            response.raise_for_status()
            tokens = response.json()
            subject, lifetime = await self._verify(tokens["id_token"], nonce)
            return Login(subject, lifetime, _refresh_token(tokens.get("refresh_token")))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AccessDenied from exc

    async def password_identity(self, username: str, password: str) -> Login:
        """The app's own login form: USER_PASSWORD_AUTH with the public client, server side.

        Any challenge (new password, MFA) is refused; users are created with permanent passwords.
        """
        try:
            response = await self._initiate("USER_PASSWORD_AUTH",
                                            {"USERNAME": username, "PASSWORD": password})
            if response.status_code != 200:
                raise AccessDenied
            result = response.json()["AuthenticationResult"]
            subject, lifetime = await self._verify(result["IdToken"], None)
            return Login(subject, lifetime, _refresh_token(result.get("RefreshToken")))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise AccessDenied from exc

    async def refresh(self, refresh_token: str) -> Login:
        """A new ID token from the refresh token; refused once Cognito revokes or expires it.

        Raises CognitoUnavailable (not AccessDenied) when Cognito itself cannot answer, so a
        network blip does not sign the user out.
        """
        try:
            response = await self._initiate("REFRESH_TOKEN_AUTH",
                                            {"REFRESH_TOKEN": refresh_token})
        except httpx.HTTPError as exc:
            raise CognitoUnavailable from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise CognitoUnavailable
        if response.status_code != 200:
            raise AccessDenied
        try:
            result = response.json()["AuthenticationResult"]
            subject, lifetime = await self._verify(result["IdToken"], None)
            # Present only when the app client rotates refresh tokens.
            return Login(subject, lifetime, _refresh_token(result.get("RefreshToken")))
        except (KeyError, TypeError, ValueError) as exc:
            raise AccessDenied from exc

    async def revoke(self, refresh_token: str) -> None:
        """Best effort at logout: the refresh token stops working at Cognito too."""
        with contextlib.suppress(httpx.HTTPError):
            await self.client.post(self.config.cognito_domain + "/oauth2/revoke", data={
                "token": refresh_token, "client_id": self.config.cognito_client_id,
            }, timeout=5)

    async def _initiate(self, flow: str, parameters: dict[str, str]) -> httpx.Response:
        return await self.client.post(
            f"https://cognito-idp.{self.config.cognito_region}.amazonaws.com/",
            headers={
                "Content-Type": "application/x-amz-json-1.1",
                "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
            },
            json={"AuthFlow": flow, "ClientId": self.config.cognito_client_id,
                  "AuthParameters": parameters},
        )

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


def _refresh_token(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 8192 else None
