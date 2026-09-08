"""Atomic acceptance and immutable page snapshots; no live template reads on execution."""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Never
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import JobArtifact, JobInput, ProcessingJob, TemplateVersion
from app.db.session import Database
from app.domain.job_pages import PagedSubmission, template_page_id
from app.domain.print_layout import print_error
from app.domain.template_config import TemplateConfig, parse_template_config
from app.image_processing.image_loader import ImageLoader
from app.repositories.job_repository import JobRepository
from app.services.print_layout_service import PrintLayoutService, digest
from app.services.reference_qualification_service import ReferenceQualificationService
from app.services.template_mutation import begin_immediate, template_mutation_lock
from app.storage.local_storage import LocalStorageService


def execution_config(settings: Settings) -> dict[str, Any]:
    return {
        "ocr": settings.ocr.model_dump(mode="json", exclude={"openai_api_key"}),
        "registration": settings.registration.model_dump(mode="json"),
        "imageProcessing": settings.image_processing.model_dump(mode="json"),
        "segmentation": settings.segmentation.model_dump(mode="json"),
    }


def fail(detail: str, code: str = "JOB_PAGE_SNAPSHOT_INVALID") -> Never:
    raise print_error(detail, code, 409)


def page_config(config: TemplateConfig, page: dict[str, Any]) -> TemplateConfig:
    mappings = {m.mapping_id: m for m in config.field_mappings if m.enabled}
    fields = []
    for field in page["fields"]:
        for binding in field.get("bindings", []):
            mapping = mappings.get(binding["mappingId"])
            if (
                mapping is None
                or mapping.target_path != binding["targetPath"]
                or mapping.mapping_type != "text"
                or mapping.composite_subfields
            ):
                fail(
                    "ページ別の複合項目・マーク設定は未対応です。", "PRINT_PAGE_CONFIG_UNSUPPORTED"
                )
            fields.append(mapping.model_copy(update=field["rectangle"], deep=True))
    if not fields:
        fail("各ページに読み取り対象の設定が必要です。", "PRINT_PAGE_FIELDS_REQUIRED")
    raw = config.model_dump(mode="json", by_alias=True)
    raw.update(
        canonicalWidth=page["width"],
        canonicalHeight=page["height"],
        regions=[],
        fieldMappings=[m.model_dump(mode="json", by_alias=True) for m in fields],
        marks=[],
        documentIdentity=None,
        documentIdentityFields=[],
        documentName=None,
    )
    return TemplateConfig.model_validate(raw)


