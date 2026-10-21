from __future__ import annotations

import hashlib
import hmac
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.ocr_models import OcrDocument
from app.editing.contracts import PageScope, TargetSearch
from app.editing.errors import reject
from app.editing.models import EditingSession
from app.editing.record_cells import matches_location, record_cells
from app.editing.registry import TEMPLATE_OPERATIONS
from app.layout_analysis.models import LayoutCell, LayoutDefinition
from app.services.layout_service import LayoutService
from app.services.ocr_result_service import OcrResultService
from app.services.print_layout_service import PrintLayoutService
from app.services.template_authoring_service import TemplateAuthoringService

INCOMPLETE_CODES = {
    "MARK_OPTIONS_REQUIRED",
    "MARK_TARGET_REQUIRED",
    "RECORD_STRUCTURE_REQUIRED",
    "RECORD_PARENT_LABEL_REQUIRED",
    "RECORD_KEY_REQUIRED",
}


@dataclass(frozen=True)
class Target:
    handle: str
    kind: str
    entity_id: str
    page_id: str | None
    label: str
    aliases: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)
    binding_path: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "kind": self.kind,
            "pageId": self.page_id,
            "name": self.label,
            "aliases": list(self.aliases),
            "allowedOperations": list(self.operations),
            **self.detail,
        }


@dataclass
class Catalog:
    revision: int
    generation: str
    editable: bool
    draft_state: dict[str, Any]
    targets: dict[str, Target]
    state: dict[str, Any]
    capabilities: tuple[str, ...]
    pages: list[dict[str, Any]] = field(default_factory=list)

    def resolve(self, handle: str, kind: str | tuple[str, ...]) -> Target:
        target = self.targets.get(handle)
        kinds = (kind,) if isinstance(kind, str) else kind
        if target is None:
            raise reject("TARGET_STALE", "対象が現在のページ・保存世代に属していません。")
        if target.kind not in kinds:
            raise reject("TARGET_KIND_MISMATCH", "指定した対象の種類が操作と一致しません。", 422)
        return target


