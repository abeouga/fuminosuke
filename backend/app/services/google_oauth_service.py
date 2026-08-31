from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import requests
from cryptography.fernet import Fernet, InvalidToken
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from sqlalchemy import select, update

from app.core.config import GoogleOAuthSettings
from app.core.errors import AppError
from app.db.models import GoogleOAuthAccount
from app.db.session import Database
from app.security.jwt_session import JwtValidationError, decode_jwt, encode_jwt

logger = logging.getLogger(__name__)

_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
_SESSION_AUDIENCE = "ledger-ocr-session"
_STATE_AUDIENCE = "ledger-ocr-google-oauth-state"
_SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive",
)
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_SPREADSHEETS_SCOPE_PREFIX = "https://www.googleapis.com/auth/spreadsheets"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    subject: str
    email: str
    display_name: str | None
    picture_url: str | None

    def payload(self) -> dict[str, str | None]:
        return {
            "subject": self.subject,
            "email": self.email,
            "displayName": self.display_name,
            "pictureUrl": self.picture_url,
        }


@dataclass(frozen=True, slots=True)
class OAuthStart:
    authorization_url: str
    state_cookie: str


@dataclass(frozen=True, slots=True)
class OAuthCompletion:
    user: AuthenticatedUser
    session_token: str
    return_to: str


