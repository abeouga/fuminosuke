from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob, Template, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.domain.field_targets import is_supported_field_path
from app.domain.individual_id import normalize_individual_id
from app.domain.job_inputs import job_input_bounds
from app.domain.job_state import require_transition
from app.domain.sale_notes import SALE_NOTE_SEX_SYMBOLS
from app.domain.sire_names import SIRE_NAME_ALLOWED_SUFFIXES
from app.domain.template_config import TemplateConfig, parse_template_config
from app.domain.template_quality import has_full_image_boundary_fallback
from app.image_processing.image_loader import ImageLoader, LoadedImage
from app.jobs.runner import JobRunner
from app.layout_analysis.models import LayoutDefinition
from app.repositories.job_repository import JobRepository
from app.repositories.template_repository import TemplateRepository
from app.services.sire_name_service import SireNameService
from app.storage.local_storage import LocalStorageService, StoragePathError

ATTENTION_JOB_STATUSES = (
    JobStatus.CREATED,
    JobStatus.QUEUED,
    JobStatus.PROCESSING,
    JobStatus.REVIEW_REQUIRED,
    JobStatus.FAILED,
    JobStatus.INTERRUPTED,
)


@dataclass(frozen=True, slots=True)
class UploadPayload:
    content: bytes
    declared_media_type: str | None