class CatalogService:
    def __init__(self, authoring: TemplateAuthoringService, ocr: OcrResultService) -> None:
        self.authoring, self.ocr = authoring, ocr

    @staticmethod
    def _sign(owner: EditingSession, payload: Any) -> str:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hmac.new(owner.token_hash.encode(), raw.encode(), hashlib.sha256).hexdigest()

    def build(self, session: Session, owner: EditingSession) -> Catalog:
        if owner.resource_kind == "ocr":
            return self._ocr(session, owner)
        return self._template(session, owner)

    def _target(
        self,
        owner: EditingSession,
        revision: int,
        kind: str,
        entity: str,
        page: str | None,
        label: str,
        operations: tuple[str, ...] = (),
        aliases: tuple[str, ...] = (),
        binding_path: str | None = None,
        **detail: Any,
    ) -> Target:
        handle = "t_" + self._sign(
            owner, [owner.id, owner.context_revision, revision, kind, entity, page]
        )
        return Target(handle, kind, entity, page, label, aliases, operations, detail, binding_path)

    @staticmethod
    def _scope(owner: EditingSession, page: str | None) -> bool:
        scope = owner.context_json["pageScope"]
        return page is None or scope["mode"] == "all" or page == scope["pageId"]

    @staticmethod
    def cell_page(
        layout: LayoutDefinition, cell: LayoutCell, pages: list[dict[str, Any]]
    ) -> str | None:
        if not pages:
            return None
        if cell.virtual:
            matches = [p for p in pages if p["range"] == cell.page_range]
        else:
            merged = next((m for m in layout.cell_merges if m.parent_cell_id == cell.id), None)
            atoms = [part.cell for part in merged.parts] if merged else [cell]
            rows = [
                int(match[1])
                for atom in atoms
                if (match := re.match(r"^xlsx_[A-Z]{1,3}([1-9]\d*)(?:$|_)", atom.id))
            ]
            matches = [
                p
                for p in pages
                if len(rows) == len(atoms)
                and all(p["startRow"] <= row <= p["endRow"] for row in rows)
            ]
        if len(matches) != 1:
            raise reject("PAGE_OWNERSHIP_INVALID", "セルの保存済みページを一意に検証できません。")
        return f"{matches[0]['page']}:{matches[0]['range']}"

    def _spills(
        self, session: Session, version: Any, layout: LayoutDefinition
    ) -> dict[str, int]:
        from app.db.models import JobArtifact
        from app.editing.printed_overflow import spills

        artifact = (
            session.get(JobArtifact, version.clean_template_artifact_id)
            if version.clean_template_artifact_id
            else None
        )
        if artifact is None:
            return {}
        try:
            image = self.authoring.storage.read_bytes(artifact.relative_path)
        except Exception:  # A missing image only removes the hint.
            return {}
        return spills(image, layout.model_dump(mode="json", by_alias=True)["cells"])

    def _template(self, session: Session, owner: EditingSession) -> Catalog:
        version = self.authoring.version(session, owner.resource_id)
        snapshot = self.authoring.snapshot(session, version)
        layout: LayoutDefinition = snapshot["layout"]
        config = snapshot["config"]
        editable = version.status == "DRAFT" and version.approved_at is None
        pages = (version.quality_json.get("printLayout") or {}).get("pages", [])
        if pages:
            source = PrintLayoutService(self.authoring.database, self.authoring.storage)._source(
                session, version, layout.model_dump(mode="json", by_alias=True)
            )
            if source["unsupported"]:
                raise reject("PAGE_OWNERSHIP_INVALID", "元セルとページの対応を検証できません。")
        scope = PageScope.model_validate(owner.context_json["pageScope"])
        page_ids = {f"{p['page']}:{p['range']}" for p in pages}
        if (
            (pages and scope.mode == "current" and scope.page_id not in page_ids)
            or (not pages and scope.page_id is not None)
            or (scope.page_id is not None and scope.page_id not in page_ids)
        ):
            raise reject("PAGE_SCOPE_INVALID", "保存済みのページを指定してください。", 422)
        targets: dict[str, Target] = {}

        def add(target: Target) -> None:
            if self._scope(owner, target.page_id):
                targets[target.handle] = target

        for page in pages:
            target = self._target(
                owner,
                version.revision,
                "page",
                f"{page['page']}:{page['range']}",
                f"{page['page']}:{page['range']}",
                f"{page['page']}ページ {page['range']}",
            )
            add(target)
        cell_pages = {cell.id: self.cell_page(layout, cell, pages) for cell in layout.cells}
        spill = self._spills(session, version, layout) if editable and not pages else {}
        cells = {c.id: c for c in layout.cells}
        meanings_by_path = {entry.target_path: entry for entry in layout.label_pool}
        for cell in layout.cells:
            label = cell.field_label or cell.text or cell.printed_label_text or "名称未設定の欄"
            heading_names: list[str] = []
            parent = cell.parent_label_cell_id
            visited: set[str] = set()
            while parent and parent in cells and parent not in visited:
                visited.add(parent)
                ancestor = cells[parent]
                heading_names.insert(0, ancestor.field_label or ancestor.text)
                parent = ancestor.parent_label_cell_id
            permitted: tuple[str, ...] = ("cell.rename", "cell.set_role", "meaning.bind")
            if cell.shape_type == "rectangle" and not cell.mark_sets:
                permitted += ("cell.merge", "cell.delete")
            if cell.role == "input":
                permitted += ("cell.set_reading", "mark.configure")
            elif cell.role == "mark":
                permitted += ("mark.configure", "mark.clear")
            if cell.composite_subfields:
                # The address split derives its parts' paths from the bound meaning.
                permitted = ("cell.rename", "meaning.bind")
            if cell.virtual:
                permitted = (
                    "heading.update",
                    "heading.attach",
                    "heading.detach",
                    "heading.delete_virtual",
                )
            detail: dict[str, Any] = {
                "printedName": cell.printed_label_text or cell.text,
                "role": cell.role,
                "headingPath": heading_names,
                "position": {"x": cell.x, "y": cell.y},
                "size": {"width": cell.width, "height": cell.height},
                # Spreadsheet-derived cells keep their sheet address in the ID.
                "sheetCell": cell.id[5:] if cell.id.startswith("xlsx_") else None,
                "textSpillsRight": spill.get(cell.id),
                "unavailableReason": None if editable else "RESOURCE_NOT_EDITABLE",
                "order": next(
                    (g.order for g in layout.record_groups if cell.id in g.input_cell_ids), None
                ),
            }
            aliases: list[str] = []
            for relation in layout.relations:
                if relation.input_cell_id != cell.id:
                    continue
                if meaning := meanings_by_path.get(relation.target_path):
                    aliases.extend([meaning.label, *meaning.aliases])
                aliases.extend(
                    cells[c].field_label or cells[c].text or cells[c].printed_label_text or ""
                    for c in relation.label_cell_ids
                    if c in cells
                )
            add(
                self._target(
                    owner,
                    version.revision,
                    "cell",
                    cell.id,
                    cell_pages[cell.id],
                    label,
                    permitted if editable else (),
                    aliases=tuple(dict.fromkeys(a for a in aliases if a and a != label)),
                    **detail,
                )
            )
            if cell.role in {"parent_label", "label"}:
                add(
                    self._target(
                        owner,
                        version.revision,
                        "heading",
                        cell.id,
                        cell_pages[cell.id],
                        label,
                        (
                            ("heading.update", "heading.attach", "heading.detach")
                            + (("heading.delete_virtual",) if cell.virtual else ())
                        )
                        if editable
                        else (),
                        virtual=cell.virtual,
                    )
                )
        for entry in layout.label_pool:
            add(
                self._target(
                    owner,
                    version.revision,
                    "meaning",
                    entry.id,
                    None,
                    entry.label,
                    ("meaning.update", "meaning.delete", "meaning.bind") if editable else (),
                    tuple(entry.aliases),
                )
            )
        for relation in layout.relations:
            add(
                self._target(
                    owner,
                    version.revision,
                    "relation",
                    relation.id,
                    cell_pages[relation.input_cell_id],
                    cells[relation.input_cell_id].field_label
                    or cells[relation.input_cell_id].text
                    or "意味の接続",
                    ("meaning.unbind",) if editable else (),
                    inputCellName=cells[relation.input_cell_id].text,
                )
            )
        incomplete: list[str] = []
        from app.editing.mark_commands import node_id
        from app.editing.semantic_commands import section_cells

        raw_layout = layout.model_dump(mode="json", by_alias=True)
        for section in layout.record_sections:
            member_pages = {cell_pages[c] for c in section_cells(raw_layout, section.id)}
            if len(member_pages) > 1:
                raise reject("PAGE_OWNERSHIP_INVALID", "表の所属が複数ページにまたがっています。")
            page_id = next(iter(member_pages), None)
            add(
                self._target(
                    owner,
                    version.revision,
                    "record_section",
                    section.id,
                    page_id,
                    section.name_with_heading(cells),
                    (
                        "records.replace_rows",
                        "records.configure",
                        "records.dissolve",
                        "heading.attach",
                        "heading.detach",
                    )
                    if editable
                    else (),
                )
            )
        for group in layout.record_groups:
            page_id = cell_pages[group.input_cell_ids[0]]
            add(
                self._target(
                    owner,
                    version.revision,
                    "record_group",
                    group.id,
                    page_id,
                    f"{group.order}行目",
                    (),
                    order=group.order,
                    memberHandles=[
                        self._target(owner, version.revision, "cell", c, cell_pages[c], "").handle
                        for c in group.input_cell_ids
                    ],
                )
            )
        for cell in layout.cells:
            for mark in cell.mark_sets:
                mark_id = node_id(cell.id, mark.id)
                meaning = next(
                    (p for p in layout.label_pool if p.target_path == mark.target_path), None
                )
                add(
                    self._target(
                        owner,
                        version.revision,
                        "mark_set",
                        mark_id,
                        cell_pages[cell.id],
                        meaning.label if meaning else cell.field_label or cell.text or "選択肢群",
                        ("mark.configure",) if editable else (),
                        recognitionMode=mark.recognition_mode,
                        selectionMode=mark.selection_mode,
                    )
                )
                for option in mark.options:
                    option_id = node_id(cell.id, mark.id, option.id)
                    add(
                        self._target(
                            owner,
                            version.revision,
                            "mark_option",
                            option_id,
                            cell_pages[cell.id],
                            option.label,
                            (),
                            optionValue=option.value,
                        )
                    )
                    if mark.recognition_mode in {"automatic", "manual"}:
                        add(
                            self._target(
                                owner,
                                version.revision,
                                "region",
                                option_id,
                                cell_pages[cell.id],
                                f"{option.label}の領域",
                            )
                        )
        try:
            mappings = LayoutService._as_field_mappings(layout)
        except AppError as error:
            if error.code not in INCOMPLETE_CODES:
                raise
            mappings = []
            incomplete.append(error.code)
        mapped = {r.input_cell_id for r in layout.relations}
        mapped.update(c.id for c in layout.cells if c.role == "mark" and c.mark_sets)
        if not mappings or any(
            c.role in {"input", "mark"} and c.id not in mapped for c in layout.cells
        ):
            incomplete.append("UNASSIGNED_FIELDS")
        for mapping in mappings:
            # Only current-layout mappings are exposed, never retained config mappings.
            mapped_cell = next(
                (
                    c
                    for c in layout.cells
                    if mapping.mapping_id == f"layout_{c.id}"
                    or any(mapping.mapping_id == f"layout_{c.id}_{m.id}"[:100] for m in c.mark_sets)
                ),
                None,
            )
            if mapped_cell:
                add(
                    self._target(
                        owner,
                        version.revision,
                        "field",
                        mapping.mapping_id,
                        cell_pages[mapped_cell.id],
                        mapping.label,
                        (),
                        mappingType=mapping.mapping_type,
                        binding_path=mapping.target_path,
                    )
                )
        # Public topology uses the same finite handles as mutation requests.
        by_entity = {(t.kind, t.entity_id): t for t in targets.values()}

        def handle(kind: str, entity: str | None) -> str | None:
            target = by_entity.get((kind, entity)) if entity else None
            return target.handle if target else None

        section_by_group = {g.id: g for g in layout.record_groups}
        meaning_by_path = {p.target_path: p.id for p in layout.label_pool}
        for target in targets.values():
            if target.kind in {"cell", "heading"}:
                target.detail["parentHandle"] = handle(
                    "heading", cells[target.entity_id].parent_label_cell_id
                )
            elif target.kind == "record_group":
                group = section_by_group[target.entity_id]
                target.detail.update(
                    ownerHandle=handle("record_section", group.section_id),
                    rowKeyHandle=handle("cell", group.record_key_cell_id),
                    rowKeyValue=group.record_key_value,
                )
            elif target.kind in {"mark_set", "mark_option", "region"}:
                parts = json.loads(target.entity_id)
                target.detail["ownerHandle"] = (
                    handle("cell", parts[0])
                    if target.kind == "mark_set"
                    else handle("mark_set", node_id(*parts[:2]))
                )
                if target.kind == "mark_set":
                    mark = next(m for m in cells[parts[0]].mark_sets if m.id == parts[1])
                    target.detail["meaningHandle"] = handle(
                        "meaning", meaning_by_path.get(mark.target_path)
                    )
            elif target.kind == "relation":
                relation = next(r for r in layout.relations if r.id == target.entity_id)
                target.detail.update(
                    ownerHandle=handle("cell", relation.input_cell_id),
                    meaningHandle=handle("meaning", meaning_by_path.get(relation.target_path)),
                    memberHandles=[h for c in relation.label_cell_ids if (h := handle("cell", c))],
                )
            elif target.kind == "field":
                target.detail["meaningHandle"] = handle(
                    "meaning", meaning_by_path.get(target.binding_path or "")
                )

        for entry in config.document_identity_fields:
            if entry.match_group_id:
                add(
                    self._target(
                        owner,
                        version.revision,
                        "identity_group",
                        entry.match_group_id,
                        None,
                        entry.match_group_label or "識別グループ",
                        ("identity.configure",) if editable else (),
                    )
                )
        state = {
            "kind": "template",
            "layout": layout.model_dump(mode="json", by_alias=True),
            "documentIdentityFields": [
                f.model_dump(mode="json", by_alias=True) for f in config.document_identity_fields
            ],
            "documentName": config.document_name.model_dump(mode="json", by_alias=True)
            if config.document_name
            else None,
        }
        generation = self._sign(
            owner, [owner.context_revision, version.revision, owner.context_json["pageScope"]]
        )
        return Catalog(
            version.revision,
            generation,
            editable,
            {"state": "incomplete" if incomplete else "complete", "reasons": incomplete},
            targets,
            state,
            TEMPLATE_OPERATIONS if editable else (),
            pages,
        )

    def _ocr(self, session: Session, owner: EditingSession) -> Catalog:
        if PageScope.model_validate(owner.context_json["pageScope"]).page_id is not None:
            raise reject("PAGE_SCOPE_INVALID", "単一OCR結果にページを指定できません。", 422)
        job, stored = self.ocr._load(session, owner.resource_id)
        document = OcrDocument.model_validate(stored.current_corrected_json)
        response = self.ocr._response(session, job, stored)
        editable = job.status == "REVIEW_REQUIRED"
        targets = {}
        review = {
            f["targetPath"]: f
            for f in job.config_snapshot_json["reviewStructure"].get("fields", [])
        }
        structure = job.config_snapshot_json["reviewStructure"]
        cells = record_cells(structure)
        if len(review) != len(structure.get("fields", [])):
            raise reject("FIELD_BINDING_CHANGED", "レビュー欄の保存先が重複しています。")
        if len({f["id"] for f in review.values()}) != len(review):
            raise reject("FIELD_BINDING_CHANGED", "レビュー欄の識別子が重複しています。")
        for key, reading in document.fields.items():
            path = f"fields.{key}"
            owned = [
                p for p in response["fieldPreviews"] if p["mappingId"] == reading.source_region_id
            ]
            matches = [p for p in owned if p["targetPath"] == path]
            claimed = any(p["targetPath"] == path for p in response["fieldPreviews"])
            if (owned or claimed) and (len(owned) != 1 or len(matches) != 1):
                raise reject("FIELD_BINDING_CHANGED", "受付時の欄と切り出し対応が一致しません。")
            preview = matches[0] if matches else None
            valid = preview is not None and preview["mappingType"] == "text"
            label = review.get(path, {}).get("label") or document.field_labels.get(key, key)
            field_id = review.get(path, {}).get("id")
            headings = [
                section["title"]
                for section in structure.get("parentSections", [])
                if field_id in section.get("fieldIds", [])
            ]
            cell = cells.get(field_id)
            if cell:
                headings.append(cell["tableName"])
            bbox = preview.get("boundingBox") if preview else None
            target = self._target(
                owner,
                stored.revision,
                "field",
                key,
                None,
                label,
                ("ocr.set_value",) if valid and editable else (),
                currentValue=reading.normalized if reading.normalized is not None else reading.raw,
                mappingType=preview["mappingType"] if preview is not None else None,
                position={"x": bbox["x"], "y": bbox["y"]} if bbox else None,
                headingPath=headings,
                order=cell["rowNumber"] if cell else None,
                recordCell=cell,
                unavailableReason=(
                    "FIELD_BINDING_UNAVAILABLE"
                    if preview is None
                    else None
                    if valid and editable
                    else "CAPABILITY_UNAVAILABLE"
                ),
            )
            targets[target.handle] = target
        state = {
            "kind": "ocr",
            "fields": {
                k: v.model_dump(mode="json", by_alias=True) for k, v in document.fields.items()
            },
            "reviewItems": [
                item.model_dump(mode="json", by_alias=True) for item in document.review_items
            ],
        }
        generation = self._sign(owner, [owner.context_revision, stored.revision])
        return Catalog(
            stored.revision,
            generation,
            editable,
            {"state": "complete", "reasons": []},
            targets,
            state,
            ("ocr.set_value",) if any(t.operations for t in targets.values()) else (),
        )

    def find(self, owner: EditingSession, catalog: Catalog, body: TargetSearch) -> dict[str, Any]:
        if (
            body.page_scope is not None
            and body.page_scope.model_dump(by_alias=True) != owner.context_json["pageScope"]
        ):
            raise reject(
                "PAGE_SCOPE_INVALID", "検索範囲を変更するには編集contextを更新してください。"
            )
        query = unicodedata.normalize("NFKC", body.query).casefold().strip()
        candidates = []
        for target in catalog.targets.values():
            if body.kinds and target.kind not in body.kinds:
                continue
            if body.role and target.detail.get("role") != body.role:
                continue
            names = [
                unicodedata.normalize("NFKC", n).casefold()
                for n in (
                    target.label, *target.aliases, target.detail.get("printedName") or "",
                    (target.detail.get("recordCell") or {}).get("columnName", ""),
                )
            ]
            if body.heading and body.heading not in target.detail.get("headingPath", []):
                continue
            if not matches_location(target.detail, body):
                continue
            if not query or any(query in name for name in names):
                score = 100 if query in names else 10 if query else 0
                candidates.append((score, target))
        if body.position is not None:
            axis = "y" if body.position in {"top", "bottom"} else "x"
            candidates = [item for item in candidates if item[1].detail.get("position")]
            if candidates:
                extreme = (min if body.position in {"top", "left"} else max)(
                    item[1].detail["position"][axis] for item in candidates
                )
                candidates = [
                    item for item in candidates if item[1].detail["position"][axis] == extreme
                ]
        candidates.sort(
            key=lambda item: (-item[0], item[1].page_id or "", item[1].label, item[1].handle)
        )
        binding = [
            catalog.generation,
            body.query,
            body.kinds,
            owner.context_json["pageScope"],
            body.position,
            body.heading,
            body.row,
            body.column,
            body.table_id,
            body.table_name,
            body.role,
        ]
        offset = 0
        if body.cursor:
            prefix, separator, signature = body.cursor.partition(".")
            if (
                not separator
                or not prefix.isdigit()
                or not hmac.compare_digest(signature, self._sign(owner, [binding, prefix]))
            ):
                raise reject("TARGET_STALE", "検索cursorの保存世代が変わりました。")
            offset = int(prefix)
        selected = candidates[offset : offset + 50]
        next_offset = offset + 50
        cursor = (
            f"{next_offset}.{self._sign(owner, [binding, str(next_offset)])}"
            if next_offset < len(candidates)
            else None
        )
        return {
            "catalogRevision": catalog.generation,
            "resourceRevision": catalog.revision,
            "targets": [{**target.public(), "score": score} for score, target in selected],
            "nextCursor": cursor,
            "total": len(candidates),
        }
