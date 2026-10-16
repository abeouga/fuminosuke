from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CloudSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BETA_", extra="ignore")

    root: Path
    public_origin: str
    cognito_pool_id: str
    cognito_client_id: str = Field(min_length=1, max_length=128)
    cognito_domain: str
    session_seconds: int = Field(default=3600, ge=60, le=3600)
    max_users: int = Field(default=10, ge=1, le=100)
    ai_enabled: bool = False
    journal_bucket: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    backup_bucket: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    installation_id: str | None = None

    @model_validator(mode="after")
    def validate_boundary(self) -> CloudSettings:
        origin = urlsplit(self.public_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username
                or origin.password or origin.path or origin.query or origin.fragment):
            raise ValueError("BETA_PUBLIC_ORIGIN must be an exact HTTPS origin")
        if not re.fullmatch(r"ap-northeast-1_[A-Za-z0-9]+", self.cognito_pool_id):
            raise ValueError("A Tokyo Cognito user pool is required")
        if not re.fullmatch(
            r"https://[a-z0-9-]+\.auth\.ap-northeast-1\.amazoncognito\.com",
            self.cognito_domain,
        ):
            raise ValueError("Use the configured Tokyo Cognito managed domain")
        if not self.root.is_absolute():
            raise ValueError("BETA_ROOT must be a dedicated absolute directory")
        if self.root.is_symlink():
            raise ValueError("BETA_ROOT cannot be a symlink")
        if self.journal_bucket and not self.installation_id:
            raise ValueError("Set a fixed installation identity with the journal")
        if self.ai_enabled and not self.journal_bucket:
            raise ValueError("Paid AI requires the shared remote journal")
        self.root = self.root.resolve()
        return self

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.ap-northeast-1.amazonaws.com/{self.cognito_pool_id}"

    @property
    def callback(self) -> str:
        return self.public_origin + "/api/auth/callback"
