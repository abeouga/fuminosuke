from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import (
    OutputLayoutTemplateRevision,
    ProcessingJob,
    Template,
    TemplateCandidateBinding,
    TemplateCandidateSet,
    TemplatePackageInstallation,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus, TemplateVersionStatus
from app.domain.template_candidates import normalize_candidate_target, normalize_candidate_text
from app.services.template_mutation import begin_immediate, template_mutation_lock
from app.template_packages.canonical_models import CanonicalOutputLayout, CanonicalTemplate


@dataclass(frozen=True, slots=True)
class TemplatePackageActivationResult:
    installation_id: str
    local_template_id: str
    local_template_version_id: str
    previous_active_version_id: str | None
    active_version_id: str
    candidate_set_count: int
    output_layout_materialized: bool
    already_active: bool


class TemplatePackageActivationService:
    """Atomically materializes version-dependent runtime state and activates an import."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def activate(
        self,
        installation_id: str,
        *,
        expected_active_version_id: str | None,
        owner_key: str,
        failure_injector: Callable[[str], None] | None = None,
    ) -> TemplatePackageActivationResult:
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            try:
                installation = session.get(TemplatePackageInstallation, installation_id)
                if installation is None:
                    raise self._not_found()
                template = session.get(Template, installation.local_template_id)
                version = session.get(TemplateVersion, installation.local_template_version_id)
                job = session.get(ProcessingJob, installation.local_job_id)
                if template is None or version is None or job is None:
                    raise AppError(
                        status=409,
                        code="TEMPLATE_PACKAGE_INSTALLATION_DAMAGED",
                        title="Template package installation is damaged",
                        detail="取り込み済みtemplateのlocal mappingが壊れています。",
                    )
                if version.template_id != template.id:
                    raise AppError(
                        status=409,
                        code="TEMPLATE_PACKAGE_INSTALLATION_DAMAGED",
                        title="Template package installation is damaged",
                        detail="取り込み済みversionの所属templateが一致しません。",
                    )
                if version.status == TemplateVersionStatus.REJECTED.value:
                    raise AppError(
                        status=409,
                        code="TEMPLATE_PACKAGE_VERSION_REJECTED",
                        title="Imported template version is rejected",
                        detail="却下済みの取り込み版は有効化できません。",
                    )

                from app.services.layout_service import LayoutService

                LayoutService.require_valid_saved_mapping_layout(version)
                canonical = CanonicalTemplate.model_validate(
                    installation.canonical_template_json
                )
                output_layout = (
                    CanonicalOutputLayout.model_validate(installation.output_layout_json)
                    if installation.output_layout_json is not None
                    else None
                )
                candidate_plan = self._candidate_plan(installation, canonical)
                runtime_layout = (
                    self._runtime_output_layout(output_layout, version.id)
                    if output_layout is not None
                    else None
                )
                self._validate_output_layout_conflict(
                    session,
                    owner_key=owner_key,
                    version_id=version.id,
                    runtime_layout=runtime_layout,
                    source_job_id=job.id,
                )

                previous_active_id = template.active_version_id
                already_active = previous_active_id == version.id
                if already_active:
                    if (
                        template.name != canonical.name
                        or template.document_type != canonical.document_type
                    ):
                        raise AppError(
                            status=409,
                            code="TEMPLATE_PACKAGE_METADATA_CONFLICT",
                            title="Imported template metadata changed",
                            detail=(
                                "有効化後にtemplate名または書類種別が変更されています。"
                                "自動上書きせず停止しました。"
                            ),
                        )
                    self._require_candidate_state(session, template.id, candidate_plan)
                elif previous_active_id != expected_active_version_id:
                    raise AppError(
                        status=409,
                        code="TEMPLATE_PACKAGE_ACTIVATION_CONFLICT",
                        title="Active template version changed",
                        detail=(
                            "確認後に有効版が変更されています。現在の有効版を確認してから"
                            "もう一度実行してください。"
                        ),
                    )

                if not already_active:
                    self._replace_candidates(session, template.id, candidate_plan)
                    if failure_injector is not None:
                        failure_injector("after_candidate_materialization")
                self._materialize_output_layout(
                    session,
                    owner_key=owner_key,
                    version_id=version.id,
                    runtime_layout=runtime_layout,
                    source_job_id=job.id,
                )
                if failure_injector is not None:
                    failure_injector("after_output_layout_materialization")

                now = datetime.now(UTC)
                if not already_active and previous_active_id:
                    previous = session.get(TemplateVersion, previous_active_id)
                    if previous is not None:
                        previous.status = TemplateVersionStatus.ARCHIVED.value
                if not already_active:
                    template.name = canonical.name
                    template.document_type = canonical.document_type
                version.status = TemplateVersionStatus.ACTIVE.value
                version.approved_at = version.approved_at or now
                template.active_version_id = version.id
                template.archived_at = None
                template.updated_at = now
                job.status = JobStatus.CONFIRMED.value
                job.progress_stage = "PACKAGE_ACTIVATED"
                job.progress_percent = 100
                job.confirmed_at = job.confirmed_at or now
                mapping = dict(installation.local_mapping_json)
                mapping["candidateMaterialization"] = "MATERIALIZED"
                mapping["outputLayoutMaterialization"] = (
                    "MATERIALIZED" if runtime_layout is not None else "NOT_INCLUDED"
                )
                owners = mapping.get("outputLayoutOwnerKeys")
                owner_keys = (
                    [item for item in owners if isinstance(item, str)]
                    if isinstance(owners, list)
                    else []
                )
                if runtime_layout is not None and owner_key not in owner_keys:
                    owner_keys.append(owner_key)
                mapping["outputLayoutOwnerKeys"] = sorted(owner_keys)
                installation.local_mapping_json = mapping
                if failure_injector is not None:
                    failure_injector("before_activation_commit")
                session.commit()
                return TemplatePackageActivationResult(
                    installation_id=installation.id,
                    local_template_id=template.id,
                    local_template_version_id=version.id,
                    previous_active_version_id=previous_active_id,
                    active_version_id=version.id,
                    candidate_set_count=len(candidate_plan),
                    output_layout_materialized=runtime_layout is not None,
                    already_active=already_active,
                )
            except BaseException:
                session.rollback()
                raise

    @staticmethod
    def imported_installation_for_version(
        session: Session,
        version_id: str,
    ) -> TemplatePackageInstallation | None:
        return session.scalar(
            select(TemplatePackageInstallation).where(
                TemplatePackageInstallation.local_template_version_id == version_id
            )
        )

    @staticmethod
    def _candidate_plan(
        installation: TemplatePackageInstallation,
        canonical: CanonicalTemplate,
    ) -> list[dict[str, Any]]:
        raw_mapping = installation.local_mapping_json.get("candidateSets")
        if not isinstance(raw_mapping, dict):
            raise TemplatePackageActivationService._mapping_damaged()
        labels = {
            normalize_candidate_target(item.target_path): item.label
            for item in canonical.runtime.field_mappings
            if item.enabled and item.mapping_type == "text"
        }
        plan: list[dict[str, Any]] = []
        for item in canonical.candidate_sets:
            mapped = raw_mapping.get(item.id)
            if not isinstance(mapped, dict):
                raise TemplatePackageActivationService._mapping_damaged()
            set_id = mapped.get("localCandidateSetId")
            binding_ids = mapped.get("localBindingIds")
            if not isinstance(set_id, str) or not isinstance(binding_ids, dict):
                raise TemplatePackageActivationService._mapping_damaged()
            bindings: list[dict[str, str]] = []
            for target in item.target_patterns:
                binding_id = binding_ids.get(target)
                label = labels.get(target)
                if not isinstance(binding_id, str) or label is None:
                    raise TemplatePackageActivationService._mapping_damaged()
                bindings.append({"id": binding_id, "target": target, "label": label})
            plan.append(
                {
                    "id": set_id,
                    "name": item.name,
                    "normalizedName": normalize_candidate_text(item.name),
                    "values": list(item.values),
                    "allowedSuffixes": list(item.allowed_suffixes),
                    "bindings": bindings,
                }
            )
        return plan

    @staticmethod
    def _replace_candidates(
        session: Session,
        template_id: str,
        plan: list[dict[str, Any]],
    ) -> None:
        session.execute(
            delete(TemplateCandidateBinding).where(
                TemplateCandidateBinding.template_id == template_id
            )
        )
        session.execute(
            delete(TemplateCandidateSet).where(TemplateCandidateSet.template_id == template_id)
        )
        session.flush()
        for item in plan:
            candidate_set = TemplateCandidateSet(
                id=item["id"],
                template_id=template_id,
                name=item["name"],
                normalized_name=item["normalizedName"],
                values_json=item["values"],
                allowed_suffixes_json=item["allowedSuffixes"],
            )
            session.add(candidate_set)
            session.flush()
            for binding in item["bindings"]:
                session.add(
                    TemplateCandidateBinding(
                        id=binding["id"],
                        template_id=template_id,
                        candidate_set_id=candidate_set.id,
                        target_pattern=binding["target"],
                        field_label=binding["label"],
                    )
                )
        session.flush()

    @staticmethod
    def _require_candidate_state(
        session: Session,
        template_id: str,
        plan: list[dict[str, Any]],
    ) -> None:
        sets = list(
            session.scalars(
                select(TemplateCandidateSet)
                .where(TemplateCandidateSet.template_id == template_id)
                .order_by(TemplateCandidateSet.id)
            )
        )
        bindings = list(
            session.scalars(
                select(TemplateCandidateBinding)
                .where(TemplateCandidateBinding.template_id == template_id)
                .order_by(TemplateCandidateBinding.id)
            )
        )
        actual_sets = sorted(
            (
                row.id,
                row.name,
                tuple(row.values_json),
                tuple(row.allowed_suffixes_json),
            )
            for row in sets
        )
        expected_sets = sorted(
            (
                item["id"],
                item["name"],
                tuple(item["values"]),
                tuple(item["allowedSuffixes"]),
            )
            for item in plan
        )
        actual_bindings = sorted(
            (row.id, row.candidate_set_id, row.target_pattern, row.field_label)
            for row in bindings
        )
        expected_bindings = sorted(
            (
                binding["id"],
                item["id"],
                binding["target"],
                binding["label"],
            )
            for item in plan
            for binding in item["bindings"]
        )
        if actual_sets != expected_sets or actual_bindings != expected_bindings:
            raise AppError(
                status=409,
                code="TEMPLATE_PACKAGE_CANDIDATE_CONFLICT",
                title="Imported candidate settings changed",
                detail=(
                    "有効化後に読み取り候補が変更されています。自動上書きせず停止しました。"
                ),
            )

    @staticmethod
    def _runtime_output_layout(
        canonical: CanonicalOutputLayout,
        version_id: str,
    ) -> dict[str, Any]:
        cells: list[dict[str, Any]] = []
        for cell in canonical.cells:
            note = f"llm-ocr:field:{cell.binding}" if cell.binding else cell.note
            cells.append(
                {
                    "row": cell.row,
                    "column": cell.column,
                    "value": cell.value,
                    "format": cell.format,
                    "note": note,
                    "dataValidation": cell.data_validation,
                    "textFormatRuns": cell.text_format_runs,
                }
            )
        return {
            "schemaVersion": 1,
            "sheetName": canonical.sheet_name,
            "rowCount": canonical.row_count,
            "columnCount": canonical.column_count,
            "frozenRowCount": canonical.frozen_row_count,
            "frozenColumnCount": canonical.frozen_column_count,
            "hideGridlines": canonical.hide_gridlines,
            "cells": cells,
            "merges": [item.model_dump(mode="json", by_alias=True) for item in canonical.merges],
            "rowHeights": canonical.row_heights,
            "columnWidths": canonical.column_widths,
            "bindingCount": sum(cell.binding is not None for cell in canonical.cells),
            "sourceTemplateVersionId": version_id,
        }

    @staticmethod
    def _validate_output_layout_conflict(
        session: Session,
        *,
        owner_key: str,
        version_id: str,
        runtime_layout: dict[str, Any] | None,
        source_job_id: str,
    ) -> None:
        if runtime_layout is None:
            return
        existing = session.scalar(
            select(OutputLayoutTemplateRevision)
            .where(
                OutputLayoutTemplateRevision.owner_key == owner_key,
                OutputLayoutTemplateRevision.template_version_id == version_id,
            )
            .order_by(OutputLayoutTemplateRevision.revision.desc())
            .limit(1)
        )
        if existing is not None and (
            existing.layout_json != runtime_layout or existing.source_job_id != source_job_id
        ):
            raise AppError(
                status=409,
                code="TEMPLATE_PACKAGE_OUTPUT_LAYOUT_CONFLICT",
                title="Imported output layout conflicts with local layout",
                detail="同じ利用者・template版の出力レイアウトが変更済みのため上書きできません。",
            )

    @staticmethod
    def _materialize_output_layout(
        session: Session,
        *,
        owner_key: str,
        version_id: str,
        runtime_layout: dict[str, Any] | None,
        source_job_id: str,
    ) -> bool:
        if runtime_layout is None:
            return False
        existing = session.scalar(
            select(OutputLayoutTemplateRevision)
            .where(
                OutputLayoutTemplateRevision.owner_key == owner_key,
                OutputLayoutTemplateRevision.template_version_id == version_id,
            )
            .order_by(OutputLayoutTemplateRevision.revision.desc())
            .limit(1)
        )
        if existing is not None:
            return False
        session.add(
            OutputLayoutTemplateRevision(
                owner_key=owner_key,
                template_version_id=version_id,
                revision=1,
                layout_json=runtime_layout,
                source_sheet_name=str(runtime_layout["sheetName"]),
                source_job_id=source_job_id,
            )
        )
        session.flush()
        return True

    @staticmethod
    def _mapping_damaged() -> AppError:
        return AppError(
            status=409,
            code="TEMPLATE_PACKAGE_INSTALLATION_DAMAGED",
            title="Template package installation is damaged",
            detail="取り込み済みtemplateのcandidate mappingが不正です。",
        )

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            status=404,
            code="TEMPLATE_PACKAGE_INSTALLATION_NOT_FOUND",
            title="Template package installation not found",
            detail="取り込み済みtemplate packageが見つかりません。",
        )
