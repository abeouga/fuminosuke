from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, model_validator
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
    database_url: SecretStr | None = None
    object_bucket: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    # Template packages (*.llmocr-template) installed into each user on first use.
    seed_template_dir: Path | None = None
    # Cognito subs (comma separated) that get admin_seed_template_dir instead of seed_template_dir.
    admin_subjects: str = ""
    admin_seed_template_dir: Path | None = None
    # ECS service of this task: during a rolling update, relay to its previous task (handoff.py).
    ecs_service: str | None = None
    relay_port: int = 8000
    # Alternative to database_url, so the password can come from its own secret (ECS/RDS).
    database_host: str | None = None
    database_port: int = 5432
    database_name: str | None = None
    database_user: str | None = None
    database_password: SecretStr | None = None

    @model_validator(mode="after")
    def validate_boundary(self) -> CloudSettings:
        origin = urlsplit(self.public_origin)
        if (origin.scheme != "https" and not self.loopback) or (
                not origin.hostname or origin.username
                or origin.password or origin.path or origin.query or origin.fragment):
            raise ValueError("BETA_PUBLIC_ORIGIN must be an exact HTTPS origin")
        if not re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d_[A-Za-z0-9]+", self.cognito_pool_id):
            raise ValueError("A Cognito user pool ID is required")
        if not re.fullmatch(
            rf"https://[a-z0-9-]+\.auth\.{re.escape(self.cognito_region)}\.amazoncognito\.com",
            self.cognito_domain,
        ):
            raise ValueError("Use the Cognito managed domain in the user pool's Region")
        if self.seed_template_dir is not None and not self.seed_template_dir.is_dir():
            raise ValueError("BETA_SEED_TEMPLATE_DIR must be an existing directory")
        if self.admin_seed_template_dir is not None and not self.admin_seed_template_dir.is_dir():
            raise ValueError("BETA_ADMIN_SEED_TEMPLATE_DIR must be an existing directory")
        if self.database_url is None and self.database_host is not None:
            if not (self.database_name and self.database_user and self.database_password):
                raise ValueError("BETA_DATABASE_HOST needs NAME, USER and PASSWORD")
            self.database_url = SecretStr(
                f"postgresql+psycopg://{quote(self.database_user, safe='')}:"
                f"{quote(self.database_password.get_secret_value(), safe='')}@"
                f"{self.database_host}:{self.database_port}/{self.database_name}?sslmode=require"
            )
        if not self.root.is_absolute():
            raise ValueError("BETA_ROOT must be a dedicated absolute directory")
        if self.root.is_symlink():
            raise ValueError("BETA_ROOT cannot be a symlink")
        if self.journal_bucket and not self.installation_id:
            raise ValueError("Set a fixed installation identity with the journal")
        if self.ai_enabled and not self.journal_bucket:
            raise ValueError("Paid AI requires the shared remote journal")
        if self.database_url is not None:
            if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
                raise ValueError("Cloud persistence requires PostgreSQL/psycopg")
            if not self.object_bucket or not self.installation_id:
                raise ValueError("PostgreSQL cloud requires object bucket and fixed installation")
        elif self.object_bucket is not None:
            raise ValueError("S3 business storage requires PostgreSQL")
        if os.environ.get("AWS_EXECUTION_ENV") == "AWS_ECS_FARGATE" and (
            self.loopback or self.database_url is None
            or not self.journal_bucket or not self.backup_bucket
        ):
            raise ValueError("Fargate requires PostgreSQL, business objects, journal and backups")
        self.root = self.root.resolve()
        return self

    @property
    def cognito_region(self) -> str:
        return self.cognito_pool_id.split("_", 1)[0]

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_pool_id}"

    @property
    def callback(self) -> str:
        return self.public_origin + "/api/auth/callback"

    @property
    def loopback(self) -> bool:
        """Local trial with the real Cognito pool: http only on this machine."""
        pattern = r"http://(localhost|127\.0\.0\.1):\d{2,5}"
        return re.fullmatch(pattern, self.public_origin) is not None

    def template_dir_for(self, subject: str) -> Path | None:
        admins = {value.strip() for value in self.admin_subjects.split(",") if value.strip()}
        if subject in admins and self.admin_seed_template_dir is not None:
            return self.admin_seed_template_dir
        return self.seed_template_dir
