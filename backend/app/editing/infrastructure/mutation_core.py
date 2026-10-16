"""One business persistence path for legacy transport and editing.v2."""

from __future__ import annotations

import copy
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.mutation_models import ResourceOperation
from app.domain.enums import RevisionSource
from app.domain.ocr_models import FieldReading, ReviewItem
from app.domain.template_config import DocumentIdentityConfig, DocumentNameConfig
from app.editing.catalog import Catalog, CatalogService
from app.editing.commands import CommandCompiler, Plan
from app.editing.errors import reject
from app.editing.models import EditingOperation, EditingSession
from app.services.mutation_receipt_service import MutationReceiptService
from app.services.ocr_result_service import CorrectionValues, OcrResultService
from app.services.template_authoring_service import TemplateAuthoringService


class MutationCore:
    def __init__(
        self,
        authoring: TemplateAuthoringService,
        ocr: OcrResultService,
        catalogs: CatalogService,
        compiler: CommandCompiler,
    ) -> None:
        self.authoring, self.ocr, self.catalogs, self.compiler = authoring, ocr, catalogs, compiler
        self.receipts = MutationReceiptService

    @staticmethod
    def local_scope(owner: EditingSession, plan: Plan) -> None:
        if owner.mode != "local":
            return
        selected = {(s["kind"], s["entityId"]) for s in owner.context_json["selected"]}
        if not selected:
            raise reject("LOCAL_SCOPE_MISMATCH", "局所録音の対象欄を選択してください。")
        if plan.before["kind"] == "ocr":
            changed = {
                ("field", key)
                for key, value in plan.after["fields"].items()
                if value != plan.before["fields"][key]
            }
        else:
            from app.editing.scope import affected_cells

            cells, unbound_meaning = affected_cells(plan)
            if unbound_meaning:
                raise reject("LOCAL_SCOPE_MISMATCH", "未接続の意味の変更は選択欄に限定できません。")
            changed = {("cell", cell) for cell in cells}
        if not changed <= selected:
            raise reject("LOCAL_SCOPE_MISMATCH", "局所録音では選択欄の外側を変更できません。")

    def prepare_template(self, owner: EditingSession, catalog: Catalog, plan: Plan) -> None:
        if not plan.changed or plan.after["kind"] != "template":
            return
        layout = self.compiler.validate_layout(plan.after["layout"])
        identities = [
            DocumentIdentityConfig.model_validate(f) for f in plan.after["documentIdentityFields"]
        ]
        spec = plan.after["documentName"]
        document_name = DocumentNameConfig.model_validate(spec) if spec is not None else None
        try:
            prepared = self.authoring.prepare(
                owner.resource_id,
                catalog.revision,
                layout,
                document_identity_fields=identities,
                document_name=document_name,
            )
        except ValidationError as error:
            raise reject(
                "INVALID_GRAPH", "書類情報と意味付けの依存関係が不正です。", 422
            ) from error
        expected = layout.model_copy(
            update={"record_structure_verified": prepared.layout.record_structure_verified}
        )
        if prepared.layout != expected:
            raise reject("INVALID_GRAPH", "保存時に暗黙の保存先変更が必要になりました。", 422)

        def address(fields: list[DocumentIdentityConfig]) -> set[tuple[str, str]]:
            return {(f.mapping_id, f.target_path) for f in fields}

        if (
            address(identities) != address(prepared.config.document_identity_fields)
            or document_name != prepared.config.document_name
        ):
            raise reject(
                "DEPENDENCY_CONFLICT", "識別・書類名の依存を同じ操作群で解決してください。"
            )
        plan.prepared = prepared
        plan.after["layout"] = prepared.layout.model_dump(mode="json", by_alias=True)
        plan.after["documentIdentityFields"] = [
            f.model_dump(mode="json", by_alias=True)
            for f in prepared.config.document_identity_fields
        ]

    @staticmethod
    def page_scope(owner: EditingSession, catalog: Catalog, plan: Plan) -> None:
        scope = owner.context_json["pageScope"]
        if plan.before["kind"] != "template" or scope["mode"] == "all" or scope["pageId"] is None:
            return
        from app.editing.catalog import CatalogService
        from app.editing.scope import affected_cells
        from app.layout_analysis.models import LayoutDefinition

        visible = {t.entity_id for t in catalog.targets.values() if t.kind == "cell"}
        for graph in (plan.before["layout"], plan.after["layout"]):
            layout = LayoutDefinition.model_validate(graph)
            visible.update(
                c.id
                for c in layout.cells
                if c.virtual
                and CatalogService.cell_page(layout, c, catalog.pages) == scope["pageId"]
            )
        changed, _ = affected_cells(plan)
        if not changed <= visible:
            raise reject("PAGE_SCOPE_INVALID", "変更が現在のページの外側にも影響します。")

    def latest(self, session: Session, owner: EditingSession) -> ResourceOperation | None:
        return session.scalar(
            select(ResourceOperation)
            .where(
                ResourceOperation.resource_kind == owner.resource_kind,
                ResourceOperation.resource_id == owner.resource_id,
            )
            .order_by(ResourceOperation.after_revision.desc())
            .limit(1)
        )

    def undoable(
        self,
        session: Session,
        owner: EditingSession,
        catalog: Catalog,
        record: ResourceOperation | None,
    ) -> bool:
        return bool(
            record is not None
            and record.undo_of_id is None
            and record.inverse_json is not None
            and record.after_revision == catalog.revision
            and record.after_json == catalog.state
            and catalog.editable
            and session.get(EditingOperation, record.id) is not None
        )

    @staticmethod
    def changes(plan: Plan, saved: Catalog) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not plan.changed:
            return result
        if plan.before["kind"] == "ocr":
            changed = [
                ("field", key)
                for key, value in plan.after["fields"].items()
                if value != plan.before["fields"][key]
            ]
        else:
            changed = []
            for kind, collection in (
                ("cell", "cells"),
                ("meaning", "labelPool"),
                ("relation", "relations"),
                ("record_section", "recordSections"),
                ("record_group", "recordGroups"),
            ):
                before = {v["id"]: v for v in plan.before["layout"][collection]}
                after = {v["id"]: v for v in plan.after["layout"][collection]}
                changed.extend(
                    (kind, key)
                    for key in before.keys() | after.keys()
                    if before.get(key) != after.get(key)
                )
        if plan.before["kind"] == "template":
            for key, label in (("documentIdentityFields", "書類識別"), ("documentName", "書類名")):
                if plan.before[key] != plan.after[key]:
                    result.append({"handle": None, "kind": "field", "name": label, "pageId": None})
        old_targets = {(t.kind, t.entity_id): t for t in plan.touched}
        for kind, key in sorted(changed):
            current = next(
                (t for t in saved.targets.values() if t.kind == kind and t.entity_id == key),
                None,
            )
            target = current or old_targets.get((kind, key))
            label = target.label if target else "意味の接続"
            if target is None and plan.before["kind"] == "template":
                for collection in ("cells", "labelPool", "recordSections", "recordGroups"):
                    old = next(
                        (v for v in plan.before["layout"][collection] if v["id"] == key), None
                    )
                    if old:
                        label = (
                            old.get("fieldLabel")
                            or old.get("text")
                            or old.get("label")
                            or old.get("displayName")
                            or old.get("title")
                            or f"{old.get('order', '')}行目"
                        )
                        break
            result.append(
                {
                    "handle": current.handle if current else None,
                    "kind": kind,
                    "name": label,
                    "pageId": target.page_id if target else None,
                }
            )
        return result

    def persist(
        self,
        session: Session,
        owner: EditingSession,
        catalog: Catalog,
        plan: Plan,
        *,
        request_key: str,
        contract_version: str,
        origin: str,
        undo_of: str | None = None,
        physical_edit: bool = False,
        supersedes: str | None = None,
        turn_id: str | None = None,
    ) -> tuple[Catalog, ResourceOperation | None]:
        if not plan.changed:
            return catalog, None
        resource_kind, resource_id = owner.resource_kind, owner.resource_id
        if resource_kind == "template":
            if plan.prepared is None:
                raise reject("INVALID_GRAPH", "テンプレート保存が準備されていません。")
            self.authoring.persist(session, plan.prepared)
        else:
            job, stored = self.ocr._load(session, resource_id)
            self.ocr.correct_in_transaction(
                session,
                job.id,
                CorrectionValues(
                    catalog.revision,
                    {k: FieldReading.model_validate(v) for k, v in plan.after["fields"].items()},
                    [ReviewItem.model_validate(v) for v in plan.after["reviewItems"]],
                ),
                source=RevisionSource.ASSISTANT_UNDO
                if undo_of is not None
                else RevisionSource.USER_EDIT
                if origin == "manual"
                else RevisionSource.ASSISTANT,
            )
        saved = self.catalogs.build(session, owner)
        plan.after = copy.deepcopy(saved.state)
        if saved.revision != catalog.revision + 1:
            raise reject("REVISION_CONFLICT", "一括保存のrevisionが一回の増分になっていません。")
        record = self.receipts.record_operation(
            session,
            request_key=request_key,
            resource_kind=resource_kind,
            resource_id=resource_id,
            operation="editing.manual_geometry"
            if physical_edit
            else "editing.undo"
            if undo_of is not None
            else "editing.batch",
            before_revision=catalog.revision,
            after_revision=saved.revision,
            before=plan.before,
            after=plan.after,
            inverse=None
            if physical_edit
            else {
                "contractVersion": contract_version,
                "state": plan.before,
                **({"supersedesOperationId": supersedes} if supersedes else {}),
            },
            undo_of_id=undo_of if undo_of is not None else None,
        )
        session.add(
            EditingOperation(
                operation_id=record.id,
                contract_version=contract_version,
                origin=origin,
                turn_id=turn_id,
            )
        )
        return saved, record
