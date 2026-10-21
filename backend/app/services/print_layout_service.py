from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from typing import Any
from uuid import uuid4

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.db.session import Database
from app.domain.calc_print_grid import MODEL as CALC_GEOMETRY_MODEL
from app.domain.print_layout import PrintLayoutRequest, page_plan, print_error
from app.domain.template_config import parse_template_config
from app.rendering import geometry as linux_geometry
from app.services.calc_workbook_geometry import apply_dimensions as apply_calc_dimensions
from app.services.draft_page_geometry import draft_geometry
from app.services.page_authoring_validation import (
    bounds,
    is_page_heading,
    physical_cells,
    validate_page_headings,
)
from app.services.print_layout_renderer import render_page
from app.services.print_layout_source import DRAFT_GEOMETRY_VERSION, read_source
from app.services.spreadsheet_template_import_service import SpreadsheetTemplateImportService
from app.services.template_mutation import begin_immediate, require_draft, template_mutation_lock
from app.services.workbook_geometry import (
    GEOMETRY_MODEL_VERSION,
    LEGACY_GEOMETRY_VERSION,
    apply_geometry,
    normal_font,
    office_geometry,
)
from app.services.wsl_workbook_geometry import MODEL as WSL_GEOMETRY_MODEL
from app.services.wsl_workbook_geometry import apply_dimensions
from app.storage.local_storage import LocalStorageService


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PrintLayoutService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self.database, self.storage = database, storage

    @staticmethod
    def _version(session: Session, version_id: str) -> TemplateVersion:
        version = session.get(TemplateVersion, version_id)
        if version is None:
            raise print_error("テンプレートがありません。", "TEMPLATE_NOT_FOUND", 404)
        if version.mode != "SPREADSHEET_TEMPLATE":
            raise print_error("XLSXテンプレートだけが対象です。", "PRINT_LAYOUT_UNSUPPORTED")
        return version

    @staticmethod
    def revision(version: TemplateVersion) -> str:
        return digest(
            {
                "version": version.id,
                "layout": version.layout_json,
                "printLayout": version.quality_json.get("printLayout"),
                "inputRevision": version.quality_json.get("layoutInputRevision", 0),
                "geometryImplementationVersion": GEOMETRY_MODEL_VERSION,
            }
        )

    def _source(self, session: Session, version: TemplateVersion,
                proposed: dict[str, Any] | None = None) -> dict[str, Any]:
        layout = version.layout_json if proposed is None else proposed
        artifact = session.scalars(
            select(JobArtifact)
            .where(
                JobArtifact.job_id == version.source_job_id,
                JobArtifact.artifact_name == "sourceWorkbook",
            )
            .order_by(JobArtifact.created_at.desc())
        ).first()
        if artifact is None:
            raise print_error("元XLSXがありません。", "PRINT_LAYOUT_SOURCE_MISSING", 409)
        content = self.storage.read_bytes(artifact.relative_path)
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise print_error(
                "元XLSXのハッシュが一致しません。", "PRINT_LAYOUT_SOURCE_CHANGED", 409
            )
        saved = version.quality_json.get("printLayout")
        draft_support = not saved or saved.get("geometryModelVersion") in {
            DRAFT_GEOMETRY_VERSION, WSL_GEOMETRY_MODEL, CALC_GEOMETRY_MODEL}
        source = read_source(
            content, str(version.quality_json.get("sourceSheetName", "")), layout,
            draft_support=draft_support,
        )
        # An address embedded in a rectangle ID is not proof that a user-edited
        # rectangle still occupies the whole source cell. Verify against the
        # legacy source-derived geometry; never invert a guessed affine transform.
        clean = session.get(JobArtifact, version.clean_template_artifact_id)
        if clean is None:
            raise print_error("基準画像がありません。", "PRINT_LAYOUT_SOURCE_MISSING", 409)
        encoded = np.frombuffer(self.storage.read_bytes(clean.relative_path), dtype=np.uint8)
        preview = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if preview is None:
            raise print_error("基準画像を読み込めません。")
        original = SpreadsheetTemplateImportService(self.database, self.storage).restore_layout(
            content,
            preview_image=preview,
            preview_geometry=version.quality_json.get("spreadsheetPreviewGeometry"),
        )
        originals = {c.id: c for c in original.cells}
        for physical_cell in physical_cells(layout):
            match = re.match(r"^(xlsx_[A-Z]{1,3}[1-9]\d*)(?:$|_)", physical_cell["id"])
            baseline = originals.get(match[1]) if match else None
            if baseline is None or any(
                physical_cell[k] != getattr(baseline, k) for k in ("x", "y", "width", "height")
            ):
                source["unsupported"].append(
                    "位置・サイズを手動変更した矩形の元セル対応は未対応です。"
                )
                break
        parents = {c["id"]: c for c in layout["cells"]}
        for merge in layout.get("cellMerges", []):
            parent = parents.get(merge["parentCellId"])
            rectangle = bounds([p["cell"] for p in merge["parts"]])
            if parent is None or any(parent[k] != v for k, v in rectangle.items()):
                source["unsupported"].append("結合矩形と元セル範囲が一致しません。")
        source["unsupported"] = list(dict.fromkeys(source["unsupported"]))
        config = parse_template_config(
            self.storage.read_bytes(version.config_relative_path).decode("utf-8")
        )
        cells = {c["id"]: c for c in layout.get("cells", [])}
        owners = {}
        for field in source["fields"]:
            cell_id = field["cellId"]
            field["bindings"] = []
            owners[f"layout_{cell_id}"] = field
            for mark in cells[cell_id].get("markSets", []):
                owners[f"layout_{cell_id}_{mark['id']}"[:100]] = field
        for mapping in config.field_mappings:
            if proposed is not None:
                break  # Proposed semantics are saved by LayoutService after preflight.
            if not mapping.enabled:
                continue
            owner = owners.get(mapping.mapping_id)
            cell = cells.get(owner["cellId"]) if owner else None
            if (
                owner is None
                or cell is None
                or any(getattr(mapping, k) != cell[k] for k in ("x", "y", "width", "height"))
            ):
                source["unsupported"].append("OCR mappingと元セルの一意な対応を確認できません。")
                break
            owner["bindings"].append(
                {
                    "mappingId": mapping.mapping_id,
                    "sourceRegionId": mapping.mapping_id,
                    "targetPath": mapping.target_path,
                    "mappingType": mapping.mapping_type,
                }
            )
        source["geometryModelVersion"] = LEGACY_GEOMETRY_VERSION
        if saved and saved.get("geometryModelVersion") == CALC_GEOMETRY_MODEL:
            return apply_calc_dimensions(source, saved["geometryEvidence"])
        if saved and saved.get("geometryModelVersion") == WSL_GEOMETRY_MODEL:
            return apply_dimensions(source, saved["geometryEvidence"])
        if saved and saved.get("geometryModelVersion") == linux_geometry.MODEL:
            return linux_geometry.apply_dimensions(source, saved["geometryEvidence"])
        if not saved and os.environ.get("PRINT_PDF_RENDERER") == "linux":
            return draft_geometry(source, version.quality_json.get("spreadsheetPreviewGeometry"))
        if draft_support and (
            source["draftFeatures"]
            or (saved and saved.get("geometryModelVersion") == DRAFT_GEOMETRY_VERSION)
        ):
            evidence = (saved.get("geometryEvidence") if saved else
                        version.quality_json.get("spreadsheetPreviewGeometry"))
            return draft_geometry(source, evidence)
        # Historical plans keep their original geometry even when edited. A
        # newly created printLayout may use the calibrated Office import model.
        # Accepted jobs and the legacy template layout are never recalculated.
        if saved and saved.get("geometryModelVersion") != GEOMETRY_MODEL_VERSION:
            return source
        if source["unsupported"]:
            return source
        font = normal_font(content)
        if not font["installed"]:
            source["unsupported"].append(
                "Normal fontの実体を確認できません。代替fontは許可しません。")
            return source
        dimensions = (saved or {}).get("geometryEvidence")
        if dimensions:
            if (dimensions.get("sourceSha256") != source["sha256"]
                    or dimensions.get("normalFont") != font):
                raise print_error("保存したgeometryの原本・fontが変わりました。",
                                  "PRINT_GEOMETRY_CHANGED", 409)
        else:
            dimensions = office_geometry(content, source)
        return apply_geometry(source, dimensions)

    def get(self, version_id: str) -> dict[str, Any]:
        with self.database.session() as session:
            version = self._version(session, version_id)
            return {
                "revision": self.revision(version),
                "resourceRevision": version.revision,
                "source": self._source(session, version),
                "saved": copy.deepcopy(version.quality_json.get("printLayout")),
                "editable": version.status == "DRAFT" and version.approved_at is None,
                "pageHeadings": [{"cellId": c["id"], "text": c.get("text", ""),
                                  "pageRange": c["pageRange"]}
                                 for c in version.layout_json["cells"] if is_page_heading(c)],
            }

    def preview(self, version_id: str, request: PrintLayoutRequest) -> dict[str, Any]:
        with self.database.session() as session:
            version = self._version(session, version_id)
            self._check_revision(version, request.base_revision)
            return self._plan(session, version, request)

    def _plan(
        self, session: Session, version: TemplateVersion, request: PrintLayoutRequest
    ) -> dict[str, Any]:
        plan = page_plan(
            self._source(session, version), request, version.quality_json.get("printLayout")
        )
        layout = self._heading_layout(version, request, plan)
        plan["layoutSha256"] = digest(layout)
        return plan

    @staticmethod
    def _heading_layout(version: TemplateVersion, request: PrintLayoutRequest,
                        plan: dict[str, Any]) -> dict[str, Any]:
        # Explicit identity/range bindings only. Never infer a destination by
        # page index, coordinates, label text or physical field membership.
        layout = copy.deepcopy(version.layout_json)
        cells = {c["id"]: c for c in layout["cells"]}
        seen: set[str] = set()
        for move in request.page_heading_moves:
            cell = cells.get(move.cell_id)
            if (move.cell_id in seen or cell is None or not is_page_heading(cell)
                    or cell["pageRange"] != move.from_range):
                raise print_error("仮想見出しのID・変更前ページが一致しません。",
                                  "PRINT_HEADING_MOVE_INVALID", 409)
            seen.add(move.cell_id)
            cell["pageRange"] = move.to_range
        validate_page_headings(layout, plan)
        return layout

    @staticmethod
    def _check_revision(version: TemplateVersion, expected: str) -> None:
        if PrintLayoutService.revision(version) != expected:
            raise print_error(
                "編集中に設定が変更されました。編集内容を控えて再読み込みしてください。",
                "PRINT_LAYOUT_CONFLICT",
                409,
            )

    def save(self, version_id: str, request: PrintLayoutRequest) -> dict[str, Any]:
        # Rendering happens before the DB lock. Compare again at commit so a
        # concurrent layout edit cannot publish stale rectangles/images.
        with self.database.session() as session:
            version = self._version(session, version_id)
            require_draft(version)
            self._check_revision(version, request.base_revision)
            plan = self._plan(session, version, request)
        images = [render_page(page) for page in plan["pages"]]
        with template_mutation_lock(), self.database.session() as session:
            begin_immediate(session)
            version = self._version(session, version_id)
            require_draft(version)
            self._check_revision(version, request.base_revision)
            layout = self._heading_layout(version, request, plan)
            job = session.get(ProcessingJob, version.source_job_id)
            if job is None:
                raise print_error("準備ジョブがありません。", status=409)
            generation = uuid4().hex
            for page, content in zip(plan["pages"], images, strict=True):
                name = f"print-reference-{generation}-{page['page']}"
                path = self.storage.write_bytes(
                    f"artifacts/{job.id}/attempts/{job.attempt_number}/{name}.png",
                    content,
                )
                artifact = JobArtifact(
                    id=str(uuid4()),
                    job_id=job.id,
                    attempt_number=job.attempt_number,
                    artifact_name=name,
                    relative_path=path,
                    media_type="image/png",
                    sha256=hashlib.sha256(content).hexdigest(),
                    width=page["width"],
                    height=page["height"],
                )
                session.add(artifact)
                page["referenceImage"] = {
                    "artifactId": artifact.id,
                    "relativePath": path,
                    "sha256": artifact.sha256,
                    "name": name,
                }
            plan["source"] = "user"
            if request.page_heading_moves:
                version.layout_json = layout
            version.quality_json = {**version.quality_json, "printLayout": plan}
            # PREPARE_TEMPLATE is a mutable draft, unlike accepted OCR snapshots.
            job.config_snapshot_json = {
                **job.config_snapshot_json,
                "printLayout": copy.deepcopy(plan),
            }
            session.commit()
        result = self.get(version_id)
        if request.page_heading_moves:
            result["layout"] = layout
        return result

    def clear(self, version_id: str, expected: str) -> dict[str, Any]:
        with template_mutation_lock(), self.database.session() as session:
            begin_immediate(session)
            version = self._version(session, version_id)
            require_draft(version)
            self._check_revision(version, expected)
            validate_page_headings(version.layout_json, None)
            quality = dict(version.quality_json)
            quality.pop("printLayout", None)
            version.quality_json = quality
            job = session.get(ProcessingJob, version.source_job_id)
            if job is not None:
                snapshot = dict(job.config_snapshot_json)
                snapshot.pop("printLayout", None)
                job.config_snapshot_json = snapshot
            session.commit()
        return self.get(version_id)

    def image(self, version_id: str, page_number: int) -> bytes:
        with self.database.session() as session:
            version = self._version(session, version_id)
            plan = version.quality_json.get("printLayout")
            if not plan or not 1 <= page_number <= len(plan["pages"]):
                raise print_error("保存済みのページがありません。", "PRINT_PAGE_NOT_FOUND", 404)
            if plan["layoutSha256"] != digest(version.layout_json):
                raise print_error(
                    "矩形の編集後は印刷ページ設定を再計算・保存してください。",
                    "PRINT_LAYOUT_STALE",
                    409,
                )
            reference = plan["pages"][page_number - 1]["referenceImage"]
            content = self.storage.read_bytes(reference["relativePath"])
            if hashlib.sha256(content).hexdigest() != reference["sha256"]:
                raise print_error(
                    "ページ画像のハッシュが一致しません。", "PRINT_REFERENCE_CHANGED", 409
                )
            return content
