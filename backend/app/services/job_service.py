from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import ProcessingJob, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType, TemplateMode, TemplateVersionStatus
from app.domain.job_state import require_transition
from app.image_processing.image_loader import ImageLoader, LoadedImage
from app.jobs.runner import JobRunner
from app.repositories.job_repository import JobRepository
from app.storage.local_storage import LocalStorageService


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
        self._validate_template_input_count(mode, len(uploads))
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
        upload: UploadPayload,
        ocr_enabled: bool,
        idempotency_key: str | None,
    ) -> ProcessingJob:
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
        loaded = [self._loader.load(upload.content, upload.declared_media_type)]
        config_snapshot = {
            "templateVersionId": template_version_id,
            "ocrEnabled": ocr_enabled,
            "ocrProvider": self._settings.ocr.provider if ocr_enabled else "disabled",
            "schemaVersion": self._settings.ocr.schema_version,
            "promptVersion": self._settings.ocr.prompt_version,
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

    async def retry(self, job_id: str) -> ProcessingJob:
        with self._database.session() as session:
            repository = JobRepository(session)
            job = repository.get(job_id)
            if job is None:
                raise self._not_found()
            current = JobStatus(job.status)
            require_transition(current, JobStatus.QUEUED)
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
            job = repository.create(
                job_type=job_type,
                request_hash=request_hash,
                config_snapshot=config_snapshot,
                mode=mode,
                idempotency_key=idempotency_key,
                image_sha256=loaded[0].metadata.sha256 if len(loaded) == 1 else None,
                template_version_id=template_version_id,
            )
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

    @staticmethod
    def _validate_template_input_count(mode: TemplateMode, count: int) -> None:
        valid = count == 1 if mode is not TemplateMode.MULTI_SCAN_CONSENSUS else count >= 3
        if not valid:
            detail = (
                "MULTI_SCAN_CONSENSUSには3枚以上の画像が必要です。"
                if mode is TemplateMode.MULTI_SCAN_CONSENSUS
                else f"{mode.value}には画像1枚が必要です。"
            )
            raise AppError(
                status=422,
                code="INSUFFICIENT_CONSENSUS_IMAGES",
                title="Invalid template input count",
                detail=detail,
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
