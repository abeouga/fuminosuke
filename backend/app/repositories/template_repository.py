from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import ProcessingJob, Template, TemplateVersion
from app.domain.enums import JobStatus, TemplateMode, TemplateVersionStatus
from app.domain.template_quality import (
    has_approved_generic_definition,
    has_full_image_boundary_fallback,
)


class TemplateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_template(self, *, name: str, document_type: str) -> Template:
        if self.name_exists(name):
            raise self._name_conflict()
        template = Template(name=name, document_type=document_type)
        self._session.add(template)
        self._session.commit()
        self._session.refresh(template)
        return template

    def create_version(
        self,
        *,
        template_id: str,
        mode: TemplateMode,
        canonical_width: int,
        canonical_height: int,
        source_job_id: str,
        clean_template_artifact_id: str,
        mask_artifact_id: str | None,
        config_relative_path: str,
        quality: dict[str, Any],
    ) -> TemplateVersion:
        maximum = self._session.scalar(
            select(func.max(TemplateVersion.version_number)).where(
                TemplateVersion.template_id == template_id
            )
        )
        version = TemplateVersion(
            template_id=template_id,
            version_number=(maximum or 0) + 1,
            mode=mode.value,
            status=TemplateVersionStatus.DRAFT.value,
            canonical_width=canonical_width,
            canonical_height=canonical_height,
            source_job_id=source_job_id,
            clean_template_artifact_id=clean_template_artifact_id,
            mask_artifact_id=mask_artifact_id,
            config_relative_path=config_relative_path,
            quality_json=quality,
        )
        self._session.add(version)
        # The caller adds the detected layout before committing so a template
        # version can never become visible in a half-initialized state.
        self._session.flush()
        self._session.refresh(version)
        return version

    def list_templates(self) -> list[Template]:
        return list(self._session.scalars(select(Template).order_by(Template.created_at.desc())))

    def name_exists(self, name: str) -> bool:
        normalized = self.normalize_name(name)
        return any(
            self.normalize_name(template.name) == normalized
            for template in self._session.scalars(select(Template))
        )

    @staticmethod
    def normalize_name(name: str) -> str:
        return unicodedata.normalize("NFKC", name).strip().casefold()

    def get_template(self, template_id: str) -> Template | None:
        return self._session.get(Template, template_id)

    def list_versions(self, template_id: str) -> list[TemplateVersion]:
        statement = (
            select(TemplateVersion)
            .where(TemplateVersion.template_id == template_id)
            .order_by(TemplateVersion.version_number.desc())
        )
        return list(self._session.scalars(statement))

    def get_version(self, version_id: str) -> TemplateVersion | None:
        return self._session.get(TemplateVersion, version_id)

    def approve(self, version_id: str) -> TemplateVersion:
        version = self.get_version(version_id)
        if version is None:
            raise self._not_found()
        if version.status != TemplateVersionStatus.DRAFT.value:
            raise self._invalid_state("DRAFTテンプレート版だけを承認できます。")
        source_job = self._session.get(ProcessingJob, version.source_job_id)
        if source_job is None or source_job.status != JobStatus.REVIEW_REQUIRED.value:
            raise self._invalid_state("元ジョブがREVIEW_REQUIREDではありません。")
        if not version.clean_template_artifact_id or not version.quality_json:
            raise self._invalid_state("必須生成物または品質情報が不足しています。")
        if has_full_image_boundary_fallback(version.quality_json):
            raise AppError(
                status=409,
                code="TEMPLATE_DOCUMENT_BOUNDARY_NOT_VERIFIED",
                title="Template document boundary must be detected",
                detail=(
                    "帳票外形の検出に失敗して画像全体を使用したテンプレートは有効化できません。"
                    "帳票全体が写る画像でテンプレートを作り直してください。"
                ),
            )
        relations = version.layout_json.get("relations")
        generic_approved = has_approved_generic_definition(version.quality_json)
        if not generic_approved and (
            not isinstance(relations, list)
            or not relations
            or version.quality_json.get("layoutFinalized") is not True
            or version.quality_json.get("layoutVerified") is not True
        ):
            raise self._invalid_state(
                "意味づけを保存し、OpenCVによる記入欄の再検出を完了してから承認してください。"
            )
        template = self.get_template(version.template_id)
        if template is None:
            raise self._not_found()
        if template.active_version_id:
            previous = self.get_version(template.active_version_id)
            if previous is not None:
                previous.status = TemplateVersionStatus.ARCHIVED.value
        now = datetime.now(UTC)
        version.status = TemplateVersionStatus.ACTIVE.value
        version.approved_at = now
        template.active_version_id = version.id
        template.updated_at = now
        source_job.status = JobStatus.CONFIRMED.value
        source_job.confirmed_at = now
        self._session.commit()
        self._session.refresh(version)
        return version

    def reject(self, version_id: str) -> TemplateVersion:
        version = self.get_version(version_id)
        if version is None:
            raise self._not_found()
        if version.status != TemplateVersionStatus.DRAFT.value:
            raise self._invalid_state("DRAFTテンプレート版だけを却下できます。")
        version.status = TemplateVersionStatus.REJECTED.value
        version.rejected_at = datetime.now(UTC)
        source_job = self._session.get(ProcessingJob, version.source_job_id)
        if source_job and source_job.status == JobStatus.REVIEW_REQUIRED.value:
            source_job.status = JobStatus.CANCELLED.value
        self._session.commit()
        self._session.refresh(version)
        return version

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            status=404,
            code="TEMPLATE_NOT_FOUND",
            title="Template not found",
            detail="指定されたテンプレートが見つかりません。",
        )

    @staticmethod
    def _invalid_state(detail: str) -> AppError:
        return AppError(
            status=409,
            code="INVALID_STATE_TRANSITION",
            title="Invalid template state",
            detail=detail,
        )

    @staticmethod
    def _name_conflict() -> AppError:
        return AppError(
            status=409,
            code="TEMPLATE_NAME_CONFLICT",
            title="Template name already exists",
            detail="同じ名前のテンプレートが既にあります。区別できる名前を指定してください。",
        )