class JobService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        storage: LocalStorageService,
        runner: JobRunner,
    ) -> None:
        self._settings = settings
        self._database = database
        self._storage = storage
        self._runner = runner
        self._loader = ImageLoader(settings.storage, settings.image_processing)

    async def create_template_job(
        self,
        *,
        name: str,
        mode: TemplateMode,
        uploads: list[UploadPayload],
        idempotency_key: str | None,
    ) -> ProcessingJob:
        cleaned_name = name.strip()
        if not cleaned_name or len(cleaned_name) > 200:
            raise AppError(
                status=422,
                code="VALIDATION_FAILED",
                title="Invalid template name",
                detail="テンプレート名は1〜200文字で指定してください。",
            )
        self._ensure_template_name_available(cleaned_name, idempotency_key)
        self.validate_template_input_count(mode, len(uploads))
        self._validate_template_batch_limits(uploads)
        loaded = [self._loader.load(item.content, item.declared_media_type) for item in uploads]
        config_snapshot = {"name": cleaned_name, "mode": mode.value}
        job, created = self._create_job_record(
            job_type=JobType.PREPARE_TEMPLATE,
            mode=mode,
            loaded=loaded,
            config_snapshot=config_snapshot,
            idempotency_key=idempotency_key,
        )
        if created:
            self._persist_inputs(job, uploads, loaded)
            self._mark_queued(job.id)
            await self._runner.enqueue(job.id)
        return self.get(job.id)

    async def create_ocr_job(
        self,
        *,
        template_version_id: str,
        mother_individual_id: str,
        upload: UploadPayload,
        ocr_enabled: bool,
        idempotency_key: str | None,
    ) -> ProcessingJob:
        try:
            normalized_mother_id = normalize_individual_id(mother_individual_id)
        except ValueError as exc:
            raise AppError(
                status=422,
                code="INVALID_INDIVIDUAL_ID",
                title="Invalid mother individual identification number",
                detail="母牛の個体識別番号は10桁の数字で入力してください。",
            ) from exc
        with self._database.session() as session:
            template_version = session.get(TemplateVersion, template_version_id)
            if template_version is None:
                raise AppError(
                    status=404,
                    code="TEMPLATE_NOT_FOUND",
                    title="Template version not found",
                    detail="指定されたテンプレート版が見つかりません。",
                )
            if template_version.status != TemplateVersionStatus.ACTIVE.value:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Template version is not active",
                    detail="OCRにはACTIVEテンプレート版を指定してください。",
                )
            if has_full_image_boundary_fallback(template_version.quality_json):
                raise AppError(
                    status=409,
                    code="TEMPLATE_DOCUMENT_BOUNDARY_NOT_VERIFIED",
                    title="Template document boundary must be detected",
                    detail=(
                        "帳票外形の検出に失敗して画像全体を使用したテンプレートは"
                        "OCRに利用できません。帳票全体が写る画像でテンプレートを"
                        "作り直してください。"
                    ),
                )
            clean_artifact = session.get(
                JobArtifact,
                template_version.clean_template_artifact_id,
            )
            if clean_artifact is None:
                raise AppError(
                    status=409,
                    code="TEMPLATE_ARTIFACT_NOT_READY",
                    title="Template artifact is not ready",
                    detail="テンプレート画像が見つかりません。再検出してからお試しください。",
                )
            clean_template_bytes = self._storage.read_bytes(clean_artifact.relative_path)
            clean_template_sha256 = hashlib.sha256(clean_template_bytes).hexdigest()
            if clean_template_sha256 != clean_artifact.sha256:
                raise AppError(
                    status=409,
                    code="TEMPLATE_ARTIFACT_CHANGED",
                    title="Template artifact changed",
                    detail=(
                        "テンプレート画像の整合性を確認できませんでした。"
                        "記入欄を再検出してからお試しください。"
                    ),
                )
            config_relative_path = template_version.config_relative_path
            template_config_bytes = self._storage.read_bytes(config_relative_path)
            template_config = parse_template_config(
                template_config_bytes.decode("utf-8")
            )
            session.refresh(template_version)
            if (
                template_version.status != TemplateVersionStatus.ACTIVE.value
                or template_version.clean_template_artifact_id != clean_artifact.id
                or template_version.config_relative_path != config_relative_path
                or self._storage.read_bytes(clean_artifact.relative_path)
                != clean_template_bytes
                or self._storage.read_bytes(config_relative_path)
                != template_config_bytes
            ):
                raise AppError(
                    status=409,
                    code="TEMPLATE_CHANGED_DURING_ACCEPTANCE",
                    title="Template changed during OCR acceptance",
                    detail=(
                        "受付中にテンプレート設定が変更されました。"
                        "内容を確認して、もう一度読み取りを開始してください。"
                    ),
                )
            mapping_snapshot = self._field_mapping_snapshot(
                template_version,
                required=ocr_enabled and self._settings.ocr.provider == "openai",
                config=template_config,
            )
            if template_version.quality_json.get("layoutVerified") is not True:
                raise AppError(
                    status=409,
                    code="TEMPLATE_LAYOUT_NOT_VERIFIED",
                    title="Template layout is not verified",
                    detail=(
                        "テンプレート設定が変更されています。テンプレート画面で"
                        "記入欄を再検出してからOCRを開始してください。"
                    ),
                )
            template_snapshot = self._template_snapshot(
                template_version=template_version,
                clean_artifact=clean_artifact,
                clean_template_sha256=clean_template_sha256,
                config=template_config,
            )
            sire_name_snapshot = self._sire_name_snapshot(session) if ocr_enabled else {
                "sireNamePool": [],
                "sireNameAllowedSuffixes": list(SIRE_NAME_ALLOWED_SUFFIXES),
                "sireNamePoolHash": None,
            }
        loaded = [self._loader.load(upload.content, upload.declared_media_type)]
        config_snapshot = {
            "templateVersionId": template_version_id,
            "templateId": template_version.template_id,
            "templateVersionNumber": template_version.version_number,
            "motherIndividualId": normalized_mother_id,
            "ocrEnabled": ocr_enabled,
            "ocrProvider": self._settings.ocr.provider if ocr_enabled else "disabled",
            "schemaVersion": self._settings.ocr.schema_version,
            "promptVersion": self._settings.ocr.prompt_version,
            "templateSnapshot": template_snapshot,
            **mapping_snapshot,
            **sire_name_snapshot,
            "saleNoteSexSymbolPool": list(SALE_NOTE_SEX_SYMBOLS),
        }
        job, created = self._create_job_record(
            job_type=JobType.EXTRACT_DOCUMENT,
            mode=None,
            loaded=loaded,
            config_snapshot=config_snapshot,
            idempotency_key=idempotency_key,
            template_version_id=template_version_id,
        )
        if created:
            self._persist_template_snapshot(
                job,
                clean_template_bytes,
            )
            self._persist_inputs(job, [upload], loaded)
            self._mark_queued(job.id)
            await self._runner.enqueue(job.id)
        return self.get(job.id)

    def get(self, job_id: str) -> ProcessingJob:
        with self._database.session() as session:
            job = JobRepository(session).get(job_id)
            if job is None:
                raise AppError(
                    status=404,
                    code="JOB_NOT_FOUND",
                    title="Job not found",
                    detail="指定されたジョブが見つかりません。",
                )
            session.expunge(job)
            return job

    def list_jobs(
        self,
        *,
        limit: int = 20,
        attention_only: bool = False,
    ) -> list[ProcessingJob]:
        with self._database.session() as session:
            repository = JobRepository(session)
            jobs = (
                repository.list_by_statuses(ATTENTION_JOB_STATUSES, limit=limit)
                if attention_only
                else repository.list(limit=limit)
            )
            for job in jobs:
                session.expunge(job)
            return jobs

    async def retry(self, job_id: str) -> ProcessingJob:
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None:
                raise self._not_found()
            current = JobStatus(job.status)
            if (
                current is JobStatus.FAILED
                and job.error_code == "INCOMPLETE_JOB_INPUTS"
            ):
                raise AppError(
                    status=409,
                    code="INCOMPLETE_JOB_INPUTS",
                    title="Job inputs are incomplete",
                    detail=(
                        "受付中に停止したため、再試行に必要な元画像が保存されていません。"
                        "画像を選び直し、同じ内容を新しいジョブとして登録してください。"
                    ),
                )
            require_transition(current, JobStatus.QUEUED)
            snapshot = dict(job.config_snapshot_json)
            if (
                job.job_type == JobType.EXTRACT_DOCUMENT.value
                and not self._ocr_template_snapshot_is_complete(job)
            ):
                raise AppError(
                    status=409,
                    code="OCR_TEMPLATE_SNAPSHOT_INVALID",
                    title="OCR template snapshot is invalid",
                    detail=(
                        "受付時のテンプレート固定データを確認できないため、"
                        "この処理は安全に再試行できません。"
                        "画像を選び直し、新しい読み取りとして登録してください。"
                    ),
                )
            repository.update(
                job_id,
                status=JobStatus.QUEUED,
                attempt_number=job.attempt_number + 1,
                progress_stage=None,
                progress_percent=0,
                cancel_requested=False,
                error_code=None,
                error_message_safe=None,
                started_at=None,
                completed_at=None,
                config_snapshot_json=snapshot,
            )
            repository.add_event(
                job_id=job_id,
                level="INFO",
                event_code="JOB_RETRY_QUEUED",
                metadata={"attemptNumber": job.attempt_number + 1},
            )
        await self._runner.enqueue(job_id)
        return self.get(job_id)

    def cancel(self, job_id: str) -> ProcessingJob:
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None:
                raise self._not_found()
            current = JobStatus(job.status)
            if current is JobStatus.PROCESSING:
                if job.job_type == JobType.PREPARE_TEMPLATE.value:
                    persisted_statement = select(TemplateVersion.id).where(
                        TemplateVersion.source_job_id == job_id
                    )
                    reprocess_version_id = job.config_snapshot_json.get(
                        "reprocessTemplateVersionId"
                    )
                    if isinstance(reprocess_version_id, str):
                        reprocess_version = session.get(
                            TemplateVersion,
                            reprocess_version_id,
                        )
                        if reprocess_version is not None:
                            persisted_statement = persisted_statement.where(
                                TemplateVersion.template_id
                                == reprocess_version.template_id,
                                TemplateVersion.version_number
                                > reprocess_version.version_number,
                            )
                        else:
                            persisted_statement = persisted_statement.where(
                                TemplateVersion.id != reprocess_version_id
                            )
                    persisted_version_id = session.scalar(
                        persisted_statement.limit(1)
                    )
                    if persisted_version_id is not None:
                        raise AppError(
                            status=409,
                            code="TEMPLATE_PERSISTENCE_COMPLETE",
                            title="Template persistence is already complete",
                            detail=(
                                "テンプレートの保存は完了しています。確認画面が開くまで"
                                "少し待ち、不要な場合はそこで却下してください。"
                            ),
                        )
                repository.update(job_id, cancel_requested=True)
            else:
                require_transition(current, JobStatus.CANCELLED)
                repository.update(
                    job_id,
                    status=JobStatus.CANCELLED,
                    cancel_requested=True,
                    completed_at=datetime.now(UTC),
                )
            repository.add_event(
                job_id=job_id,
                level="INFO",
                event_code="JOB_CANCEL_REQUESTED",
                metadata={"attemptNumber": job.attempt_number},
            )
        return self.get(job_id)

    def delete(self, job_id: str, *, confirm: bool) -> None:
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None:
                raise self._not_found()
            if job.status == JobStatus.PROCESSING.value:
                raise AppError(
                    status=409,
                    code="INVALID_STATE_TRANSITION",
                    title="Processing job cannot be deleted",
                    detail="処理中ジョブは先に取消要求を行ってください。",
                )
            if job.status == JobStatus.CONFIRMED.value and not confirm:
                raise AppError(
                    status=409,
                    code="DELETE_CONFIRMATION_REQUIRED",
                    title="Deletion confirmation required",
                    detail="確定済みジョブの削除には confirm=true が必要です。",
                )
            referenced_version_id = session.scalar(
                select(TemplateVersion.id)
                .where(TemplateVersion.source_job_id == job_id)
                .limit(1)
            )
            if referenced_version_id is not None:
                raise AppError(
                    status=409,
                    code="JOB_IN_USE",
                    title="Job is used by a template version",
                    detail=(
                        "このジョブはテンプレートの元データとして使用中のため削除できません。"
                        "テンプレートを残したまま元画像や生成物を失うことを防止しました。"
                    ),
                    metadata={"templateVersionId": referenced_version_id},
                )
            repository.delete(job_id)
        for directory in ("uploads", "artifacts", "exports"):
            relative = f"{directory}/{job_id}"
            if self._storage.exists(relative):
                self._storage.delete_tree(relative)

    def _create_job_record(
        self,
        *,
        job_type: JobType,
        mode: TemplateMode | None,
        loaded: list[LoadedImage],
        config_snapshot: Mapping[str, object],
        idempotency_key: str | None,
        template_version_id: str | None = None,
    ) -> tuple[ProcessingJob, bool]:
        request_hash = self._request_hash(job_type, loaded, config_snapshot)
        with self._database.session() as session:
            repository = JobRepository(session)
            if idempotency_key:
                existing = repository.find_by_idempotency_key(idempotency_key)
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise AppError(
                            status=409,
                            code="IDEMPOTENCY_KEY_CONFLICT",
                            title="Idempotency key conflict",
                            detail="同じIdempotency-Keyが異なるリクエストで使用されています。",
                        )
                    session.expunge(existing)
                    return existing, False
            try:
                job = repository.create(
                    job_type=job_type,
                    request_hash=request_hash,
                    config_snapshot=config_snapshot,
                    mode=mode,
                    idempotency_key=idempotency_key,
                    image_sha256=(
                        loaded[0].metadata.sha256 if len(loaded) == 1 else None
                    ),
                    template_version_id=template_version_id,
                )
            except IntegrityError:
                session.rollback()
                if not idempotency_key:
                    raise
                existing = repository.find_by_idempotency_key(idempotency_key)
                if existing is None:
                    raise
                if existing.request_hash != request_hash:
                    raise AppError(
                        status=409,
                        code="IDEMPOTENCY_KEY_CONFLICT",
                        title="Idempotency key conflict",
                        detail=(
                            "同じIdempotency-Keyが異なるリクエストで使用されています。"
                        ),
                    ) from None
                session.expunge(existing)
                return existing, False
            session.expunge(job)
            return job, True

    def _persist_inputs(
        self,
        job: ProcessingJob,
        uploads: list[UploadPayload],
        loaded: list[LoadedImage],
    ) -> None:
        try:
            manifest_items: list[dict[str, object]] = []
            with self._database.session() as session:
                repository = JobRepository(session)
                for index, (upload, image) in enumerate(zip(uploads, loaded, strict=True)):
                    suffix = "" if len(uploads) == 1 else f"_{index + 1:03d}"
                    relative_path = (
                        f"uploads/{job.id}/original{suffix}.{image.metadata.extension}"
                    )
                    stored_path = self._storage.write_bytes(relative_path, upload.content)
                    repository.add_input(
                        job_id=job.id,
                        input_index=index,
                        media_type=image.metadata.media_type,
                        format=image.metadata.format,
                        width=image.metadata.width,
                        height=image.metadata.height,
                        file_bytes=image.metadata.file_bytes,
                        sha256=image.metadata.sha256,
                        relative_path=stored_path,
                    )
                    manifest_items.append(
                        {
                            "index": index,
                            "mediaType": image.metadata.media_type,
                            "format": image.metadata.format,
                            "width": image.metadata.width,
                            "height": image.metadata.height,
                            "fileBytes": image.metadata.file_bytes,
                            "sha256": image.metadata.sha256,
                            "relativePath": stored_path,
                        }
                    )
            manifest = {"jobId": job.id, "inputs": manifest_items}
            self._storage.write_text(
                f"uploads/{job.id}/manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        except Exception:
            with self._database.session() as session:
                JobRepository(session).delete(job.id)
            upload_dir = f"uploads/{job.id}"
            if self._storage.exists(upload_dir):
                self._storage.delete_tree(upload_dir)
            raise

    def _persist_template_snapshot(
        self,
        job: ProcessingJob,
        clean_template_bytes: bytes,
    ) -> None:
        try:
            with self._database.session() as session:
                repository = JobRepository(session)
                current = repository.get(job.id)
                if current is None:
                    raise RuntimeError("OCR job disappeared before snapshot persistence")
                config_snapshot = dict(current.config_snapshot_json)
                raw_snapshot = config_snapshot.get("templateSnapshot")
                if not isinstance(raw_snapshot, dict):
                    raise RuntimeError("OCR template snapshot metadata is missing")
                snapshot = dict(raw_snapshot)
                expected_sha256 = snapshot.get("cleanTemplateSha256")
                actual_sha256 = hashlib.sha256(clean_template_bytes).hexdigest()
                if expected_sha256 != actual_sha256:
                    raise RuntimeError("OCR template snapshot changed before persistence")
                stored_path = self._storage.write_bytes(
                    f"uploads/{job.id}/template_snapshot/clean_template.png",
                    clean_template_bytes,
                )
                snapshot["cleanTemplatePath"] = stored_path
                config_snapshot["templateSnapshot"] = snapshot
                repository.update(
                    job.id,
                    config_snapshot_json=config_snapshot,
                )
        except Exception:
            with self._database.session() as session:
                JobRepository(session).delete(job.id)
            upload_dir = f"uploads/{job.id}"
            if self._storage.exists(upload_dir):
                self._storage.delete_tree(upload_dir)
            raise

    def _ocr_template_snapshot_is_complete(self, job: ProcessingJob) -> bool:
        raw_snapshot = job.config_snapshot_json.get("templateSnapshot")
        if not isinstance(raw_snapshot, dict):
            return False
        relative_path = raw_snapshot.get("cleanTemplatePath")
        expected_sha256 = raw_snapshot.get("cleanTemplateSha256")
        source_sha256 = raw_snapshot.get("sourceArtifactSha256")
        raw_config = raw_snapshot.get("config")
        expected_config_sha256 = raw_snapshot.get("configSha256")
        if (
            not isinstance(relative_path, str)
            or not isinstance(expected_sha256, str)
            or source_sha256 != expected_sha256
            or not isinstance(raw_config, dict)
            or not isinstance(expected_config_sha256, str)
        ):
            return False
        try:
            snapshot_root = self._storage.resolve(
                f"uploads/{job.id}/template_snapshot"
            )
            path = self._storage.resolve(relative_path)
            path.relative_to(snapshot_root)
            if not path.is_file():
                return False
            with path.open("rb") as stream:
                actual_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual_sha256 != expected_sha256:
                return False
            config = TemplateConfig.model_validate(raw_config)
            canonical = json.dumps(
                config.model_dump(mode="json", by_alias=True),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (OSError, StoragePathError, ValueError):
            return False
        return hashlib.sha256(canonical).hexdigest() == expected_config_sha256

    def _mark_queued(self, job_id: str) -> None:
        with self._database.session() as session:
            repository = JobRepository(session)
            repository.update(job_id, status=JobStatus.QUEUED)
            repository.add_event(
                job_id=job_id,
                level="INFO",
                event_code="JOB_QUEUED",
                metadata={"attemptNumber": 1},
            )

    def _ensure_template_name_available(
        self, name: str, idempotency_key: str | None
    ) -> None:
        normalized = TemplateRepository.normalize_name(name)
        orphan_template_ids: list[str] = []
        with self._database.session() as session:
            active_statuses = {
                JobStatus.CREATED.value,
                JobStatus.QUEUED.value,
                JobStatus.PROCESSING.value,
                JobStatus.REVIEW_REQUIRED.value,
                JobStatus.CONFIRMED.value,
            }
            jobs = session.scalars(
                select(ProcessingJob).where(
                    ProcessingJob.job_type == JobType.PREPARE_TEMPLATE.value,
                    ProcessingJob.status.in_(active_statuses),
                )
            )
            for job in jobs:
                if idempotency_key and job.idempotency_key == idempotency_key:
                    return
                reserved_name = job.config_snapshot_json.get("name")
                if (
                    isinstance(reserved_name, str)
                    and TemplateRepository.normalize_name(reserved_name) == normalized
                ):
                    raise self._template_name_conflict()
            matching_templates = [
                template
                for template in session.scalars(select(Template))
                if TemplateRepository.normalize_name(template.name) == normalized
            ]
            for template in matching_templates:
                version_id = session.scalar(
                    select(TemplateVersion.id)
                    .where(TemplateVersion.template_id == template.id)
                    .limit(1)
                )
                if version_id is not None:
                    raise self._template_name_conflict()
                orphan_template_ids.append(template.id)
                session.delete(template)
            if orphan_template_ids:
                session.commit()
        for template_id in orphan_template_ids:
            relative = f"templates/{template_id}"
            if self._storage.exists(relative):
                self._storage.delete_tree(relative)

    def _field_mapping_snapshot(
        self,
        version: TemplateVersion,
        *,
        required: bool,
        config: TemplateConfig | None = None,
    ) -> dict[str, object]:
        if config is None:
            config = parse_template_config(
                self._storage.read_bytes(version.config_relative_path).decode("utf-8")
            )
        mappings = [
            mapping.model_copy(update={"padding": 0}, deep=True)
            for mapping in config.field_mappings
            if mapping.enabled
        ]
        unsupported = [
            mapping for mapping in mappings
            if not is_supported_field_path(mapping.target_path)
        ]
        if unsupported:
            invalid = unsupported[0]
            raise AppError(
                status=409,
                code="TEMPLATE_FIELD_MAPPING_OUT_OF_RANGE",
                title="Template field mapping is out of range",
                detail=(
                    f"{invalid.label} の保存先 {invalid.target_path} は現在の帳票行数を"
                    "超えています。テンプレート画面で意味づけを修正し、再検出してください。"
                ),
                metadata={
                    "mappingId": invalid.mapping_id,
                    "targetPath": invalid.target_path,
                },
            )
        if required and not mappings:
            raise AppError(
                status=409,
                code="TEMPLATE_FIELD_MAPPING_REQUIRED",
                title="Template field mappings are required",
                detail=(
                    "OpenAI読み取りには意味づけ済みの手書きセルが必要です。"
                    "テンプレート画面で記入欄とJSON項目を設定してください。"
                ),
            )
        serialized = [
            mapping.model_dump(mode="json", by_alias=True) for mapping in mappings
        ]
        record_structure: dict[str, object] = {
            "layoutSchemaVersion": 3,
            "verified": False,
            "canonicalWidth": config.canonical_width,
            "canonicalHeight": config.canonical_height,
            "sections": [],
            "groups": [],
            "fieldBindings": [],
        }
        if version.layout_json:
            layout = LayoutDefinition.model_validate(version.layout_json)
            if (
                any(
                    mapping.target_path.startswith(("saleCalves.", "calvings."))
                    for mapping in mappings
                )
                and not layout.record_structure_verified
            ):
                raise AppError(
                    status=409,
                    code="TEMPLATE_RECORD_STRUCTURE_REQUIRED",
                    title="Template record structure is required",
                    detail=(
                        "売却子牛・繁殖履歴の各行をレコードとしてまとめ、"
                        "親セクションと産次キーを確認してください。"
                    ),
                )
            group_by_input = {
                input_id: group
                for group in layout.record_groups
                for input_id in group.input_cell_ids
            }
            target_by_input = {
                relation.input_cell_id: relation.target_path
                for relation in layout.relations
            }
            record_structure = {
                "layoutSchemaVersion": layout.layout_schema_version,
                "verified": layout.record_structure_verified,
                "canonicalWidth": layout.canonical_width,
                "canonicalHeight": layout.canonical_height,
                "sections": [
                    section.model_dump(mode="json", by_alias=True)
                    for section in layout.record_sections
                ],
                "groups": [
                    group.model_dump(mode="json", by_alias=True)
                    for group in layout.record_groups
                ],
                "fieldBindings": [
                    {
                        "mappingId": mapping.mapping_id,
                        "recordGroupId": group.id,
                        "fieldKey": target_by_input.get(input_id, "").split(".")[-1],
                        "targetPath": target_by_input.get(input_id),
                    }
                    for input_id, group in group_by_input.items()
                    if (
                        mapping := next(
                            (
                                item
                                for item in mappings
                                if item.mapping_id == f"layout_{input_id}"
                            ),
                            None,
                        )
                    )
                    is not None
                    and target_by_input.get(input_id)
                ],
            }
        canonical = json.dumps(
            {
                "fieldMappings": serialized,
                "recordStructure": record_structure,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "fieldMappings": serialized,
            "fieldMappingCount": len(serialized),
            "fieldMappingHash": hashlib.sha256(canonical).hexdigest(),
            "fieldMappingSource": "TEMPLATE_VERSION_SNAPSHOT",
            "mappingCanonicalWidth": config.canonical_width,
            "mappingCanonicalHeight": config.canonical_height,
            "recordStructure": record_structure,
        }

    @staticmethod
    def _template_snapshot(
        *,
        template_version: TemplateVersion,
        clean_artifact: JobArtifact,
        clean_template_sha256: str,
        config: TemplateConfig,
    ) -> dict[str, object]:
        serialized_config = config.model_dump(mode="json", by_alias=True)
        canonical_config = json.dumps(
            serialized_config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "schemaVersion": 1,
            "templateId": template_version.template_id,
            "templateVersionId": template_version.id,
            "templateVersionNumber": template_version.version_number,
            "sourceArtifactId": clean_artifact.id,
            "sourceArtifactSha256": clean_artifact.sha256,
            "cleanTemplateSha256": clean_template_sha256,
            "cleanTemplateMediaType": clean_artifact.media_type,
            "cleanTemplateWidth": clean_artifact.width,
            "cleanTemplateHeight": clean_artifact.height,
            "cleanTemplatePath": None,
            "config": serialized_config,
            "configSha256": hashlib.sha256(canonical_config).hexdigest(),
        }

    @staticmethod
    def _sire_name_snapshot(session: Session) -> dict[str, object]:
        names = SireNameService.snapshot_names(session)
        canonical = json.dumps(
            {
                "names": names,
                "allowedSuffixes": list(SIRE_NAME_ALLOWED_SUFFIXES),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "sireNamePool": names,
            "sireNameAllowedSuffixes": list(SIRE_NAME_ALLOWED_SUFFIXES),
            "sireNamePoolHash": hashlib.sha256(canonical).hexdigest(),
        }

    def validate_template_input_count(self, mode: TemplateMode, count: int) -> None:
        minimum, maximum = job_input_bounds(
            JobType.PREPARE_TEMPLATE,
            mode,
            min_consensus_images=self._settings.registration.min_consensus_images,
            max_consensus_images=self._settings.registration.max_consensus_images,
        )
        if minimum == maximum == 1:
            if count == minimum:
                return
            raise AppError(
                status=422,
                code="INVALID_TEMPLATE_INPUT_COUNT",
                title="Invalid template input count",
                detail=f"{mode.value}には画像1枚が必要です。",
            )

        if count < minimum:
            raise AppError(
                status=422,
                code="INSUFFICIENT_CONSENSUS_IMAGES",
                title="Not enough consensus images",
                detail=f"MULTI_SCAN_CONSENSUSには{minimum}枚以上の画像が必要です。",
                metadata={"minimum": minimum, "received": count},
            )
        if count > maximum:
            raise AppError(
                status=422,
                code="TOO_MANY_CONSENSUS_IMAGES",
                title="Too many consensus images",
                detail=(
                    f"一度に処理できる画像は{maximum}枚までです。"
                    "画像を減らして、もう一度お試しください。"
                ),
                metadata={"maximum": maximum, "received": count},
            )

    def validate_template_batch_bytes(self, total_bytes: int) -> None:
        maximum = self._settings.storage.max_batch_upload_bytes
        if total_bytes <= maximum:
            return
        maximum_mib = maximum / (1024 * 1024)
        raise AppError(
            status=422,
            code="TEMPLATE_BATCH_TOO_LARGE",
            title="Template image batch is too large",
            detail=(
                f"画像の合計ファイルサイズは{maximum_mib:g} MiBまでです。"
                "画像を減らすか、ファイルサイズを小さくしてください。"
            ),
            metadata={"maximumBytes": maximum, "receivedBytes": total_bytes},
        )

    def _validate_template_batch_limits(self, uploads: list[UploadPayload]) -> None:
        self.validate_template_batch_bytes(sum(len(item.content) for item in uploads))
        maximum_pixels = self._settings.image_processing.max_batch_image_pixels
        total_pixels = 0
        for upload in uploads:
            width, height = self._loader.inspect_dimensions(
                upload.content,
                upload.declared_media_type,
            )
            total_pixels += width * height
            if total_pixels > maximum_pixels:
                raise AppError(
                    status=422,
                    code="TEMPLATE_BATCH_PIXEL_LIMIT_EXCEEDED",
                    title="Template image batch has too many pixels",
                    detail=(
                        "画像の合計画素数が上限を超えています。"
                        "画像を減らすか、解像度を下げてください。"
                    ),
                    metadata={
                        "maximumPixels": maximum_pixels,
                        "receivedPixels": total_pixels,
                    },
                )

    @staticmethod
    def _request_hash(
        job_type: JobType,
        loaded: list[LoadedImage],
        config_snapshot: Mapping[str, object],
    ) -> str:
        canonical = json.dumps(
            {
                "jobType": job_type.value,
                "images": [item.metadata.sha256 for item in loaded],
                "config": config_snapshot,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            status=404,
            code="JOB_NOT_FOUND",
            title="Job not found",
            detail="指定されたジョブが見つかりません。",
        )

    @staticmethod
    def _template_name_conflict() -> AppError:
        return AppError(
            status=409,
            code="TEMPLATE_NAME_CONFLICT",
            title="Template name already exists",
            detail="同じ名前のテンプレートが既にあります。区別できる名前を指定してください。",
        )
