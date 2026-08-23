from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseModel):
    environment: str
    host: Literal["127.0.0.1", "localhost", "::1"]
    port: int = Field(ge=1, le=65535)
    frontend_origins: tuple[str, ...]

    @field_validator("frontend_origins")
    @classmethod
    def validate_local_origins(cls, origins: tuple[str, ...]) -> tuple[str, ...]:
        if not origins:
            raise ValueError("FRONTEND_ORIGINS must contain at least one origin")
        for origin in origins:
            if origin == "*":
                raise ValueError("Wildcard CORS origins are forbidden")
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise ValueError("Only loopback frontend origins are allowed")
        return origins


class StorageSettings(BaseModel):
    data_dir: Path
    max_upload_bytes: int = Field(gt=0)


class DatabaseSettings(BaseModel):
    url: str


class JobSettings(BaseModel):
    max_concurrency: int = Field(ge=1)
    thread_workers: int = Field(ge=1)
    poll_interval_ms: int = Field(ge=100)


class ImageProcessingSettings(BaseModel):
    max_image_pixels: int = Field(gt=0)
    max_image_edge: int = Field(gt=0)
    opencv_num_threads: int = Field(ge=1)


class RegistrationSettings(BaseModel):
    min_feature_matches: int = Field(ge=4)
    min_inliers: int = Field(ge=4)
    min_inlier_ratio: float = Field(ge=0, le=1)
    max_reprojection_error_px: float = Field(gt=0)
    min_ecc_score: float = Field(ge=0, le=1)
    min_edge_overlap: float = Field(ge=0, le=1)
    min_template_confidence: float = Field(ge=0, le=1)


class SegmentationSettings(BaseModel):
    mode: Literal["heuristic", "onnx", "combined"]
    model_path: Path | None
    model_version: str | None
    threshold: float = Field(ge=0, le=1)
    tile_size: int = Field(gt=0)
    tile_overlap: int = Field(ge=0)


class OcrSettings(BaseModel):
    provider: Literal["mock", "openai", "disabled"]
    openai_api_key: SecretStr | None
    primary_model: str | None
    fallback_model: str | None
    image_detail: Literal["low", "high", "auto"]
    reasoning_effort: Literal["low", "medium", "high"]
    max_calls_per_job: int = Field(ge=1)
    max_output_tokens: int = Field(ge=1)
    timeout_seconds: int = Field(ge=1)
    max_retries: int = Field(ge=0)
    prompt_version: str
    schema_version: str
    initial_image_detail: Literal["low", "high", "auto"] = "high"
    uncertain_retry_image_detail: Literal["low", "high", "auto"] = "high"
    retry_uncertain_high_detail: bool = False
    max_concurrency: int = Field(default=2, ge=1, le=4)
    paddle_presence_audit_enabled: bool = False


class LoggingSettings(BaseModel):
    level: str
    file: Path


class GoogleSheetsSettings(BaseModel):
    enabled: bool
    spreadsheet_id: str | None
    credentials_path: Path | None
    frontend_base_url: str
    spreadsheet_title: str = "繁殖台帳 OCR"
    document_sheet_name: str = "帳票一覧"
    sale_sheet_name: str = "売却子牛一覧"
    calving_sheet_name: str = "繁殖履歴"
    edit_queue_sheet_name: str = "_OCR編集キュー"
    edit_poll_interval_seconds: int = Field(default=5, ge=1, le=300)

    @field_validator("frontend_base_url")
    @classmethod
    def validate_frontend_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("GOOGLE_SHEETS_FRONTEND_BASE_URL must be a loopback URL")
        return value.rstrip("/") + "/"


