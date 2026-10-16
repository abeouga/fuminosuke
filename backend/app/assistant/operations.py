from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.assistant.contracts import OcrContext, Operation, TemplateContext
from app.core.errors import AppError
from app.db.models import TemplateVersion
from app.db.mutation import require_revision
from app.domain.enums import RevisionSource
from app.domain.ocr_models import FieldReading, OcrDocument, ReadingStatus
from app.layout_analysis.models import LayoutDefinition
from app.services.layout_service import LayoutService
from app.services.mutation_receipt_service import MutationReceiptService
from app.services.ocr_result_service import CorrectionValues, OcrResultService
from app.services.print_layout_service import PrintLayoutService
from app.services.template_authoring_service import TemplateAuthoringService
from app.services.template_mutation import require_draft, require_isolated_ids

CATEGORIES = {
    "input": "記入欄",
    "label": "項目名",
    "parent_label": "見出し",
    "ignored": "読み取らない",
}
READING_LABELS = {
    ReadingStatus.READABLE: "読めた",
    ReadingStatus.UNCERTAIN: "不確実",
    ReadingStatus.BLANK: "空欄",
}


def refused(detail: str, code: str = "ASSISTANT_OPERATION_REJECTED", status: int = 409) -> AppError:
    return AppError(status=status, code=code, title=code, detail=detail)


@dataclass(frozen=True)
class PlannedChange:
    data: dict[str, Any]
    candidate: LayoutDefinition | None = None

    @property
    def digest(self) -> str:
        return MutationReceiptService.fingerprint(self.data)