class JobPageService:
    def __init__(self, database: Database, storage: LocalStorageService, settings: Settings):
        self.database, self.storage, self.settings = database, storage, settings

    def revision(self, version: TemplateVersion) -> str:
        return digest(
            {
                "printRevision": PrintLayoutService.revision(version),
                "configSha256": hashlib.sha256(
                    self.storage.read_bytes(version.config_relative_path)
                ).hexdigest(),
            }
        )

    def catalog(self, version_id: str) -> dict[str, Any]:
        state = PrintLayoutService(self.database, self.storage).get(version_id)
        plan = state["saved"]
        with self.database.session() as session:
            version = session.get(TemplateVersion, version_id)
            if version is None:
                fail("テンプレート版がありません。", "TEMPLATE_NOT_FOUND")
            revision = self.revision(version)
        return {
            "templateVersionId": version_id,
            "templateRevision": revision,
            "available": False,
            "reason": "PRINT_REFERENCE_NOT_VALIDATED",
            "pages": [
                dict(
                    pageId=template_page_id(version_id, p),
                    pageNumber=p["page"],
                    range=p["range"],
                    referenceImageSha256=p["referenceImage"]["sha256"],
                    referenceUrl=f"/api/v1/template-versions/{version_id}/print-layout/pages/{p['page']}/image",
                )
                for p in (plan or {}).get("pages", [])
            ],
        }

    def accept(
        self, request: PagedSubmission, uploads: list[Any], idempotency_key: str | None = None
    ) -> ProcessingJob:
        """Persist one job and all inputs/snapshots in one transaction before enqueue."""
        if len(uploads) != len(request.upload_ids) or len(set(request.upload_ids)) != len(uploads):
            fail("入力IDの件数・重複を確認してください。", "PAGE_BINDING_INVALID")
        input_by_id = dict(zip(request.upload_ids, uploads, strict=True))
        bindings = request.bindings
        if (
            len({b.template_page_id for b in bindings}) != len(bindings)
            or len({b.input_id for b in bindings}) != len(bindings)
            or len({b.input_sha256 for b in bindings}) != len(bindings)
            or {b.input_id for b in bindings} != set(input_by_id)
        ):
            fail("入力とページは重複なく一対一で指定してください。", "PAGE_BINDING_INVALID")
        for binding in bindings:
            if (
                hashlib.sha256(input_by_id[binding.input_id].content).hexdigest()
                != binding.input_sha256
            ):
                fail("選択後に入力画像が変更されています。", "INPUT_REVISION_STALE")
        request_hash = digest(request.model_dump(mode="json", by_alias=True))
        with template_mutation_lock(), self.database.session() as session:
            begin_immediate(session)
            repository = JobRepository(session)
            if idempotency_key:
                existing = repository.find_by_idempotency_key(idempotency_key)
                if existing:
                    if existing.request_hash != request_hash:
                        fail("受付キーの内容が異なります。", "IDEMPOTENCY_KEY_CONFLICT")
                    return existing
            version = session.get(TemplateVersion, request.template_version_id)
            if version is None:
                fail("テンプレート版がありません。", "TEMPLATE_NOT_FOUND")
            if self.revision(version) != request.template_revision:
                fail(
                    "受付前にテンプレート版が変更されています。",
                    "TEMPLATE_CHANGED_DURING_ACCEPTANCE",
                )
            plan = copy.deepcopy(version.quality_json.get("printLayout"))
            if not plan or plan.get("layoutSha256") != digest(version.layout_json):
                fail(
                    "印刷ページ設定がありません、または矩形が変更されています。",
                    "PRINT_LAYOUT_STALE",
                )
            pages = plan["pages"]
            expected = {template_page_id(version.id, p) for p in pages}
            if expected != {b.template_page_id for b in bindings} or len(expected) != len(pages):
                fail("全ページを過不足なく指定してください。", "PAGE_BINDING_INVALID")
            if [p["page"] for p in pages] != list(range(1, len(pages) + 1)):
                fail("保存ページ順序が不正です。")
            # No production renderer has yet passed physical reference qualification.
            # Do not accept a client/DB ocrReady flag as evidence of pixel accuracy.
            self._require_qualified_reference({**plan, "templateVersionId": version.id})
            if version.status != "ACTIVE" or version.quality_json.get("layoutVerified") is not True:
                fail("検証済みACTIVE版が必要です。", "TEMPLATE_LAYOUT_NOT_VERIFIED")
            config = parse_template_config(
                self.storage.read_bytes(version.config_relative_path).decode()
            )
            if config.document_identity or config.document_identity_fields or config.document_name:
                fail("ページをまたぐ文書識別設定は未対応です。", "PRINT_PAGE_CONFIG_UNSUPPORTED")
            job_id = str(uuid4())
            accepted = []
            seen_fields: set[str] = set()
            loader = ImageLoader(self.settings.storage, self.settings.image_processing)
            for index, page in enumerate(pages):
                page_id = template_page_id(version.id, page)
                binding = next(b for b in bindings if b.template_page_id == page_id)
                upload = input_by_id[binding.input_id]
                image = loader.load(upload.content, upload.declared_media_type)
                reference = page["referenceImage"]
                artifact = session.get(JobArtifact, reference["artifactId"])
                if artifact is None or artifact.sha256 != reference["sha256"]:
                    fail("基準ページ画像の登録情報が一致しません。", "PRINT_REFERENCE_CHANGED")
                content = self.storage.read_bytes(artifact.relative_path)
                if hashlib.sha256(content).hexdigest() != reference["sha256"]:
                    fail("基準ページ画像のSHAが一致しません。", "PRINT_REFERENCE_CHANGED")
                per_config = page_config(config, page)
                mapping_ids = {m.mapping_id for m in per_config.field_mappings}
                if seen_fields & mapping_ids:
                    fail("複数ページで項目が重複しています。")
                seen_fields |= mapping_ids
                input_id = str(uuid4())
                input_path = self.storage.write_bytes(
                    f"uploads/{job_id}/pages/{input_id}.{image.metadata.extension}", upload.content
                )
                reference_path = self.storage.write_bytes(
                    f"uploads/{job_id}/template_snapshot/{page_id}.png", content
                )
                snapshot = {
                    "schemaVersion": 1,
                    "templateId": version.template_id,
                    "templateVersionId": version.id,
                    "templateVersionNumber": version.version_number,
                    "sourceArtifactId": reference["artifactId"],
                    "sourceArtifactSha256": reference["sha256"],
                    "cleanTemplateSha256": reference["sha256"],
                    "cleanTemplatePath": reference_path,
                    "cleanTemplateMediaType": "image/png",
                    "config": per_config.model_dump(mode="json", by_alias=True),
                }
                snapshot["configSha256"] = digest(snapshot["config"])
                accepted.append(
                    {
                        "pageId": page_id,
                        "pageNumber": page["page"],
                        "pageRange": page["range"],
                        "inputId": input_id,
                        "clientInputId": binding.input_id,
                        "inputRevision": binding.input_revision,
                        "inputSha256": binding.input_sha256,
                        "templateVersionId": version.id,
                        "templateSnapshot": snapshot,
                        "transform": page["transform"],
                        "fields": page["fields"],
                        "fieldMappings": snapshot["config"]["fieldMappings"],
                        "ocrProvider": self.settings.ocr.provider,
                        "ocrEnabled": True,
                        "sendFullDocumentToOpenAi": False,
                        "schemaVersion": self.settings.ocr.schema_version,
                        "promptVersion": self.settings.ocr.prompt_version,
                    }
                )
                session.add(
                    JobInput(
                        id=input_id,
                        job_id=job_id,
                        input_index=index,
                        media_type=image.metadata.media_type,
                        format=image.metadata.format,
                        width=image.metadata.width,
                        height=image.metadata.height,
                        file_bytes=len(upload.content),
                        sha256=binding.input_sha256,
                        relative_path=input_path,
                        content_bytes=upload.content,
                    )
                )
            if seen_fields != {m.mapping_id for m in config.field_mappings if m.enabled}:
                fail("全読み取り項目のページ対応を確認できません。")
            session.refresh(version)
            if self.revision(version) != request.template_revision or version.status != "ACTIVE":
                fail(
                    "受付中にテンプレートが変更されています。", "TEMPLATE_CHANGED_DURING_ACCEPTANCE"
                )
            bundle = {
                "schemaVersion": 1,
                "templateRevision": request.template_revision,
                "printLayout": plan,
                "pages": accepted,
                "executionConfig": execution_config(self.settings),
            }
            job = ProcessingJob(
                id=job_id,
                job_type="EXTRACT_DOCUMENT",
                status="QUEUED",
                template_version_id=version.id,
                request_hash=request_hash,
                idempotency_key=idempotency_key,
                attempt_number=1,
                progress_percent=0,
                cancel_requested=False,
                config_snapshot_json={"jobPages": bundle, "jobPagesSha256": digest(bundle)},
            )
            session.add(job)
            session.commit()
            return job

    def _require_qualified_reference(self, plan: dict[str, Any]) -> None:
        version_id = plan.get("templateVersionId")
        evidence = ReferenceQualificationService(self.database, self.storage).status(
            version_id) if version_id else {"status": "UNVERIFIED"}
        # Evidence validation is connected, but no production renderer is admitted
        # by the current policy. Neither qualified nor ocrReady JSON flags override it.
        fail(
            f"基準画像検証: {evidence['status']}。本番の位置精度・実行隔離条件は未認定です。",
            "PRINT_REFERENCE_NOT_VALIDATED",
        )

    def validate(self, session: Session, job: ProcessingJob) -> list[dict[str, Any]]:
        bundle = job.config_snapshot_json.get("jobPages")
        if not isinstance(bundle, dict) or digest(bundle) != job.config_snapshot_json.get(
            "jobPagesSha256"
        ):
            fail("受付ページsnapshotの整合性を確認できません。")
        if bundle["executionConfig"] != execution_config(self.settings):
            fail("受付時の実行設定と現在の設定が異なります。", "PAGE_EXECUTION_CONFIG_CHANGED")
        inputs = {i.id: i for i in JobRepository(session).list_inputs(job.id)}
        pages = bundle["pages"]
        if (
            not pages
            or len({p["pageId"] for p in pages}) != len(pages)
            or len({p["inputId"] for p in pages}) != len(pages)
            or {p["inputId"] for p in pages} != set(inputs)
            or [p["pageNumber"] for p in pages] != list(range(1, len(pages) + 1))
        ):
            fail("受付ページの入力対応が不正です。")
        for page in pages:
            item = inputs[page["inputId"]]
            snapshot = page["templateSnapshot"]
            if page["templateVersionId"] != job.template_version_id:
                fail("受付ページの版が一致しません。")
            for path, sha in (
                (item.relative_path, page["inputSha256"]),
                (snapshot["cleanTemplatePath"], snapshot["cleanTemplateSha256"]),
            ):
                try:
                    resolved = self.storage.resolve(path)
                    resolved.relative_to(self.storage.resolve(f"uploads/{job.id}"))
                    valid = hashlib.sha256(resolved.read_bytes()).hexdigest() == sha
                except (OSError, ValueError):
                    valid = False
                if not valid:
                    fail("受付画像のSHAが一致しません。", "JOB_PAGE_IMAGE_CHANGED")
            if digest(snapshot["config"]) != snapshot["configSha256"]:
                fail("受付設定のSHAが一致しません。")
        return copy.deepcopy(pages)