class GoogleOAuthSettings(BaseModel):
    enabled: bool
    client_id: str | None
    client_secret: SecretStr | None
    jwt_secret: SecretStr | None
    redirect_uri: str
    frontend_url: str
    session_cookie_name: str = "ledger_session"
    state_cookie_name: str = "ledger_google_oauth_state"
    session_ttl_seconds: int = Field(ge=300, le=2_592_000)
    state_ttl_seconds: int = Field(ge=60, le=1_800)
    secure_cookie: bool
    picker_api_key: str | None = None
    picker_app_id: str | None = None

    @field_validator("redirect_uri", "frontend_url")
    @classmethod
    def validate_oauth_url(cls, value: str) -> str:
        parsed = urlparse(value)
        is_loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
            raise ValueError("Google OAuth URLs must use HTTPS except on loopback hosts")
        return value.rstrip("/") if parsed.path in {"", "/"} else value

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.client_id
            and self.client_secret is not None
            and self.jwt_secret is not None
        )

    @property
    def picker_configured(self) -> bool:
        return bool(
            self.configured
            and self.picker_api_key
            and self.picker_app_id
            and self.picker_project_matches
        )

    @property
    def picker_project_matches(self) -> bool:
        if not self.client_id or not self.picker_app_id:
            return False
        project_number = self.client_id.partition("-")[0]
        return project_number.isdigit() and project_number == self.picker_app_id

    @property
    def picker_configuration_error(self) -> str | None:
        if not self.picker_api_key or not self.picker_app_id:
            return "Google Picker APIキーとApp IDが設定されていません。"
        if not self.picker_project_matches:
            return (
                "PickerのApp IDがOAuthクライアントのGoogle Cloudプロジェクト番号と"
                "一致していません。同じプロジェクトでAPIキーを作成してください。"
            )
        return None


class RawSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_env: str = Field(default="local", validation_alias="APP_ENV")
    app_host: Literal["127.0.0.1", "localhost", "::1"] = Field(
        default="127.0.0.1", validation_alias="APP_HOST"
    )
    app_port: int = Field(default=8000, validation_alias="APP_PORT")
    app_log_level: str = Field(default="INFO", validation_alias="APP_LOG_LEVEL")
    frontend_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        validation_alias="FRONTEND_ORIGINS",
    )
    data_dir: Path = Field(default=Path("./data"), validation_alias="DATA_DIR")
    database_url: str = Field(default="sqlite:///./data/app.db", validation_alias="DATABASE_URL")
    log_file: Path = Field(default=Path("./data/logs/app.log"), validation_alias="LOG_FILE")
    max_upload_bytes: int = Field(default=20_971_520, validation_alias="MAX_UPLOAD_BYTES")
    max_image_pixels: int = Field(default=30_000_000, validation_alias="MAX_IMAGE_PIXELS")
    max_image_edge: int = Field(default=8000, validation_alias="MAX_IMAGE_EDGE")
    job_max_concurrency: int = Field(default=1, validation_alias="JOB_MAX_CONCURRENCY")
    job_thread_workers: int = Field(default=1, validation_alias="JOB_THREAD_WORKERS")
    job_poll_interval_ms: int = Field(default=1000, validation_alias="JOB_POLL_INTERVAL_MS")
    ocr_provider: Literal["mock", "openai", "disabled"] = Field(
        default="mock", validation_alias="OCR_PROVIDER"
    )
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_primary_model: str | None = Field(default=None, validation_alias="OPENAI_PRIMARY_MODEL")
    openai_fallback_model: str | None = Field(
        default=None, validation_alias="OPENAI_FALLBACK_MODEL"
    )
    openai_image_detail: Literal["low", "high", "auto"] = Field(
        default="high", validation_alias="OPENAI_IMAGE_DETAIL"
    )
    openai_initial_image_detail: Literal["low", "high", "auto"] = Field(
        default="high", validation_alias="OPENAI_INITIAL_IMAGE_DETAIL"
    )
    openai_retry_uncertain_high_detail: bool = Field(
        default=False, validation_alias="OPENAI_RETRY_UNCERTAIN_HIGH_DETAIL"
    )
    openai_max_concurrency: int = Field(
        default=2, validation_alias="OPENAI_MAX_CONCURRENCY"
    )
    paddle_presence_audit_enabled: bool = Field(
        default=False, validation_alias="PADDLE_PRESENCE_AUDIT_ENABLED"
    )
    openai_reasoning_effort: Literal["low", "medium", "high"] = Field(
        default="low", validation_alias="OPENAI_REASONING_EFFORT"
    )
    openai_max_calls_per_job: int = Field(default=8, validation_alias="OPENAI_MAX_CALLS_PER_JOB")
    openai_max_output_tokens: int = Field(default=4000, validation_alias="OPENAI_MAX_OUTPUT_TOKENS")
    openai_timeout_seconds: int = Field(default=60, validation_alias="OPENAI_TIMEOUT_SECONDS")
    openai_max_retries: int = Field(default=2, validation_alias="OPENAI_MAX_RETRIES")
    openai_prompt_version: str = Field(
        default="mapped-compact-v1", validation_alias="OPENAI_PROMPT_VERSION"
    )
    openai_schema_version: str = Field(default="1.0", validation_alias="OPENAI_SCHEMA_VERSION")
    handwriting_segmenter_mode: Literal["heuristic", "onnx", "combined"] = Field(
        default="heuristic", validation_alias="HANDWRITING_SEGMENTER_MODE"
    )
    handwriting_model_path: Path | None = Field(
        default=None, validation_alias="HANDWRITING_MODEL_PATH"
    )
    handwriting_model_version: str | None = Field(
        default=None, validation_alias="HANDWRITING_MODEL_VERSION"
    )
    handwriting_threshold: float = Field(default=0.55, validation_alias="HANDWRITING_THRESHOLD")
    handwriting_tile_size: int = Field(default=1024, validation_alias="HANDWRITING_TILE_SIZE")
    handwriting_tile_overlap: int = Field(default=128, validation_alias="HANDWRITING_TILE_OVERLAP")
    opencv_num_threads: int = Field(default=1, validation_alias="OPENCV_NUM_THREADS")
    min_feature_matches: int = Field(default=30, validation_alias="MIN_FEATURE_MATCHES")
    min_inliers: int = Field(default=20, validation_alias="MIN_INLIERS")
    min_inlier_ratio: float = Field(default=0.4, validation_alias="MIN_INLIER_RATIO")
    max_reprojection_error_px: float = Field(
        default=5.0, validation_alias="MAX_REPROJECTION_ERROR_PX"
    )
    min_ecc_score: float = Field(default=0.75, validation_alias="MIN_ECC_SCORE")
    min_edge_overlap: float = Field(default=0.7, validation_alias="MIN_EDGE_OVERLAP")
    min_template_confidence: float = Field(default=0.75, validation_alias="MIN_TEMPLATE_CONFIDENCE")
    google_sheets_enabled: bool = Field(default=False, validation_alias="GOOGLE_SHEETS_ENABLED")
    google_sheets_spreadsheet_id: str | None = Field(
        default=None, validation_alias="GOOGLE_SHEETS_SPREADSHEET_ID"
    )
    google_sheets_credentials_path: Path | None = Field(
        default=None, validation_alias="GOOGLE_SHEETS_CREDENTIALS_PATH"
    )
    google_sheets_frontend_base_url: str = Field(
        default="http://127.0.0.1:5173/",
        validation_alias="GOOGLE_SHEETS_FRONTEND_BASE_URL",
    )
    google_sheets_spreadsheet_title: str = Field(
        default="繁殖台帳 OCR",
        validation_alias="GOOGLE_SHEETS_SPREADSHEET_TITLE",
    )
    google_sheets_edit_poll_interval_seconds: int = Field(
        default=5,
        validation_alias="GOOGLE_SHEETS_EDIT_POLL_INTERVAL_SECONDS",
    )
    google_oauth_enabled: bool = Field(default=False, validation_alias="GOOGLE_OAUTH_ENABLED")
    google_oauth_client_id: str | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_CLIENT_ID"
    )
    google_oauth_client_secret: SecretStr | None = Field(
        default=None, validation_alias="GOOGLE_OAUTH_CLIENT_SECRET"
    )
    auth_jwt_secret: SecretStr | None = Field(default=None, validation_alias="AUTH_JWT_SECRET")
    google_oauth_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/api/v1/auth/google/callback",
        validation_alias="GOOGLE_OAUTH_REDIRECT_URI",
    )
    google_oauth_frontend_url: str = Field(
        default="http://127.0.0.1:5173/",
        validation_alias="GOOGLE_OAUTH_FRONTEND_URL",
    )
    google_picker_api_key: str | None = Field(
        default=None,
        validation_alias="GOOGLE_PICKER_API_KEY",
    )
    google_picker_app_id: str | None = Field(
        default=None,
        validation_alias="GOOGLE_PICKER_APP_ID",
    )
    auth_session_ttl_seconds: int = Field(
        default=604_800, validation_alias="AUTH_SESSION_TTL_SECONDS"
    )
    auth_state_ttl_seconds: int = Field(default=600, validation_alias="AUTH_STATE_TTL_SECONDS")
    auth_secure_cookie: bool = Field(default=False, validation_alias="AUTH_SECURE_COOKIE")


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppSettings
    storage: StorageSettings
    database: DatabaseSettings
    jobs: JobSettings
    image_processing: ImageProcessingSettings
    registration: RegistrationSettings
    segmentation: SegmentationSettings
    ocr: OcrSettings
    logging: LoggingSettings
    google_sheets: GoogleSheetsSettings
    google_oauth: GoogleOAuthSettings

    @classmethod
    def load(cls, env_file: str | Path | None = ".env") -> Settings:
        if env_file is None:
            raw = RawSettings(_env_file=None)
        else:
            primary = Path(env_file)
            env_sources: str | Path | tuple[Path, Path] = primary
            if primary.name == ".env":
                env_sources = (primary, primary.with_name(".env.local"))
            raw = RawSettings(_env_file=env_sources)
        return cls.from_raw(raw)

    @classmethod
    def from_raw(cls, raw: RawSettings) -> Settings:
        data_dir = raw.data_dir.expanduser().resolve()
        log_file = raw.log_file.expanduser().resolve()
        _require_descendant(log_file, data_dir, "LOG_FILE")
        database_url = _normalize_database_url(raw.database_url, data_dir)
        origins = tuple(item.strip() for item in raw.frontend_origins.split(",") if item.strip())
        model_path = raw.handwriting_model_path
        if model_path is not None:
            model_path = model_path.expanduser().resolve()
        credentials_path = raw.google_sheets_credentials_path
        if credentials_path is not None:
            credentials_path = credentials_path.expanduser().resolve()
        if (
            raw.google_sheets_enabled
            and not raw.google_oauth_enabled
            and not raw.google_sheets_spreadsheet_id
        ):
            raise ValueError(
                "GOOGLE_SHEETS_SPREADSHEET_ID is required when Sheets sync uses no OAuth account"
            )
        if raw.google_oauth_enabled:
            if not raw.google_oauth_client_id or raw.google_oauth_client_secret is None:
                raise ValueError(
                    "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET are required"
                )
            if raw.auth_jwt_secret is None or len(raw.auth_jwt_secret.get_secret_value()) < 32:
                raise ValueError("AUTH_JWT_SECRET must contain at least 32 characters")

        return cls(
            app=AppSettings(
                environment=raw.app_env,
                host=raw.app_host,
                port=raw.app_port,
                frontend_origins=origins,
            ),
            storage=StorageSettings(
                data_dir=data_dir,
                max_upload_bytes=raw.max_upload_bytes,
            ),
            database=DatabaseSettings(url=database_url),
            jobs=JobSettings(
                max_concurrency=raw.job_max_concurrency,
                thread_workers=raw.job_thread_workers,
                poll_interval_ms=raw.job_poll_interval_ms,
            ),
            image_processing=ImageProcessingSettings(
                max_image_pixels=raw.max_image_pixels,
                max_image_edge=raw.max_image_edge,
                opencv_num_threads=raw.opencv_num_threads,
            ),
            registration=RegistrationSettings(
                min_feature_matches=raw.min_feature_matches,
                min_inliers=raw.min_inliers,
                min_inlier_ratio=raw.min_inlier_ratio,
                max_reprojection_error_px=raw.max_reprojection_error_px,
                min_ecc_score=raw.min_ecc_score,
                min_edge_overlap=raw.min_edge_overlap,
                min_template_confidence=raw.min_template_confidence,
            ),
            segmentation=SegmentationSettings(
                mode=raw.handwriting_segmenter_mode,
                model_path=model_path,
                model_version=raw.handwriting_model_version,
                threshold=raw.handwriting_threshold,
                tile_size=raw.handwriting_tile_size,
                tile_overlap=raw.handwriting_tile_overlap,
            ),
            ocr=OcrSettings(
                provider=raw.ocr_provider,
                openai_api_key=raw.openai_api_key,
                primary_model=raw.openai_primary_model,
                fallback_model=raw.openai_fallback_model,
                image_detail=raw.openai_image_detail,
                initial_image_detail=raw.openai_initial_image_detail,
                uncertain_retry_image_detail=raw.openai_image_detail,
                retry_uncertain_high_detail=raw.openai_retry_uncertain_high_detail,
                max_concurrency=raw.openai_max_concurrency,
                paddle_presence_audit_enabled=raw.paddle_presence_audit_enabled,
                reasoning_effort=raw.openai_reasoning_effort,
                max_calls_per_job=raw.openai_max_calls_per_job,
                max_output_tokens=raw.openai_max_output_tokens,
                timeout_seconds=raw.openai_timeout_seconds,
                max_retries=raw.openai_max_retries,
                prompt_version=raw.openai_prompt_version,
                schema_version=raw.openai_schema_version,
            ),
            logging=LoggingSettings(level=raw.app_log_level.upper(), file=log_file),
            google_sheets=GoogleSheetsSettings(
                enabled=raw.google_sheets_enabled,
                spreadsheet_id=raw.google_sheets_spreadsheet_id,
                credentials_path=credentials_path,
                frontend_base_url=raw.google_sheets_frontend_base_url,
                spreadsheet_title=raw.google_sheets_spreadsheet_title,
                edit_poll_interval_seconds=raw.google_sheets_edit_poll_interval_seconds,
            ),
            google_oauth=GoogleOAuthSettings(
                enabled=raw.google_oauth_enabled,
                client_id=raw.google_oauth_client_id,
                client_secret=raw.google_oauth_client_secret,
                jwt_secret=raw.auth_jwt_secret,
                redirect_uri=raw.google_oauth_redirect_uri,
                frontend_url=raw.google_oauth_frontend_url,
                session_ttl_seconds=raw.auth_session_ttl_seconds,
                state_ttl_seconds=raw.auth_state_ttl_seconds,
                secure_cookie=raw.auth_secure_cookie,
                picker_api_key=raw.google_picker_api_key,
                picker_app_id=raw.google_picker_app_id,
            ),
        )


def _require_descendant(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must be inside DATA_DIR") from exc


def _normalize_database_url(url: str, data_dir: Path) -> str:
    memory_urls = {"sqlite:///:memory:", "sqlite+pysqlite:///:memory:"}
    if url in memory_urls:
        return "sqlite+pysqlite:///:memory:"
    prefixes = ("sqlite:///", "sqlite+pysqlite:///")
    prefix = next((candidate for candidate in prefixes if url.startswith(candidate)), None)
    if prefix is None:
        raise ValueError("Only SQLite DATABASE_URL values are supported")
    path_text = url[len(prefix) :]
    database_path = Path(path_text).expanduser().resolve()
    _require_descendant(database_path, data_dir, "DATABASE_URL")
    return f"sqlite+pysqlite:///{database_path.as_posix()}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()