class OperationRegistry:
    def __init__(self, authoring: TemplateAuthoringService, ocr: OcrResultService) -> None:
        self.authoring, self.ocr = authoring, ocr

    def compile(
        self,
        session: Session,
        context: OcrContext | TemplateContext,
        operation: Operation,
        value: str | None,
    ) -> PlannedChange:
        if context.resource_revision is None:
            raise refused("保存済みの世代を取得してから変更案を作成してください。")
        try:
            if isinstance(context, OcrContext):
                return self._ocr(session, context, operation, value)
            return self._template(session, context, operation, value)
        except ValidationError as error:
            raise refused(
                "名称または選択値が保存規則に合いません。既存の編集画面で設定を確認してください。",
                status=422,
            ) from error

    def _ocr(
        self, session: Session, context: OcrContext, operation: Operation, value: str | None
    ) -> PlannedChange:
        if operation != "ocr.set_value" or not context.mapping_id or not context.target_path:
            raise refused("読み取り欄を1つ選択してください。")
        job, stored = self.ocr._load(session, context.job_id)
        if job.status != "REVIEW_REQUIRED" or stored.revision != context.resource_revision:
            raise refused(
                "確定状態または読み取り結果が変わりました。最新結果を確認してください。",
                "REVISION_CONFLICT",
            )
        current = OcrDocument.model_validate(stored.current_corrected_json)
        key = context.target_path.removeprefix("fields.")
        reading = current.fields.get(key) if context.target_path.startswith("fields.") else None
        matches = [
            p
            for p in self.ocr._response(session, job, stored)["fieldPreviews"]
            if p["mappingId"] == context.mapping_id and p["targetPath"] == context.target_path
        ]
        if (
            reading is None
            or reading.source_region_id != context.mapping_id
            or len(matches) != 1
            or matches[0]["mappingType"] != "text"
        ):
            raise refused("選択欄と受付時の対応情報が一致しません。", "ASSISTANT_TARGET_MISMATCH")
        if value is not None and (not value.strip() or len(value) > 500):
            raise refused("文字列を入力するか、明示的な空欄を選択してください。", status=422)
        before = reading.model_dump(mode="json", by_alias=True)
        after = {
            **before,
            "raw": value,
            "normalized": value,
            "status": "blank" if value is None else "readable",
            "alternatives": [],
            "reviewReason": None,
        }
        FieldReading.model_validate(after)
        if before == after:
            raise refused("変更前と同じ内容です。", "ASSISTANT_NO_CHANGE")
        label = next(
            (
                f["label"]
                for f in job.config_snapshot_json.get("reviewStructure", {}).get("fields", [])
                if f.get("targetPath") == context.target_path
            ),
            current.field_labels.get(key, key),
        )
        return PlannedChange(
            {
                "contractVersion": 1,
                "resourceKind": "ocr",
                "resourceId": job.id,
                "revision": stored.revision,
                "operation": operation,
                "value": value,
                "target": {
                    "label": label,
                    "mappingId": context.mapping_id,
                    "targetPath": context.target_path,
                },
                "before": reading.normalized if reading.normalized is not None else reading.raw,
                "after": value,
                "beforeData": before,
                "afterData": after,
                "effects": [
                    {
                        "label": "読取状態",
                        "before": READING_LABELS[reading.status],
                        "after": READING_LABELS[ReadingStatus.BLANK]
                        if value is None
                        else READING_LABELS[ReadingStatus.READABLE],
                    },
                    {"label": "既存の確認メモ", "before": "保持", "after": "保持"},
                ],
            }
        )

    def _page(
        self,
        session: Session,
        version: TemplateVersion,
        layout: LayoutDefinition,
        context: TemplateContext,
        cell: Any,
    ) -> None:
        saved = version.quality_json.get("printLayout")
        if not saved:
            if context.page_id is not None:
                raise refused("選択ページが保存状態と一致しません。", "ASSISTANT_TARGET_MISMATCH")
            return
        page = next(
            (p for p in saved["pages"] if context.page_id == f"{p['page']}:{p['range']}"), None
        )
        if page is None:
            raise refused("保存済みのページを選択してください。", "ASSISTANT_TARGET_MISMATCH")
        if cell.virtual:
            valid = cell.page_range == page["range"]
        else:
            # The existing source validator proves IDs still occupy their XLSX cells.
            source = PrintLayoutService(self.authoring.database, self.authoring.storage)._source(
                session, version, layout.model_dump(mode="json", by_alias=True)
            )
            if source["unsupported"]:
                raise refused("元セルとの対応を検証できません。", "ASSISTANT_TARGET_MISMATCH")
            merged = next((m for m in layout.cell_merges if m.parent_cell_id == cell.id), None)
            atoms = [p.cell for p in merged.parts] if merged else [cell]
            rows = [
                int(match[1])
                for atom in atoms
                if (match := re.match(r"^xlsx_[A-Z]{1,3}([1-9]\d*)(?:$|_)", atom.id))
            ]
            valid = len(rows) == len(atoms) and all(
                page["startRow"] <= row <= page["endRow"] for row in rows
            )
        if not valid:
            raise refused(
                "選択セルは指定ページの所有範囲にありません。", "ASSISTANT_TARGET_MISMATCH"
            )

    @staticmethod
    def _category(layout: LayoutDefinition, cell: Any, value: str | None) -> None:
        if value not in CATEGORIES or cell.role not in CATEGORIES:
            raise refused("この欄の種類は変更できません。")
        structural = {
            "relations": [r.model_dump() for r in layout.relations],
            "sections": [s.model_dump() for s in layout.record_sections],
            "groups": [g.model_dump() for g in layout.record_groups],
            "merges": [m.model_dump() for m in layout.cell_merges],
        }

        def contains(node: Any) -> bool:
            return (
                node == cell.id
                if isinstance(node, str)
                else any(contains(v) for v in node.values())
                if isinstance(node, dict)
                else any(contains(v) for v in node)
                if isinstance(node, list)
                else False
            )

        if (
            cell.virtual
            or cell.reading_mode
            or cell.field_label
            or cell.mark_sets
            or cell.composite_subfields
            or cell.parent_label_cell_id
            or any(c.parent_label_cell_id == cell.id for c in layout.cells)
            or contains(structural)
        ):
            raise refused(
                "接続・階層・繰り返し・読取設定の変更が必要です。既存の編集画面で整理してください。",
                "ASSISTANT_CATEGORY_HAS_DEPENDENCIES",
            )

    def _template(
        self, session: Session, context: TemplateContext, operation: Operation, value: str | None
    ) -> PlannedChange:
        if not operation.startswith("template.") or len(context.cell_ids) != 1:
            raise refused("テンプレートのセルを1つ選択してください。")
        version = self.authoring.version(session, context.version_id)
        assert context.resource_revision is not None
        require_revision(version, context.resource_revision)
        require_draft(version)
        require_isolated_ids(version, context.cell_ids)
        layout = LayoutDefinition.model_validate(version.layout_json)
        cell = next((c for c in layout.cells if c.id == context.cell_ids[0]), None)
        if cell is None:
            raise refused("選択セルが版にありません。", "ASSISTANT_TARGET_MISMATCH")
        self._page(session, version, layout, context, cell)
        before_cell = cell.model_dump(mode="json", by_alias=True)
        after_cell = copy.deepcopy(before_cell)
        effects: list[dict[str, Any]] = []
        before: str | None
        after: str | None = value
        if operation == "template.change_category":
            if context.mark_set_id or context.choice_id:
                raise refused("選択肢を指定したまま欄の種類を変更できません。")
            self._category(layout, cell, value)
            before, after = CATEGORIES[cell.role], CATEGORIES[str(value)]
            after_cell["role"] = value
        else:
            if value is None or not value.strip() or value != value.strip():
                raise refused("前後の空白を含まない名称を入力してください。", status=422)
            if operation == "template.rename_cell":
                if (
                    cell.role not in {"input", "label", "parent_label"}
                    or context.mark_set_id
                    or context.choice_id
                ):
                    raise refused("このセルの名称は変更できません。")
                key = "fieldLabel" if cell.role == "input" else "text"
                before = cell.field_label if key == "fieldLabel" else cell.text
                if key == "fieldLabel" and before is None:
                    before = next(
                        (
                            m.label
                            for m in LayoutService._as_field_mappings(layout)
                            if m.mapping_id == f"layout_{cell.id}"
                        ),
                        cell.printed_label_text or cell.text or None,
                    )
                after_cell[key] = value
            elif operation == "template.rename_choice":
                if cell.role != "mark" or not context.mark_set_id or not context.choice_id:
                    raise refused("選択肢の所有セルとIDを指定してください。")
                mark = next(
                    (s for s in after_cell.get("markSets", []) if s["id"] == context.mark_set_id),
                    None,
                )
                choice = (
                    next((o for o in mark["options"] if o["id"] == context.choice_id), None)
                    if mark
                    else None
                )
                if mark is None or choice is None:
                    raise refused("選択肢がこのセルに属していません。", "ASSISTANT_TARGET_MISMATCH")
                before = choice["label"]
                old_value = choice["value"]
                choice["label"] = value
                if mark.get("recognitionMode") in {"symbol", "checklist"}:
                    choice["value"] = value
                effects.append(
                    {"label": "選択肢の保存値", "before": old_value, "after": choice["value"]}
                )
            else:
                raise refused("許可されていない操作です。")
        if after_cell == before_cell:
            raise refused("変更前と同じ内容です。", "ASSISTANT_NO_CHANGE")
        candidate_json = layout.model_dump(mode="json", by_alias=True)
        candidate_json["cells"] = [
            after_cell if c["id"] == cell.id else c for c in candidate_json["cells"]
        ]
        candidate = LayoutDefinition.model_validate(candidate_json)
        ready, _, finalized = LayoutService(
            self.authoring.database, self.authoring.storage
        ).prepare_layout(session, version, candidate)
        expected = candidate.model_copy(
            update={"record_structure_verified": ready.record_structure_verified}
        )
        if ready != expected:
            raise refused(
                "追加の保存先変更が必要です。手動編集で解決してください。",
                "ASSISTANT_UNEXPECTED_EFFECT",
            )
        if layout.record_structure_verified != ready.record_structure_verified:
            effects.append(
                {
                    "label": "繰り返し構造の検証状態",
                    "before": "確認済み" if layout.record_structure_verified else "未確認",
                    "after": "確認済み" if ready.record_structure_verified else "未確認",
                }
            )
        if version.quality_json.get("layoutVerified"):
            effects.append({"label": "読取範囲の確認", "before": "確認済み", "after": "未確認"})
        if bool(version.quality_json.get("layoutFinalized")) != finalized:
            effects.append(
                {
                    "label": "欄の割当",
                    "before": "完了" if version.quality_json.get("layoutFinalized") else "未完了",
                    "after": "完了" if finalized else "未完了",
                }
            )
        candidate = ready
        if operation != "template.change_category":
            old = {m.mapping_id: m for m in LayoutService._as_field_mappings(layout)}
            new = {m.mapping_id: m for m in LayoutService._as_field_mappings(candidate)}
            if old.keys() != new.keys() or any(
                old[k].target_path != new[k].target_path for k in old
            ):
                raise refused("名称変更で保存先を変更できません。")
            effects.extend(
                {"label": f"出力項目「{old[k].label}」", "before": old[k].label, "after": m.label}
                for k, m in new.items()
                if old[k].label != m.label
            )
        mapping_id = (
            f"layout_{cell.id}_{context.mark_set_id}"[:100]
            if cell.role == "mark"
            else f"layout_{cell.id}"
        )
        target_label = cell.field_label or next(
            (
                m.label
                for m in LayoutService._as_field_mappings(layout)
                if m.mapping_id == mapping_id
            ),
            cell.text or "選択したセル",
        )
        return PlannedChange(
            {
                "contractVersion": 1,
                "resourceKind": "template",
                "resourceId": version.id,
                "revision": version.revision,
                "operation": operation,
                "value": value,
                "target": {
                    "label": target_label,
                    "cellId": cell.id,
                    "markSetId": context.mark_set_id,
                    "choiceId": context.choice_id,
                },
                "before": before,
                "after": after,
                "beforeData": before_cell,
                "afterData": after_cell,
                "effects": effects,
            },
            candidate,
        )

    def persist(
        self, session: Session, context: OcrContext | TemplateContext, planned: PlannedChange
    ) -> int:
        plan = planned.data
        if isinstance(context, OcrContext):
            job, stored = self.ocr._load(session, context.job_id)
            current = OcrDocument.model_validate(stored.current_corrected_json)
            fields = dict(current.fields)
            fields[str(context.target_path).removeprefix("fields.")] = FieldReading.model_validate(
                plan["afterData"]
            )
            result = self.ocr.correct_in_transaction(
                session,
                job.id,
                CorrectionValues(stored.revision, fields, current.review_items),
                source=RevisionSource.ASSISTANT,
            )
            return int(result["revision"])
        assert planned.candidate is not None
        version = self.authoring.version(session, context.version_id)
        config = self.authoring.snapshot(session, version)["config"]
        prepared = self.authoring.prepare(
            context.version_id,
            plan["revision"],
            planned.candidate,
            document_identity_fields=config.document_identity_fields,
            document_name=config.document_name,
        )
        if prepared.layout != planned.candidate:
            raise refused(
                "保存時に追加の構造変更が必要になりました。手動編集で解決してください。",
                "ASSISTANT_UNEXPECTED_EFFECT",
            )
        updated = self.authoring.persist(session, prepared)
        return updated.revision
