from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    OutputLayoutTemplateRevision,
    Template,
    TemplateCandidateBinding,
    TemplateCandidateSet,
    TemplateDefinitionRevision,
    TemplateVersion,
)
from app.domain.enums import TemplateMode, TemplateVersionStatus
from app.domain.template_config import parse_template_config
from app.domain.template_definition import TemplateDefinition, TemplateDefinitionValidation
from app.domain.template_quality import (
    has_approved_generic_definition,
    has_full_image_boundary_fallback,
)
from app.layout_analysis.models import LayoutDefinition
from app.storage.local_storage import LocalStorageService
from app.template_packages.canonical_models import (
    CanonicalApprovedDefinition,
    CanonicalAssetReferences,
    CanonicalCandidateSet,
    CanonicalOutputLayout,
    CanonicalOutputLayoutCell,
    CanonicalOutputLayoutMerge,
    CanonicalRuntimeConfig,
    CanonicalSpreadsheetSource,
    CanonicalTemplate,
    CanonicalTemplateQuality,
)
from app.template_packages.export_models import TemplateExportRequest, TemplatePackageExportError

_SAFE_WARNING_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,99}$")


class CanonicalTemplateMapper:
    def __init__(self, storage: LocalStorageService) -> None:
        self._storage = storage

    @staticmethod
    def select_release(
        session: Session, request: TemplateExportRequest
    ) -> tuple[Template, TemplateVersion]:
        template = session.get(Template, request.template_id)
        if template is None:
            raise TemplatePackageExportError("TEMPLATE_NOT_FOUND", "テンプレートが見つかりません。")
        version_id = request.template_version_id or template.active_version_id
        version = session.get(TemplateVersion, version_id) if version_id else None
        if version is None or version.template_id != template.id:
            raise TemplatePackageExportError(
                "TEMPLATE_VERSION_NOT_FOUND", "対象のテンプレート版が見つかりません。"
            )
        if version.status != TemplateVersionStatus.ACTIVE.value:
            raise TemplatePackageExportError(
                "TEMPLATE_VERSION_NOT_ACTIVE",
                "利用可能状態ではないテンプレート版は書き出せません。",
            )
        if template.active_version_id != version.id:
            raise TemplatePackageExportError(
                "TEMPLATE_VERSION_NOT_ACTIVE", "現在の有効版だけを書き出せます。"
            )
        return template, version

    def map_template(
        self,
        session: Session,
        template: Template,
        version: TemplateVersion,
        *,
        assets: CanonicalAssetReferences,
        source_sheet_name: str | None,
        source_file_name: str | None,
    ) -> tuple[CanonicalTemplate, CanonicalTemplateQuality]:
        if version.quality_json.get("printLayout") is not None:
            raise TemplatePackageExportError(
                "PRINT_LAYOUT_PACKAGE_UNSUPPORTED",
                "印刷ページ設定を含む版のパッケージ書き出しは未対応です。設定を省略して書き出すことはできません。",
            )
        try:
            config = parse_template_config(
                self._storage.read_bytes(version.config_relative_path).decode("utf-8")
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise TemplatePackageExportError(
                "TEMPLATE_CONFIG_INVALID", "テンプレート設定を読み込めません。"
            ) from exc
        if config.template_id != template.id or config.template_version != version.version_number:
            raise TemplatePackageExportError(
                "TEMPLATE_CONFIG_MISMATCH", "テンプレート設定と対象版が一致しません。"
            )
        if (
            config.canonical_width != version.canonical_width
            or config.canonical_height != version.canonical_height
        ):
            raise TemplatePackageExportError(
                "TEMPLATE_CONFIG_DIMENSION_MISMATCH",
                "テンプレート設定の寸法が対象版と一致しません。",
            )
        try:
            layout = LayoutDefinition.model_validate(version.layout_json)
        except ValueError as exc:
            raise TemplatePackageExportError(
                "TEMPLATE_LAYOUT_INVALID", "テンプレートlayoutを検証できません。"
            ) from exc

        approved_definition, definition_valid = self._approved_definition(session, version)
        candidate_sets = self._candidate_sets(session, template.id)
        spreadsheet = None
        if version.mode == TemplateMode.SPREADSHEET_TEMPLATE.value:
            if source_sheet_name is None or source_file_name is None:
                raise TemplatePackageExportError(
                    "SPREADSHEET_SOURCE_METADATA_REQUIRED",
                    "元XLSXのportable metadataが不足しています。",
                )
            spreadsheet = CanonicalSpreadsheetSource(
                sheet_name=source_sheet_name,
                file_name=source_file_name,
            )

        canonical = CanonicalTemplate(
            name=template.name,
            document_type=template.document_type,
            mode=TemplateMode(version.mode),
            canonical_width=version.canonical_width,
            canonical_height=version.canonical_height,
            runtime=CanonicalRuntimeConfig(
                registration=config.registration,
                regions=config.regions,
                field_mappings=config.field_mappings,
                document_identity_fields=config.effective_document_identity_fields(),
                document_name=config.document_name,
                marks=config.marks,
                export=config.export,
            ),
            spreadsheet=spreadsheet,
            layout=layout,
            approved_definition=approved_definition,
            candidate_sets=candidate_sets,
            assets=assets,
        )
        quality = CanonicalTemplateQuality(
            document_boundary_verified=not has_full_image_boundary_fallback(version.quality_json),
            layout_verified=version.quality_json.get("layoutVerified") is True,
            layout_finalized=version.quality_json.get("layoutFinalized") is True,
            record_structure_verified=layout.record_structure_verified,
            definition_approved=approved_definition is not None,
            definition_valid=definition_valid,
            field_mapping_count=sum(1 for item in config.field_mappings if item.enabled),
            warnings=self._safe_warnings(version.quality_json),
        )
        return canonical, quality

    @staticmethod
    def _approved_definition(
        session: Session, version: TemplateVersion
    ) -> tuple[CanonicalApprovedDefinition | None, bool]:
        if not has_approved_generic_definition(version.quality_json):
            return None, False
        revision_number = version.quality_json["templateDefinitionRevision"]
        row = session.scalar(
            select(TemplateDefinitionRevision).where(
                TemplateDefinitionRevision.template_version_id == version.id,
                TemplateDefinitionRevision.revision == revision_number,
                TemplateDefinitionRevision.source == "APPROVED",
            )
        )
        if row is None:
            raise TemplatePackageExportError(
                "TEMPLATE_DEFINITION_REQUIRED", "承認済みdefinitionが見つかりません。"
            )
        try:
            definition = TemplateDefinition.model_validate(row.definition_json)
            validation = TemplateDefinitionValidation.model_validate(row.validation_json)
        except ValueError as exc:
            raise TemplatePackageExportError(
                "TEMPLATE_DEFINITION_INVALID", "承認済みdefinitionを検証できません。"
            ) from exc
        if definition.state != "APPROVED" or not validation.valid:
            raise TemplatePackageExportError(
                "TEMPLATE_DEFINITION_INVALID", "承認済みdefinitionが有効ではありません。"
            )
        return (
            CanonicalApprovedDefinition(
                schema_version=definition.schema_version,
                canonical_width=definition.canonical_width,
                canonical_height=definition.canonical_height,
                cells=definition.cells,
                geometry=definition.geometry,
                standalone_fields=definition.standalone_fields,
                sections=definition.sections,
                unassigned_cell_ids=definition.unassigned_cell_ids,
                confidence=definition.confidence,
                phases=definition.phases,
                warnings=[
                    item for item in definition.warnings if _SAFE_WARNING_CODE.fullmatch(item)
                ],
            ),
            True,
        )

    @staticmethod
    def _candidate_sets(session: Session, template_id: str) -> list[CanonicalCandidateSet]:
        rows = list(
            session.scalars(
                select(TemplateCandidateSet)
                .where(TemplateCandidateSet.template_id == template_id)
                .order_by(TemplateCandidateSet.created_at, TemplateCandidateSet.id)
            )
        )
        bindings = list(
            session.scalars(
                select(TemplateCandidateBinding)
                .where(TemplateCandidateBinding.template_id == template_id)
                .order_by(TemplateCandidateBinding.target_pattern)
            )
        )
        by_set: dict[str, list[str]] = {}
        for binding in bindings:
            by_set.setdefault(binding.candidate_set_id, []).append(binding.target_pattern)
        return [
            CanonicalCandidateSet(
                id=f"candidate-set-{index:03d}",
                name=row.name,
                values=list(row.values_json),
                allowed_suffixes=list(row.allowed_suffixes_json),
                target_patterns=by_set.get(row.id, []),
            )
            for index, row in enumerate(rows, start=1)
        ]

    @staticmethod
    def output_layout(
        session: Session,
        version_id: str,
        owner_key: str | None,
    ) -> CanonicalOutputLayout | None:
        if owner_key is None:
            return None
        row = session.scalar(
            select(OutputLayoutTemplateRevision)
            .where(
                OutputLayoutTemplateRevision.template_version_id == version_id,
                OutputLayoutTemplateRevision.owner_key == owner_key,
            )
            .order_by(OutputLayoutTemplateRevision.revision.desc())
            .limit(1)
        )
        if row is None:
            raise TemplatePackageExportError(
                "OUTPUT_LAYOUT_NOT_FOUND", "指定した既定出力レイアウトが見つかりません。"
            )
        raw = row.layout_json
        cells: list[CanonicalOutputLayoutCell] = []
        for untyped in raw.get("cells", []):
            if not isinstance(untyped, dict):
                raise TemplatePackageExportError(
                    "OUTPUT_LAYOUT_INVALID", "出力レイアウトのcell形式が不正です。"
                )
            item = dict(untyped)
            note = item.get("note")
            binding: str | None = None
            portable_note: str | None = None
            if isinstance(note, str) and note.startswith("llm-ocr:field:"):
                binding = note.removeprefix("llm-ocr:field:").strip()
            elif isinstance(note, str) and note.startswith("llm-ocr:template-version:"):
                pass
            elif isinstance(note, str):
                portable_note = note
            cells.append(
                CanonicalOutputLayoutCell.model_validate(
                    {
                        "row": item.get("row"),
                        "column": item.get("column"),
                        "value": item.get("value"),
                        "format": item.get("format", {}),
                        "binding": binding,
                        "note": portable_note,
                        "dataValidation": item.get("dataValidation"),
                        "textFormatRuns": item.get("textFormatRuns", []),
                    }
                )
            )
        try:
            return CanonicalOutputLayout.model_validate(
                {
                    "sheetName": raw.get("sheetName", row.source_sheet_name),
                    "rowCount": raw.get("rowCount"),
                    "columnCount": raw.get("columnCount"),
                    "frozenRowCount": raw.get("frozenRowCount", 0),
                    "frozenColumnCount": raw.get("frozenColumnCount", 0),
                    "hideGridlines": raw.get("hideGridlines", False),
                    "cells": cells,
                    "merges": [
                        CanonicalOutputLayoutMerge.model_validate(item)
                        for item in raw.get("merges", [])
                    ],
                    "rowHeights": raw.get("rowHeights", {}),
                    "columnWidths": raw.get("columnWidths", {}),
                }
            )
        except ValueError as exc:
            raise TemplatePackageExportError(
                "OUTPUT_LAYOUT_INVALID", "出力レイアウトをCanonical化できません。"
            ) from exc

    @staticmethod
    def _safe_warnings(quality: dict[str, object]) -> list[str]:
        warnings = quality.get("warnings")
        if not isinstance(warnings, list):
            return []
        return sorted(
            {
                item
                for item in warnings
                if isinstance(item, str) and _SAFE_WARNING_CODE.fullmatch(item)
            }
        )
