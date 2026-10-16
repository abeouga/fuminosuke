from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseModel):
    environment: str
    host: Literal["127.0.0.1", "localhost", "::1"]
    port: int = Field(ge=1, le=65535)
    frontend_origins: tuple[str, ...]
    editing_api_enabled: bool = False
    template_analysis_enabled: bool = False
    print_pdf_renderer: Literal["appcontainer", "wsl", "linux"] = "appcontainer"
    print_wsl_distribution: str = Field(
        default="Ubuntu-22.04", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
    )
    print_wsl_runtime_id: str | None = None

    @field_validator("print_wsl_runtime_id")
    @classmethod
    def validate_print_runtime_id(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("Invalid print runtime identity: expected a lowercase SHA-256")
        return value

    @field_validator("frontend_origins")
    @classmethod
    def validate_local_origins(
        cls, origins: tuple[str, ...], info: ValidationInfo,
    ) -> tuple[str, ...]:
        if not origins:
            raise ValueError("FRONTEND_ORIGINS must contain at least one origin")
        for origin in origins:
            if origin == "*":
                raise ValueError("Wildcard CORS origins are forbidden")
            parsed = urlparse(origin)
            if info.data.get("environment") == "cloud":
                if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                        or parsed.password or parsed.path or parsed.query or parsed.fragment):
                    raise ValueError("Cloud frontend origins must be exact HTTPS origins")
                continue
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
    text_recognition_toggle_enabled: bool = False
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
    schema_version: Literal["2.0"]
    initial_image_detail: Literal["low", "high", "auto"] = "high"
    uncertain_retry_image_detail: Literal["low", "high", "auto"] = "high"
    retry_uncertain_high_detail: bool = False
    max_concurrency: int = Field(default=2, ge=1, le=4)
    paddle_presence_audit_enabled: bool = False


class LoggingSettings(BaseModel):
    level: str
    file: Path


class VoiceSettings(BaseModel):
    provider: Literal["openai", "disabled"] = "openai"
    stt_model: str = "gpt-transcribe"
    proposal_model: str = "gpt-6.1-sol"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "marin"
    max_audio_bytes: int = Field(default=10_485_760, ge=1024, le=10_485_760)
    max_record_seconds: int = Field(default=60, ge=1, le=60)
    ffmpeg_path: str | None = None


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
    editing_api_enabled: bool = Field(default=False, validation_alias="EDITING_API_ENABLED")
    template_analysis_enabled: bool = Field(
        default=False, validation_alias="TEMPLATE_ANALYSIS_ENABLED"
    )
    print_pdf_renderer: Literal["appcontainer", "wsl", "linux"] = Field(
        default="appcontainer", validation_alias="PRINT_PDF_RENDERER"
    )
    print_wsl_distribution: str = Field(
        default="Ubuntu-22.04", validation_alias="PRINT_WSL_DISTRIBUTION"
    )
    print_wsl_runtime_id: str | None = Field(default=None, validation_alias="PRINT_WSL_RUNTIME_ID")
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
    ocr_text_recognition_toggle_enabled: bool = Field(
        default=False, validation_alias="OCR_TEXT_RECOGNITION_TOGGLE_ENABLED"
    )
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_api_key_file: Path | None = Field(default=None, validation_alias="OPENAI_API_KEY_FILE")
    voice_provider: Literal["openai", "disabled"] = Field(
        default="openai", validation_alias="VOICE_PROVIDER"
    )
    voice_stt_model: str = Field(default="gpt-transcribe", validation_alias="VOICE_STT_MODEL")
    voice_proposal_model: str = Field(
        default="gpt-6.1-sol", validation_alias="VOICE_PROPOSAL_MODEL"
    )
    voice_tts_model: str = Field(default="gpt-4o-mini-tts", validation_alias="VOICE_TTS_MODEL")
    voice_tts_voice: str = Field(default="marin", validation_alias="VOICE_TTS_VOICE")
    voice_max_audio_bytes: int = Field(default=10_485_760, validation_alias="VOICE_MAX_AUDIO_BYTES")
    voice_max_record_seconds: int = Field(default=60, validation_alias="VOICE_MAX_RECORD_SECONDS")
    voice_ffmpeg_path: str | None = Field(default=None, validation_alias="VOICE_FFMPEG_PATH")
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
    openai_max_concurrency: int = Field(default=2, validation_alias="OPENAI_MAX_CONCURRENCY")
    paddle_presence_audit_enabled: bool = Field(
        default=False, validation_alias="PADDLE_PRESENCE_AUDIT_ENABLED"
    )
    openai_reasoning_effort: Literal["low", "medium", "high"] = Field(
        default="low", validation_alias="OPENAI_REASONING_EFFORT"
    )
    openai_max_calls_per_job: int = Field(default=16, validation_alias="OPENAI_MAX_CALLS_PER_JOB")
    openai_max_output_tokens: int = Field(
        default=12000, validation_alias="OPENAI_MAX_OUTPUT_TOKENS"
    )
    openai_timeout_seconds: int = Field(default=60, validation_alias="OPENAI_TIMEOUT_SECONDS")
    openai_max_retries: int = Field(default=2, validation_alias="OPENAI_MAX_RETRIES")
    openai_prompt_version: str = Field(
        default="mapped-compact-v1", validation_alias="OPENAI_PROMPT_VERSION"
    )
    openai_schema_version: Literal["2.0"] = Field(
        default="2.0", validation_alias="OPENAI_SCHEMA_VERSION"
    )
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
    voice: VoiceSettings = dataclass_field(default_factory=VoiceSettings)

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
        if raw.openai_api_key_file is not None:
            path = raw.openai_api_key_file
            if (raw.app_env != "cloud" or raw.openai_api_key is not None
                    or not path.is_absolute() or path.is_symlink() or not path.is_file()
                    or path.stat().st_size > 8192):
                raise ValueError("Use one dedicated cloud API secret file")
            secret = path.read_text().strip()
            if not secret or any(c.isspace() for c in secret):
                raise ValueError("Invalid API secret file")
            raw.openai_api_key = SecretStr(secret)
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
        return cls(
            app=AppSettings(
                environment=raw.app_env,
                host=raw.app_host,
                port=raw.app_port,
                frontend_origins=origins,
                editing_api_enabled=raw.editing_api_enabled,
                template_analysis_enabled=raw.template_analysis_enabled,
                print_pdf_renderer=raw.print_pdf_renderer,
                print_wsl_distribution=raw.print_wsl_distribution,
                print_wsl_runtime_id=raw.print_wsl_runtime_id,
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
                text_recognition_toggle_enabled=raw.ocr_text_recognition_toggle_enabled,
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
            voice=VoiceSettings(
                provider=raw.voice_provider, stt_model=raw.voice_stt_model,
                proposal_model=raw.voice_proposal_model, tts_model=raw.voice_tts_model,
                tts_voice=raw.voice_tts_voice, max_audio_bytes=raw.voice_max_audio_bytes,
                max_record_seconds=raw.voice_max_record_seconds,
                ffmpeg_path=raw.voice_ffmpeg_path or shutil.which("ffmpeg"),
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
