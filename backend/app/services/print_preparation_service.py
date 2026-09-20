"""Explicit, revision-bound print preparation; never part of normal auto-save."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import JobArtifact, ProcessingJob
from app.domain.calc_print_grid import MODEL as CALC_GEOMETRY_MODEL
from app.domain.print_layout import PAPER_MM, PrintLayoutRequest, page_plan, print_error
from app.services import calc_workbook_geometry
from app.services.office_reference_renderer import _RENDER_LOCK
from app.services.page_authoring_validation import validate_page_headings
from app.services.prepared_print_contract import render_key
from app.services.preserving_print_workbook import inspect, workbook_copy
from app.services.print_layout_service import PrintLayoutService, digest
from app.services.renderer_process import renderer_mutex
from app.services.renderer_wsl import import_wsl_geometry, render_wsl_reference
from app.services.template_mutation import begin_immediate, require_draft, template_mutation_lock
from app.services.wsl_workbook_geometry import apply_dimensions, dimensions


class PrintPreparationService(PrintLayoutService):
    def prepare(self, version_id: str, expected: str, distribution: str,
                *, runtime_id: str | None = None,
                geometry_model: str | None = None) -> dict[str, Any]:
        if geometry_model not in {None, CALC_GEOMETRY_MODEL}:
            raise print_error("指定された座標モデルは未対応です。", "PRINT_GEOMETRY_CHANGED", 409)
        if geometry_model == CALC_GEOMETRY_MODEL and runtime_id is None:
            raise print_error("Calc座標の確定には固定変換環境が必要です。",
                              "RENDERER_UNAVAILABLE", 409)
        if runtime_id is not None and re.fullmatch(r"[0-9a-f]{64}", runtime_id) is None:
            raise print_error("固定変換環境の識別子が不正です。", "RENDERER_UNAVAILABLE", 409)
        if not _RENDER_LOCK.acquire(blocking=False):
            raise print_error("別の印刷用ファイルを生成中です。", "RENDERER_BUSY", 409)
        try:
            with renderer_mutex():
                return self._prepare(version_id, expected, distribution, runtime_id=runtime_id,
                                     geometry_model=geometry_model)
        except AppError:
            raise
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            raise print_error(
                "隔離環境で印刷用ファイルを確定できませんでした。設定は保持しています。",
                "PRINT_PREPARATION_FAILED",
                409,
            ) from exc
        finally:
            _RENDER_LOCK.release()

    def _prepare(self, version_id: str, expected: str, distribution: str,
                 *, runtime_id: str | None = None,
                 geometry_model: str | None = None) -> dict[str, Any]:
        with self.database.session() as session:
            version = self._version(session, version_id)
            require_draft(version)
            self._check_revision(version, expected)
            saved = copy.deepcopy(version.quality_json.get("printLayout"))
            if not saved or saved.get("layoutSha256") != digest(version.layout_json):
                raise print_error(
                    "先に編集中の内容と印刷ページ設定を保存してください。",
                    "PRINT_LAYOUT_STALE",
                    409,
                )
            source = self._source(session, version)
            if source["unsupported"]:
                raise print_error(" / ".join(source["unsupported"]), "PRINT_LAYOUT_UNSUPPORTED")
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
            source_path, source_sha = artifact.relative_path, artifact.sha256
            content = self.storage.read_bytes(source_path)
            if hashlib.sha256(content).hexdigest() != source_sha:
                raise print_error("元XLSXが変更されました。", "PRINT_LAYOUT_SOURCE_CHANGED", 409)
            layout = copy.deepcopy(version.layout_json)
            config_sha = hashlib.sha256(
                self.storage.read_bytes(version.config_relative_path)
            ).hexdigest()

        calc_grid = geometry_model == CALC_GEOMETRY_MODEL or (
            geometry_model is None and saved.get("geometryModelVersion") == CALC_GEOMETRY_MODEL)
        if calc_grid and runtime_id is None:
            raise print_error("Calc座標の確定には固定変換環境が必要です。",
                              "RENDERER_UNAVAILABLE", 409)
        # No database mutation lock is held while Office imports, converts or rasterizes.
        inspect(content)
        observation_args = ({"dimension_request": {
            "sheetName": saved["sheetName"], "rows": source["endRow"],
            "columns": source["endCol"]}} if calc_grid else {})
        fods, measured_report = import_wsl_geometry(
            content, distribution=distribution, font_source="windows", runtime_id=runtime_id,
            **observation_args,
        )
        # A model change is requested only by this explicit preparation command.
        # Ordinary GET/save/reopen never remeasure or upgrade historical geometry.
        measure = calc_workbook_geometry.dimensions if calc_grid else dimensions
        project = calc_workbook_geometry.apply_dimensions if calc_grid else apply_dimensions
        evidence = measure(fods, saved["sheetName"], source["endRow"], source["endCol"])
        if calc_grid:
            calc_workbook_geometry.verify_dimension_observation(
                evidence, measured_report, saved["sheetName"], source_sha)
        fonts = measured_report["resolvedFonts"]
        if not fonts or any(
            font["requestedFamily"].casefold()
            not in {name.casefold() for name in font["resolvedFamily"].split(",")}
            for font in fonts
        ):
            raise print_error(
                "指定されたフォントの実体を確認できません。", "PRINT_FONT_UNRESOLVED", 409
            )
        evidence.update(sourceSha256=source_sha, fontSource="windows", importReport=measured_report)
        measured = project(source, evidence)
        request = PrintLayoutRequest.model_validate(
            {
                **{
                    key: saved[key]
                    for key in (
                        "paperSize",
                        "orientation",
                        "margins",
                        "horizontalCentering",
                        "verticalCentering",
                    )
                },
                "baseRevision": expected,
                "breakAfterRows": [p["endRow"] for p in saved["pages"][:-1]],
            }
        )
        plan = page_plan(measured, request, saved)
        validate_page_headings(layout, plan)
        plan["layoutSha256"] = digest(layout)
        printable = workbook_copy(content, plan)
        pdf, images, report = render_wsl_reference(
            printable, distribution=distribution, runtime_id=runtime_id,
        )
        if report["resolvedFonts"] != fonts:
            raise print_error(
                "生成中にフォント条件が変更されました。", "PRINT_GEOMETRY_CHANGED", 409
            )
        if runtime_id is not None:
            measured_runtime = measured_report.get("runtimeImage")
            measured_fonts = measured_report.get("fontDirectoryIdentity")
            measured_worker = measured_report.get("workerIdentity")
            if (not isinstance(measured_runtime, dict)
                    or measured_runtime.get("sha256") != runtime_id
                    or not isinstance(measured_fonts, dict)
                    or not isinstance(measured_worker, dict)
                    or report.get("runtimeImage") != measured_runtime
                    or report.get("fontDirectoryIdentity") != measured_fonts
                    or report.get("workerIdentity") != measured_worker
                    or any(report.get(key) != measured_report.get(key) for key in (
                        "coreDumpGuard", "brokerCoreDumpGuard", "bootstrapPythonIdentity"))):
                raise print_error(
                    "寸法測定とPDF生成で実行環境またはフォント条件が変更されました。",
                    "PRINT_GEOMETRY_CHANGED", 409,
                )
        paper = PAPER_MM[plan["paperSize"]]
        if plan["orientation"] == "landscape":
            paper = paper[::-1]
        if len(images) != len(plan["pages"]) or report["pageCount"] != len(plan["pages"]):
            raise print_error("PDFのページ数が設定と一致しません。", "PAGE_COUNT_MISMATCH")
        if any(
            len(size) != 2
            or any(
                not math.isclose(a, b * 72 / 25.4, abs_tol=0.1)
                for a, b in zip(size, paper, strict=True)
            )
            for size in report["sizes"]
        ):
            raise print_error("PDFの用紙寸法が設定と一致しません。", "PAGE_GEOMETRY_MISMATCH")
        if report["imageSizes"] != [[p["width"], p["height"]] for p in plan["pages"]]:
            raise print_error("基準画像の寸法が設定と一致しません。", "PAGE_GEOMETRY_MISMATCH")

        with template_mutation_lock(), self.database.session() as session:
            begin_immediate(session)
            version = self._version(session, version_id)
            require_draft(version)
            self._check_revision(version, expected)
            if (
                hashlib.sha256(self.storage.read_bytes(source_path)).hexdigest() != source_sha
                or hashlib.sha256(self.storage.read_bytes(version.config_relative_path)).hexdigest()
                != config_sha
            ):
                raise print_error(
                    "生成中に原本または意味づけが変更されました。", "PRINT_LAYOUT_CONFLICT", 409
                )
            job = session.get(ProcessingJob, version.source_job_id)
            if job is None:
                raise print_error("準備ジョブがありません。", "PRINT_LAYOUT_SOURCE_MISSING", 409)
            generation = uuid4().hex

            def store(
                name: str, data: bytes, media_type: str, size: list[int] | None = None
            ) -> dict[str, Any]:
                path = self.storage.write_bytes(
                    f"artifacts/{job.id}/print/{generation}/{name}", data
                )
                item = JobArtifact(
                    id=str(uuid4()),
                    job_id=job.id,
                    attempt_number=job.attempt_number,
                    artifact_name=f"prepared-{generation}-{name}",
                    relative_path=path,
                    sha256=hashlib.sha256(data).hexdigest(),
                    media_type=media_type,
                    width=size[0] if size else None,
                    height=size[1] if size else None,
                )
                session.add(item)
                return {
                    "artifactId": item.id,
                    "relativePath": path,
                    "sha256": item.sha256,
                    "name": item.artifact_name,
                }

            for page, image, size in zip(plan["pages"], images, report["imageSizes"], strict=True):
                page["referenceImage"] = store(f"page-{page['page']}.png", image, "image/png", size)
            plan["printFiles"] = {
                "renderKey": render_key(plan),
                "xlsx": store(
                    "print.xlsx",
                    printable,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
                "pdf": store("print.pdf", pdf, "application/pdf"),
                "report": store(
                    "renderer.json", json.dumps(report, sort_keys=True).encode(), "application/json"
                ),
            }
            plan["rendererVersion"] = "wsl-print-reference-v1"
            plan["source"] = "user"
            quality = {**version.quality_json, "printLayout": plan}
            quality.pop("referenceQualification", None)
            version.quality_json = quality
            job.config_snapshot_json = {
                **job.config_snapshot_json,
                "printLayout": copy.deepcopy(plan),
            }
            session.commit()
        return self.get(version_id)
