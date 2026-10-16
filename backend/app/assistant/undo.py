"""Compile inverses from durable server history, never from client snapshots."""

from __future__ import annotations

import copy
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant.contracts import AssistantContext, OcrContext, TemplateContext
from app.assistant.models import AssistantProposal
from app.assistant.operations import READING_LABELS, OperationRegistry, PlannedChange, refused
from app.db.mutation import require_revision
from app.db.mutation_models import ResourceOperation
from app.domain.ocr_models import FieldReading, OcrDocument
from app.layout_analysis.models import LayoutDefinition
from app.services.layout_service import LayoutService
from app.services.template_mutation import require_draft, require_isolated_ids

CONTEXT: TypeAdapter[OcrContext | TemplateContext] = TypeAdapter(AssistantContext)


def resource(context: OcrContext | TemplateContext) -> tuple[str, str]:
    return context.resource_kind, context.job_id if isinstance(
        context, OcrContext
    ) else context.version_id


def compile_undo(
    registry: OperationRegistry,
    session: Session,
    current_context: OcrContext | TemplateContext,
    record: ResourceOperation,
) -> PlannedChange:
    if resource(current_context) != (record.resource_kind, record.resource_id):
        raise refused(
            "この書類・テンプレートの操作ではありません。", "ASSISTANT_OPERATION_NOT_FOUND", 404
        )
    if record.undo_of_id or not record.inverse_json:
        raise refused(
            "取り消した操作を再び取り消すことはできません。", "ASSISTANT_UNDO_UNAVAILABLE"
        )
    if session.scalar(
        select(ResourceOperation.id).where(ResourceOperation.undo_of_id == record.id)
    ):
        raise refused("この操作はすでに取り消されています。", "ASSISTANT_ALREADY_UNDONE")
    if current_context.resource_revision != record.after_revision:
        raise refused("後続の変更があります。以前の操作は取り消せません。", "REVISION_CONFLICT")
    inverse = record.inverse_json
    context = CONTEXT.validate_python(
        {**inverse["context"], "resourceRevision": record.after_revision}
    )
    if resource(context) != resource(current_context) or inverse.get("contractVersion") != 1:
        raise refused("取り消し履歴の対応情報が一致しません。", "ASSISTANT_UNDO_UNAVAILABLE")
    restore = copy.deepcopy(inverse["restore"])
    if restore != record.before_json["data"]:
        raise refused("取り消し履歴が一致しません。", "ASSISTANT_UNDO_UNAVAILABLE")
    proposal = session.scalar(
        select(AssistantProposal).where(
            AssistantProposal.consumed_request_key == record.request_key,
        )
    )
    if proposal is None:
        raise refused("元の変更案が見つかりません。", "ASSISTANT_UNDO_UNAVAILABLE")
    effects: list[dict[str, Any]] = []
    candidate = None
    if isinstance(context, OcrContext):
        job, stored = registry.ocr._load(session, context.job_id)
        if job.status != "REVIEW_REQUIRED" or stored.revision != record.after_revision:
            raise refused("確定状態または値が変わりました。取り消せません。", "REVISION_CONFLICT")
        document = OcrDocument.model_validate(stored.current_corrected_json)
        reading = document.fields.get(str(context.target_path).removeprefix("fields."))
        previews = [
            p
            for p in registry.ocr._response(session, job, stored)["fieldPreviews"]
            if p["mappingId"] == context.mapping_id
            and p["targetPath"] == context.target_path
            and p["mappingType"] == "text"
        ]
        if (
            reading is None
            or len(previews) != 1
            or reading.model_dump(mode="json", by_alias=True) != record.after_json["data"]
        ):
            raise refused("現在値と操作履歴が一致しません。", "ASSISTANT_UNDO_MISMATCH")
        restored = FieldReading.model_validate(restore)
        if (
            restored.source_region_id != reading.source_region_id
            or restored.bounding_box != reading.bounding_box
        ):
            raise refused("欄の対応情報を変更する取り消しはできません。", "ASSISTANT_UNDO_MISMATCH")
        effects = [
            {
                "label": "読取状態",
                "before": READING_LABELS[reading.status],
                "after": READING_LABELS[restored.status],
            },
            {"label": "候補・不確実な理由", "before": "変更後", "after": "元の状態へ復元"},
            {"label": "既存の確認メモ", "before": "保持", "after": "保持"},
        ]
    else:
        version = registry.authoring.version(session, context.version_id)
        require_revision(version, record.after_revision)
        require_draft(version)
        require_isolated_ids(version, context.cell_ids)
        layout = LayoutDefinition.model_validate(version.layout_json)
        cell = next(
            (c for c in layout.cells if c.id == record.after_json["target"]["cellId"]), None
        )
        if cell is None or cell.model_dump(mode="json", by_alias=True) != record.after_json["data"]:
            raise refused("現在のセルと操作履歴が一致しません。", "ASSISTANT_UNDO_MISMATCH")
        registry._page(session, version, layout, context, cell)
        candidate_json = layout.model_dump(mode="json", by_alias=True)
        candidate_json["cells"] = [
            restore if c["id"] == cell.id else c for c in candidate_json["cells"]
        ]
        candidate = LayoutDefinition.model_validate(candidate_json)
        ready, _, _ = LayoutService(
            registry.authoring.database, registry.authoring.storage
        ).prepare_layout(session, version, candidate)
        if ready != candidate.model_copy(
            update={"record_structure_verified": ready.record_structure_verified}
        ):
            raise refused(
                "追加の構造変更が必要なため取り消せません。", "ASSISTANT_UNEXPECTED_EFFECT"
            )
        candidate = ready
        effects = [{"label": "読取範囲の確認", "before": "現在の状態", "after": "再確認が必要"}]
        old_mappings = {m.mapping_id: m for m in LayoutService._as_field_mappings(layout)}
        new_mappings = {m.mapping_id: m for m in LayoutService._as_field_mappings(candidate)}
        effects.extend(
            {
                "label": f"出力項目「{old_mappings[key].label}」",
                "before": old_mappings[key].label,
                "after": mapping.label,
            }
            for key, mapping in new_mappings.items()
            if key in old_mappings and old_mappings[key].label != mapping.label
        )
        if record.operation == "template.rename_choice":
            effects += [
                {**e, "before": e["after"], "after": e["before"]}
                for e in proposal.plan_json["effects"]
                if e["label"] == "選択肢の保存値"
            ]
    return PlannedChange(
        {
            "contractVersion": 1,
            "resourceKind": record.resource_kind,
            "resourceId": record.resource_id,
            "revision": record.after_revision,
            "operation": record.operation,
            "value": None,
            "undoOfId": record.id,
            "restoreContext": context.model_dump(mode="json", by_alias=True),
            "target": record.before_json["target"],
            "before": proposal.plan_json["after"],
            "after": proposal.plan_json["before"],
            "beforeData": record.after_json["data"],
            "afterData": restore,
            "effects": effects,
        },
        candidate,
    )
