from __future__ import annotations

from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import OcrSettings
from app.core.errors import AppError
from app.db.models import Template, TemplateDefinitionRevision, TemplateVersion
from app.db.session import Database
from app.domain.generic_template_mapping import generic_field_locations
from app.domain.template_config import FieldMappingConfig, MarkOptionConfig
from app.domain.template_definition import (
    TemplateBox,
    TemplateCell,
    TemplateDefinition,
    TemplateDefinitionRevisionView,
    TemplateDefinitionValidation,
    TemplateMarkSet,
)
from app.layout_analysis.geometry import analyze_geometry
from app.layout_analysis.models import LayoutDefinition
from app.services.relationship_analyzer import infer_relationships_with_openai
from app.services.template_management_service import TemplateManagementService
from app.services.template_mutation import begin_immediate, template_mutation_lock
from app.storage.local_storage import LocalStorageService
from app.validation.template_definition_validator import validate_template_definition


class TemplateDefinitionService:
    def __init__(
        self,
        database: Database,
        ocr_settings: OcrSettings | None = None,
        storage: LocalStorageService | None = None,
    ) -> None:
        self._database = database
        self._ocr_settings = ocr_settings
        self._storage = storage

    def latest(self, version_id: str) -> TemplateDefinitionRevisionView | None:
        with self._database.session() as session:
            version = self._require_version(session, version_id)
            if version.quality_json.get("templateDefinitionLayoutInputRevision") != (
                version.quality_json.get("layoutInputRevision")
            ):
                return None
            row = session.scalar(
                select(TemplateDefinitionRevision)
                .where(TemplateDefinitionRevision.template_version_id == version_id)
                .order_by(TemplateDefinitionRevision.revision.desc())
                .limit(1)
            )
            return self._view(row) if row else None

    def history(self, version_id: str) -> list[TemplateDefinitionRevisionView]:
        with self._database.session() as session:
            self._require_version(session, version_id)
            rows = session.scalars(
                select(TemplateDefinitionRevision)
                .where(TemplateDefinitionRevision.template_version_id == version_id)
                .order_by(TemplateDefinitionRevision.revision.desc())
            )
            return [self._view(row) for row in rows]

    async def generate(self, version_id: str) -> TemplateDefinitionRevisionView:
        with self._database.session() as session:
            version = self._require_version(session, version_id)
            template = session.get(Template, version.template_id)
            layout = self._parse_layout(version)
            template_name = template.name if template is not None else "帳票"
        cells = self._cells_from_layout(layout)
        geometry = analyze_geometry(cells, layout.canonical_width, layout.canonical_height)
        if self._ocr_settings is None:
            from app.services.relationship_analyzer import infer_relationships

            draft = infer_relationships(cells, geometry, layout)
        else:
            draft = await infer_relationships_with_openai(
                cells, geometry, self._ocr_settings, layout
            )
        phase_confidences = [phase.confidence for phase in draft.phases]
        definition = TemplateDefinition(
            template_version_id=version_id,
            template_name=template_name,
            state="AI_GENERATED",
            canonical_width=layout.canonical_width,
            canonical_height=layout.canonical_height,
            cells=cells,
            geometry=geometry,
            standalone_fields=draft.standalone_fields,
            sections=draft.sections,
            unassigned_cell_ids=draft.unassigned_cell_ids,
            confidence=round(mean(phase_confidences), 3) if phase_confidences else 0,
            phases=draft.phases,
            warnings=draft.warnings,
        )
        return self._append(version_id, "AI_GENERATED", definition)

    def save_human_revision(
        self, version_id: str, definition: TemplateDefinition
    ) -> TemplateDefinitionRevisionView:
        if definition.template_version_id != version_id:
            raise AppError(
                status=422,
                code="TEMPLATE_DEFINITION_VERSION_MISMATCH",
                title="Template definition version mismatch",
                detail="TemplateDefinitionの対象テンプレート版が一致しません。",
            )
        return self._append(
            version_id,
            "HUMAN_REVISION",
            definition.model_copy(update={"state": "USER_REVIEWING"}),
        )

    def approve(
        self, version_id: str, definition: TemplateDefinition | None = None
    ) -> TemplateDefinitionRevisionView:
        candidate = definition
        if candidate is None:
            latest = self.latest(version_id)
            if latest is None:
                raise AppError(
                    status=409,
                    code="TEMPLATE_DEFINITION_REQUIRED",
                    title="Template definition is required",
                    detail="先に汎用テンプレート定義を生成してください。",
                )
            candidate = latest.definition
        if candidate.template_version_id != version_id:
            raise AppError(
                status=422,
                code="TEMPLATE_DEFINITION_VERSION_MISMATCH",
                title="Template definition version mismatch",
                detail="TemplateDefinitionの対象テンプレート版が一致しません。",
            )
        approved = candidate.model_copy(update={"state": "APPROVED"})
        validation = validate_template_definition(approved)
        if not validation.valid:
            first = next(issue for issue in validation.issues if issue.severity == "ERROR")
            raise AppError(
                status=409,
                code="TEMPLATE_DEFINITION_INVALID",
                title="Template definition is invalid",
                detail=first.message,
            )
        if self._storage is None:
            raise RuntimeError("Template definition approval requires storage")
        self._save_generic_mappings(version_id, approved)
        return self._append(version_id, "APPROVED", approved)

    def _save_generic_mappings(self, version_id: str, definition: TemplateDefinition) -> None:
        cells = {cell.id: cell for cell in definition.cells}
        mappings: list[FieldMappingConfig] = []
        for location in generic_field_locations(definition):
            field = location.field
            cell = cells[field.value_cell_id]
            mark_set = next(
                (item for item in cell.mark_sets if item.id == field.mark_set_id),
                None,
            )
            mappings.append(
                FieldMappingConfig(
                    mapping_id=location.mapping_key,
                    target_path=f"customFields.{location.mapping_key}",
                    label=field.name[:100],
                    x=cell.bbox.x,
                    y=cell.bbox.y,
                    width=cell.bbox.width,
                    height=cell.bbox.height,
                    padding=0 if mark_set is not None else 4,
                    enabled=True,
                    model_detail="high",
                    printed_label_text=(
                        cells[field.label_cell_id].text
                        if field.label_cell_id and field.label_cell_id in cells
                        else ""
                    ),
                    mapping_type="mark" if mark_set is not None else "text",
                    mark_selection_mode=(
                        mark_set.selection_mode if mark_set is not None else "single"
                    ),
                    mark_options=(
                        [
                            MarkOptionConfig.model_validate(
                                option.model_dump(mode="json", by_alias=True)
                            )
                            for option in mark_set.options
                        ]
                        if mark_set is not None
                        else []
                    ),
                )
            )
        storage = self._storage
        if storage is None:
            raise RuntimeError("Template definition approval requires storage")
        TemplateManagementService(self._database, storage).save_field_mappings(
            version_id, mappings
        )

    def _append(
        self,
        version_id: str,
        source: str,
        definition: TemplateDefinition,
    ) -> TemplateDefinitionRevisionView:
        validation = validate_template_definition(definition)
        with template_mutation_lock(), self._database.session() as session:
            begin_immediate(session)
            version = self._require_version(session, version_id)
            layout_cell_ids = {
                str(cell.get("id"))
                for cell in version.layout_json.get("cells", [])
                if isinstance(cell, dict) and cell.get("id")
            }
            definition_cell_ids = {cell.id for cell in definition.cells}
            if (
                definition.canonical_width != version.canonical_width
                or definition.canonical_height != version.canonical_height
                or definition_cell_ids != layout_cell_ids
            ):
                raise AppError(
                    status=409,
                    code="TEMPLATE_DEFINITION_LAYOUT_STALE",
                    title="Template definition is stale",
                    detail="矩形またはセル種別の変更後に構造草案を再生成してください。",
                )
            next_revision = (
                session.scalar(
                    select(func.max(TemplateDefinitionRevision.revision)).where(
                        TemplateDefinitionRevision.template_version_id == version_id
                    )
                )
                or 0
            ) + 1
            row = TemplateDefinitionRevision(
                template_version_id=version_id,
                revision=next_revision,
                source=source,
                definition_json=definition.model_dump(mode="json", by_alias=True),
                validation_json=validation.model_dump(mode="json", by_alias=True),
            )
            session.add(row)
            version.quality_json = {
                **version.quality_json,
                "templateDefinitionRevision": next_revision,
                "templateDefinitionState": definition.state,
                "templateDefinitionValid": validation.valid,
                "templateDefinitionLayoutInputRevision": version.quality_json.get(
                    "layoutInputRevision"
                ),
            }
            session.commit()
            session.refresh(row)
            return self._view(row)

    @staticmethod
    def _cells_from_layout(layout: LayoutDefinition) -> list[TemplateCell]:
        parent_ids = {
            section.parent_label_cell_id
            for section in layout.record_sections
            if section.parent_label_cell_id is not None
        }
        role_map = {
            "label": "LABEL",
            "parent_label": "PARENT_LABEL",
            "input": "VALUE",
            "mark": "VALUE",
            "record_key_cell": "IGNORE",
            "column_key_cell": "IGNORE",
            "ignored": "IGNORE",
        }
        result: list[TemplateCell] = []
        for cell in layout.cells:
            cell_type = role_map[cell.role]
            if cell.id in parent_ids:
                cell_type = "PARENT_LABEL"
            result.append(
                TemplateCell(
                    id=cell.id,
                    type=cell_type,
                    text=(cell.printed_label_text or cell.text).strip(),
                    bbox=TemplateBox(
                        x=cell.x,
                        y=cell.y,
                        width=cell.width,
                        height=cell.height,
                    ),
                    detection_confidence=cell.confidence,
                    text_confidence=cell.text_confidence,
                    source_role=cell.role,
                    mark_sets=[
                        TemplateMarkSet.model_validate(
                            mark_set.model_dump(mode="json", by_alias=True)
                        )
                        for mark_set in cell.mark_sets
                    ],
                )
            )
        return result

    @staticmethod
    def _parse_layout(version: TemplateVersion) -> LayoutDefinition:
        if not version.layout_json:
            raise AppError(
                status=409,
                code="TEMPLATE_LAYOUT_REQUIRED",
                title="Template layout is required",
                detail="先に帳票上の矩形を検出し、セル種別を確認してください。",
            )
        return LayoutDefinition.model_validate(version.layout_json)

    @staticmethod
    def _require_version(session: Session, version_id: str) -> TemplateVersion:
        version = session.get(TemplateVersion, version_id)
        if version is None:
            raise AppError(
                status=404,
                code="TEMPLATE_NOT_FOUND",
                title="Template not found",
                detail="指定されたテンプレート版が見つかりません。",
            )
        return version

    @staticmethod
    def _view(row: TemplateDefinitionRevision) -> TemplateDefinitionRevisionView:
        return TemplateDefinitionRevisionView(
            id=row.id,
            revision=row.revision,
            source=row.source,
            definition=TemplateDefinition.model_validate(row.definition_json),
            validation=TemplateDefinitionValidation.model_validate(row.validation_json),
            created_at=row.created_at,
        )
