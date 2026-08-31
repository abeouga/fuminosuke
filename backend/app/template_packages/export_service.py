from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from app.db.session import Database
from app.storage.local_storage import LocalStorageService
from app.template_packages.asset_collector import TemplateAssetCollector
from app.template_packages.canonical_json import canonical_json_bytes
from app.template_packages.canonical_mapper import CanonicalTemplateMapper
from app.template_packages.canonical_models import CanonicalAssetReferences
from app.template_packages.export_models import (
    ExportAssetInspection,
    ExportCandidateSetInspection,
    TemplateExportInspection,
    TemplateExportRequest,
    TemplatePackageExport,
    TemplatePackageExportError,
)
from app.template_packages.package_serializer import BuiltTemplatePackage, TemplatePackageSerializer


class TemplateExportService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage
        self._mapper = CanonicalTemplateMapper(storage)
        self._collector = TemplateAssetCollector(storage)
        self._serializer = TemplatePackageSerializer()

    def inspect(self, request: TemplateExportRequest) -> TemplateExportInspection:
        prepared, template_name, version_number, mode, candidates, assets, warnings = self._prepare(
            request, exported_at=datetime(2000, 1, 1, tzinfo=UTC)
        )
        return self._inspection(
            request=request,
            built=prepared,
            template_name=template_name,
            version_number=version_number,
            mode=mode,
            candidates=candidates,
            assets=assets,
            warnings=warnings,
        )

    def export(
        self,
        request: TemplateExportRequest,
        *,
        confirmed_inspection_digest: str,
        write_to_storage: bool = False,
    ) -> TemplatePackageExport:
        inspection = self.inspect(request)
        if inspection.confirmation_digest != confirmed_inspection_digest:
            raise TemplatePackageExportError(
                "TEMPLATE_EXPORT_CONFIRMATION_MISMATCH",
                "確認後に書き出し対象が変更されたため停止しました。内容を再確認してください。",
            )
        built, template_name, version_number, mode, candidates, assets, warnings = self._prepare(
            request, exported_at=datetime.now(UTC)
        )
        final_inspection = self._inspection(
            request=request,
            built=built,
            template_name=template_name,
            version_number=version_number,
            mode=mode,
            candidates=candidates,
            assets=assets,
            warnings=warnings,
        )
        if final_inspection.confirmation_digest != confirmed_inspection_digest:
            raise TemplatePackageExportError(
                "TEMPLATE_CHANGED_DURING_EXPORT",
                "書き出し中に対象内容が変更されたため停止しました。",
            )
        file_name = self._file_name(request)
        relative_path: str | None = None
        if write_to_storage:
            relative_path = f"exports/template-packages/{file_name}"
            if self._storage.exists(relative_path):
                raise TemplatePackageExportError(
                    "TEMPLATE_RELEASE_ALREADY_EXISTS",
                    "同じ配布者・package・versionの書き出しファイルが既に存在します。",
                )
            relative_path = self._storage.write_bytes(relative_path, built.content)
        return TemplatePackageExport(
            content=built.content,
            file_name=file_name,
            manifest=built.manifest,
            inspection=final_inspection,
            storage_relative_path=relative_path,
        )

    def _prepare(
        self,
        request: TemplateExportRequest,
        *,
        exported_at: datetime,
    ) -> tuple[
        BuiltTemplatePackage,
        str,
        int,
        str,
        list[ExportCandidateSetInspection],
        list[ExportAssetInspection],
        list[str],
    ]:
        request.validated_version()
        with self._database.session() as session:
            template, version = self._mapper.select_release(session, request)
            collected = self._collector.collect(
                session,
                version,
                include_preview=request.include_preview,
                include_handwriting_candidates=request.include_handwriting_candidates,
            )
            output_layout = self._mapper.output_layout(
                session, version.id, request.output_layout_owner_key
            )
            refs = CanonicalAssetReferences(
                handwriting_mask=(
                    "assets/handwriting-mask.png"
                    if "assets/handwriting-mask.png" in collected.payloads
                    else None
                ),
                handwriting_mask_edited=(
                    "assets/handwriting-mask-edited.png"
                    if "assets/handwriting-mask-edited.png" in collected.payloads
                    else None
                ),
                preview=(
                    "assets/preview.png"
                    if "assets/preview.png" in collected.payloads
                    else None
                ),
                handwriting_candidates=(
                    "optional/handwriting-candidates.json"
                    if "optional/handwriting-candidates.json" in collected.payloads
                    else None
                ),
                output_layout=(
                    "optional/output-layout.json" if output_layout is not None else None
                ),
                spreadsheet_source=(
                    "optional/spreadsheet-source.xlsx"
                    if "optional/spreadsheet-source.xlsx" in collected.payloads
                    else None
                ),
            )
            canonical, quality = self._mapper.map_template(
                session,
                template,
                version,
                assets=refs,
                source_sheet_name=collected.source_sheet_name,
                source_file_name=collected.source_file_name,
            )
            built = self._serializer.build(
                canonical_template=canonical,
                quality=quality,
                output_layout=output_layout,
                asset_payloads=collected.payloads,
                publisher_id=request.publisher_id,
                package_id=request.package_id,
                release_version=request.version,
                exported_at=exported_at,
            )
            candidates = [
                ExportCandidateSetInspection(
                    name=item.name,
                    value_count=len(item.values),
                    values=item.values,
                    target_patterns=item.target_patterns,
                )
                for item in canonical.candidate_sets
            ]
            warnings = list(quality.warnings)
            return (
                built,
                template.name,
                version.version_number,
                version.mode,
                candidates,
                list(collected.inspections),
                warnings,
            )

    @staticmethod
    def _inspection(
        *,
        request: TemplateExportRequest,
        built: BuiltTemplatePackage,
        template_name: str,
        version_number: int,
        mode: str,
        candidates: list[ExportCandidateSetInspection],
        assets: list[ExportAssetInspection],
        warnings: list[str],
    ) -> TemplateExportInspection:
        basis = {
            "publisherId": request.publisher_id,
            "packageId": request.package_id,
            "version": request.version,
            "contentDigest": built.manifest.content_digest,
            "candidateSets": candidates,
            "assets": assets,
        }
        confirmation_digest = f"sha256:{hashlib.sha256(canonical_json_bytes(basis)).hexdigest()}"
        return TemplateExportInspection(
            template_name=template_name,
            template_version_number=version_number,
            mode=mode,
            candidate_sets=candidates,
            assets=assets,
            output_layout_included=any(
                item.role == "output-layout" for item in built.manifest.files
            ),
            spreadsheet_source_included=any(
                item.role == "spreadsheet-source" for item in built.manifest.files
            ),
            warnings=warnings,
            content_digest=built.manifest.content_digest,
            confirmation_digest=confirmation_digest,
        )

    @staticmethod
    def _file_name(request: TemplateExportRequest) -> str:
        publisher = re.sub(r"[^a-z0-9.-]", "-", request.publisher_id)
        return f"{publisher}--{request.package_id}--{request.version}.llmocr-template"