class GoogleOAuthService:
    def __init__(self, settings: GoogleOAuthSettings, database: Database) -> None:
        self._settings = settings
        self._database = database
        self._fernet = self._build_fernet()

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    @property
    def configured(self) -> bool:
        return self._settings.configured

    @property
    def session_cookie_name(self) -> str:
        return self._settings.session_cookie_name

    @property
    def state_cookie_name(self) -> str:
        return self._settings.state_cookie_name

    @property
    def secure_cookie(self) -> bool:
        return self._settings.secure_cookie

    @property
    def session_ttl_seconds(self) -> int:
        return self._settings.session_ttl_seconds

    @property
    def state_ttl_seconds(self) -> int:
        return self._settings.state_ttl_seconds

    @property
    def frontend_url(self) -> str:
        return self._settings.frontend_url

    def begin(self, return_to: str | None) -> OAuthStart:
        self._require_configured()
        nonce = str(uuid4())
        state = encode_jwt(
            {"jti": nonce, "returnTo": self._safe_return_to(return_to)},
            secret=self._jwt_secret(),
            audience=_STATE_AUDIENCE,
            ttl_seconds=self._settings.state_ttl_seconds,
        )
        query = urlencode(
            {
                "client_id": self._client_id(),
                "redirect_uri": self._settings.redirect_uri,
                "response_type": "code",
                "scope": " ".join(_SCOPES),
                "access_type": "offline",
                "include_granted_scopes": "false",
                "prompt": "consent select_account",
                "state": state,
            }
        )
        return OAuthStart(f"{_AUTHORIZATION_ENDPOINT}?{query}", nonce)

    def complete(self, *, code: str, state: str, state_cookie: str | None) -> OAuthCompletion:
        self._require_configured()
        try:
            state_payload = decode_jwt(
                state,
                secret=self._jwt_secret(),
                audience=_STATE_AUDIENCE,
            )
        except JwtValidationError as exc:
            raise self._oauth_error("Google認証のstateを確認できませんでした。") from exc
        nonce = state_payload.get("jti")
        if not isinstance(nonce, str) or not state_cookie or not hmac_compare(nonce, state_cookie):
            raise self._oauth_error("Google認証を開始したブラウザーと一致しません。")
        try:
            token_response = requests.post(
                _TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self._client_id(),
                    "client_secret": self._client_secret(),
                    "redirect_uri": self._settings.redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=20,
            )
        except requests.RequestException as exc:
            raise self._oauth_error("Google認証サーバーへ接続できませんでした。") from exc
        if not token_response.ok:
            provider_error = self._provider_error(token_response)
            logger.warning(
                "Google OAuth token exchange failed",
                extra={
                    "status": token_response.status_code,
                    "providerError": provider_error,
                },
            )
            code, detail = self._token_exchange_error(provider_error)
            raise self._oauth_error(detail, code=code)
        token_data = token_response.json()
        if not isinstance(token_data, dict):
            raise self._oauth_error("Google認証レスポンスの形式が不正です。")
        claims = self._verified_claims(token_data)
        subject = claims.get("sub")
        email = claims.get("email")
        if not isinstance(subject, str) or not isinstance(email, str):
            raise self._oauth_error("Googleアカウントの識別情報を取得できませんでした。")
        if claims.get("email_verified") is not True:
            raise self._oauth_error("確認済みのGoogleメールアドレスを取得できませんでした。")
        user = AuthenticatedUser(
            subject=subject,
            email=email,
            display_name=claims.get("name") if isinstance(claims.get("name"), str) else None,
            picture_url=claims.get("picture") if isinstance(claims.get("picture"), str) else None,
        )
        self._save_account(user, token_data)
        session_token = encode_jwt(
            {
                "sub": user.subject,
                "email": user.email,
                "name": user.display_name,
            },
            secret=self._jwt_secret(),
            audience=_SESSION_AUDIENCE,
            ttl_seconds=self._settings.session_ttl_seconds,
        )
        return OAuthCompletion(
            user=user,
            session_token=session_token,
            return_to=self._safe_return_to(
                state_payload.get("returnTo")
                if isinstance(state_payload.get("returnTo"), str)
                else None
            ),
        )

    def current_user(self, session_token: str | None) -> AuthenticatedUser | None:
        if not self.configured or not session_token:
            return None
        try:
            payload = decode_jwt(
                session_token,
                secret=self._jwt_secret(),
                audience=_SESSION_AUDIENCE,
            )
        except JwtValidationError:
            return None
        subject = payload.get("sub")
        if not isinstance(subject, str):
            return None
        with self._database.session() as session:
            account = session.get(GoogleOAuthAccount, subject)
            if account is None or not account.is_active:
                return None
            return self._user(account)

    def has_active_account(self) -> bool:
        if not self.configured:
            return False
        with self._database.session() as session:
            account = (
                session.query(GoogleOAuthAccount)
                .filter(GoogleOAuthAccount.is_active.is_(True))
                .first()
            )
            return account is not None and self._authorization_is_current(account.scopes)

    def authorization_is_current(self, subject: str) -> bool:
        if not self.configured:
            return False
        with self._database.session() as session:
            account = session.get(GoogleOAuthAccount, subject)
            return bool(
                account is not None
                and account.is_active
                and self._authorization_is_current(account.scopes)
            )

    def active_subject(self) -> str | None:
        if not self.configured:
            return None
        with self._database.session() as session:
            account = session.scalar(
                select(GoogleOAuthAccount)
                .where(GoogleOAuthAccount.is_active.is_(True))
                .order_by(GoogleOAuthAccount.updated_at.desc())
                .limit(1)
            )
            if account is None or not self._authorization_is_current(account.scopes):
                return None
            return account.google_subject

    def credentials(self, subject: str | None = None) -> Credentials:
        self._require_configured()
        with self._database.session() as session:
            account = (
                session.get(GoogleOAuthAccount, subject)
                if subject is not None
                else session.scalar(
                    select(GoogleOAuthAccount)
                    .where(GoogleOAuthAccount.is_active.is_(True))
                    .order_by(GoogleOAuthAccount.updated_at.desc())
                    .limit(1)
                )
            )
            if account is None or not account.is_active:
                raise RuntimeError("Googleアカウントが接続されていません。")
            if not self._authorization_is_current(account.scopes):
                raise RuntimeError(
                    "Google連携の権限が旧形式です。連携を解除して再認証してください。"
                )
            refresh_token = self._decrypt(account.encrypted_refresh_token)
            scopes = tuple(item for item in account.scopes.split(" ") if item)
        return Credentials(  # type: ignore[no-untyped-call]
            token=None,
            refresh_token=refresh_token,
            token_uri=_TOKEN_ENDPOINT,
            client_id=self._client_id(),
            client_secret=self._client_secret(),
            scopes=scopes or _SCOPES,
        )

    def spreadsheet_target(self) -> tuple[str | None, str | None]:
        self._require_configured()
        with self._database.session() as session:
            account = (
                session.query(GoogleOAuthAccount)
                .filter(GoogleOAuthAccount.is_active.is_(True))
                .order_by(GoogleOAuthAccount.updated_at.desc())
                .first()
            )
            if account is None:
                raise RuntimeError("Googleアカウントが接続されていません。")
            if not self._authorization_is_current(account.scopes):
                raise RuntimeError(
                    "Google連携の権限が旧形式です。連携を解除して再認証してください。"
                )
            return account.spreadsheet_id, account.spreadsheet_url

    def save_spreadsheet_target(self, spreadsheet_id: str, spreadsheet_url: str) -> None:
        self._require_configured()
        with self._database.session() as session:
            account = (
                session.query(GoogleOAuthAccount)
                .filter(GoogleOAuthAccount.is_active.is_(True))
                .order_by(GoogleOAuthAccount.updated_at.desc())
                .first()
            )
            if account is None:
                raise RuntimeError("Googleアカウントが接続されていません。")
            if not self._authorization_is_current(account.scopes):
                raise RuntimeError(
                    "Google連携の権限が旧形式です。連携を解除して再認証してください。"
                )
            account.spreadsheet_id = spreadsheet_id
            account.spreadsheet_url = spreadsheet_url
            session.commit()

    def disconnect(self, user: AuthenticatedUser) -> None:
        refresh_token: str | None = None
        with self._database.session() as session:
            account = session.get(GoogleOAuthAccount, user.subject)
            if account is not None:
                refresh_token = self._decrypt(account.encrypted_refresh_token)
                # Keep the account row as an ownership tombstone.  Folder/root
                # mappings use the Google subject to prevent another account
                # from adopting legacy Drive content after a disconnect.
                account.encrypted_refresh_token = self._encrypt("")
                account.scopes = ""
                account.is_active = False
                session.commit()
        if refresh_token:
            try:
                requests.post(_REVOKE_ENDPOINT, params={"token": refresh_token}, timeout=10)
            except requests.RequestException:
                logger.warning("Google OAuth token revocation request failed", exc_info=True)

    def _save_account(self, user: AuthenticatedUser, token_data: dict[str, Any]) -> None:
        refresh_token = token_data.get("refresh_token")
        with self._database.session() as session:
            existing = session.get(GoogleOAuthAccount, user.subject)
            if not isinstance(refresh_token, str):
                if existing is None or not self._authorization_is_current(existing.scopes):
                    raise self._oauth_error(
                        "Googleからrefresh tokenを取得できませんでした。"
                        "連携を解除して再認証してください。"
                    )
                encrypted_refresh_token = existing.encrypted_refresh_token
            else:
                encrypted_refresh_token = self._encrypt(refresh_token)
            session.execute(update(GoogleOAuthAccount).values(is_active=False))
            scope_value = token_data.get("scope")
            scopes = scope_value if isinstance(scope_value, str) else " ".join(_SCOPES)
            if existing is None:
                session.add(
                    GoogleOAuthAccount(
                        google_subject=user.subject,
                        email=user.email,
                        display_name=user.display_name,
                        picture_url=user.picture_url,
                        encrypted_refresh_token=encrypted_refresh_token,
                        scopes=scopes,
                        is_active=True,
                    )
                )
            else:
                existing.email = user.email
                existing.display_name = user.display_name
                existing.picture_url = user.picture_url
                existing.encrypted_refresh_token = encrypted_refresh_token
                existing.scopes = scopes
                existing.is_active = True
            session.commit()

    def _verified_claims(self, token_data: dict[str, Any]) -> dict[str, Any]:
        raw_id_token = token_data.get("id_token")
        if not isinstance(raw_id_token, str):
            raise self._oauth_error("Google ID tokenを取得できませんでした。")
        try:
            claims = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                raw_id_token,
                GoogleAuthRequest(),
                self._client_id(),
            )
        except ValueError as exc:
            raise self._oauth_error("Google ID tokenを検証できませんでした。") from exc
        return claims if isinstance(claims, dict) else {}

    def _safe_return_to(self, value: str | None) -> str:
        fallback = self._settings.frontend_url.rstrip("/") + "/"
        if not value:
            return fallback
        candidate = urlparse(value)
        frontend = urlparse(fallback)
        if not candidate.scheme and value.startswith("/"):
            return f"{frontend.scheme}://{frontend.netloc}{value}"
        if candidate.scheme == frontend.scheme and candidate.netloc == frontend.netloc:
            return value
        return fallback

    def _build_fernet(self) -> Fernet | None:
        if not self.configured:
            return None
        digest = hashlib.sha256(
            b"ledger-ocr-google-refresh-token:" + self._jwt_secret().encode()
        ).digest()
        return Fernet(base64.urlsafe_b64encode(digest))

    def _encrypt(self, value: str) -> str:
        if self._fernet is None:
            raise RuntimeError("Google OAuth暗号化キーが設定されていません。")
        return self._fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value: str) -> str:
        if self._fernet is None:
            raise RuntimeError("Google OAuth暗号化キーが設定されていません。")
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("保存済みGoogleトークンを復号できません。") from exc

    def _require_configured(self) -> None:
        if not self.configured:
            raise AppError(
                status=503,
                code="GOOGLE_OAUTH_NOT_CONFIGURED",
                title="Google OAuth is not configured",
                detail="Google OAuthクライアント設定が完了していません。",
            )

    def _jwt_secret(self) -> str:
        secret = self._settings.jwt_secret
        if secret is None:
            raise RuntimeError("AUTH_JWT_SECRET is not configured")
        return secret.get_secret_value()

    def _client_id(self) -> str:
        if not self._settings.client_id:
            raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID is not configured")
        return self._settings.client_id

    def _client_secret(self) -> str:
        secret = self._settings.client_secret
        if secret is None:
            raise RuntimeError("GOOGLE_OAUTH_CLIENT_SECRET is not configured")
        return secret.get_secret_value()

    @staticmethod
    def _authorization_is_current(scope_value: str) -> bool:
        scopes = {item for item in scope_value.split() if item}
        return _DRIVE_SCOPE in scopes and not any(
            item.startswith(_SPREADSHEETS_SCOPE_PREFIX) for item in scopes
        )

    @staticmethod
    def _provider_error(token_response: requests.Response) -> str | None:
        try:
            payload = token_response.json()
        except (requests.JSONDecodeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        value = payload.get("error")
        return value if isinstance(value, str) else None

    @staticmethod
    def _token_exchange_error(provider_error: str | None) -> tuple[str, str]:
        if provider_error == "invalid_client":
            return (
                "GOOGLE_OAUTH_INVALID_CLIENT",
                "Google OAuthのクライアントIDまたはクライアントシークレットが一致しません。",
            )
        if provider_error in {"invalid_grant", "redirect_uri_mismatch"}:
            return (
                "GOOGLE_OAUTH_INVALID_GRANT",
                "Google認証コードが期限切れ、再使用済み、またはリダイレクトURIと一致しません。認証を最初からやり直してください。",
            )
        return (
            "GOOGLE_OAUTH_FAILED",
            "Google認証コードをアクセストークンへ交換できませんでした。",
        )

    @staticmethod
    def _user(account: GoogleOAuthAccount) -> AuthenticatedUser:
        return AuthenticatedUser(
            subject=account.google_subject,
            email=account.email,
            display_name=account.display_name,
            picture_url=account.picture_url,
        )

    @staticmethod
    def _oauth_error(detail: str, *, code: str = "GOOGLE_OAUTH_FAILED") -> AppError:
        return AppError(
            status=400,
            code=code,
            title="Google OAuth failed",
            detail=detail,
        )


def hmac_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())
