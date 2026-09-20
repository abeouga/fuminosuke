"""Read-only, revision-bound printable artifacts for a saved TemplateVersion."""

from __future__ import annotations

import copy
import hashlib
from typing import Literal

from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import JobArtifact
from app.domain.print_layout import print_error
from app.services.prepared_print_contract import render_key
from app.services.print_layout_service import PrintLayoutService, digest
from app.services.print_pdf import render_print_pdf, render_wsl_print_pdf
from app.services.print_workbook import workbook_copy


class TemplatePrintService(PrintLayoutService):
    def export(self, version_id: str, format: Literal["xlsx", "pdf"], expected: str, *,
               pdf_renderer: Literal["appcontainer", "wsl"] = "appcontainer",
               wsl_distribution: str = "Ubuntu-22.04") -> bytes:
        try:
            with self.database.session() as session:
                version = self._version(session, version_id)
                self._check_revision(version, expected)
                plan = copy.deepcopy(version.quality_json.get("printLayout"))
                if not plan:
                    raise print_error(
                        "印刷ページ設定を保存してから取得してください。",
                        "PRINT_LAYOUT_NOT_SAVED",
                        409,
                    )
                if plan.get("layoutSha256") != digest(version.layout_json):
                    raise print_error(
                        "矩形の編集後は印刷ページ設定を保存し直してください。",
                        "PRINT_LAYOUT_STALE",
                        409,
                    )
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
                path, source_sha = artifact.relative_path, artifact.sha256
                content = self.storage.read_bytes(path)
                if hashlib.sha256(content).hexdigest() != source_sha or source_sha != plan.get(
                    "sourceSha256"
                ):
                    raise print_error(
                        "元XLSXのハッシュが一致しません。", "PRINT_LAYOUT_SOURCE_CHANGED", 409
                    )
                prepared = plan.get("printFiles")
                if not prepared and plan.get("printScalePolicy") == "integer-percent-v1":
                    raise print_error("変更後の印刷用ファイルを確定してから取得してください。",
                                      "PRINT_FILES_NOT_PREPARED", 409)
                cached = None
                if prepared:
                    if prepared.get("renderKey") != render_key(plan):
                        raise print_error(
                            "印刷条件を保存し直してください。", "PRINT_LAYOUT_STALE", 409)
                    ref = prepared[format]
                    stored = session.get(JobArtifact, ref["artifactId"])
                    if (stored is None or stored.job_id != version.source_job_id
                            or stored.sha256 != ref["sha256"]
                            or stored.relative_path != ref["relativePath"]):
                        raise print_error("印刷用ファイルの対応を確認できません。",
                                          "PRINT_FILE_CHANGED", 409)
                    cached = self.storage.read_bytes(stored.relative_path)
                    if hashlib.sha256(cached).hexdigest() != stored.sha256:
                        raise print_error(
                            "印刷用ファイルが変更されています。", "PRINT_FILE_CHANGED", 409)
            if cached is not None:
                result = cached
            elif format == "pdf":
                workbook = workbook_copy(content, plan)
                result = (render_wsl_print_pdf(workbook, plan, wsl_distribution)
                          if pdf_renderer == "wsl" else render_print_pdf(workbook, plan))
            else:
                result = workbook_copy(content, plan)
            # Long-running conversion must not return a stale draft or changed source.
            with self.database.session() as session:
                self._check_revision(self._version(session, version_id), expected)
                if hashlib.sha256(self.storage.read_bytes(path)).hexdigest() != source_sha:
                    raise print_error(
                        "生成中に元XLSXが変更されました。", "PRINT_LAYOUT_SOURCE_CHANGED", 409
                    )
            return result
        except FileNotFoundError as exc:
            raise print_error(
                "元XLSXが見つかりません。", "PRINT_LAYOUT_SOURCE_MISSING", 409
            ) from exc
        except AppError as exc:
            if format == "pdf" and exc.code in {
                "RENDER_FAILED",
                "RENDER_TIMEOUT",
                "RENDERER_UNAVAILABLE",
            }:
                raise print_error(
                    "隔離環境でPDFを生成できませんでした。renderer環境は未認定です。", exc.code, 409
                ) from exc
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            raise print_error(
                "印刷用ファイルを生成できませんでした。", "PRINT_EXPORT_FAILED", 409
            ) from exc
