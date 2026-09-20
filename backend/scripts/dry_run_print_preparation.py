"""Reprepare a DRAFT in a private copy; never open the source database writable.

Retains the copy for inspection. No configuration or external OCR API is loaded.
Run from backend: python -m scripts.dry_run_print_preparation VERSION RUNTIME_SHA
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from app.core.config import DatabaseSettings
from app.db.models import TemplateVersion
from app.db.session import Database
from app.domain.calc_print_grid import MODEL as CALC_MODEL
from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services import calc_workbook_geometry
from app.services.print_preparation_service import PrintPreparationService
from app.services.wsl_workbook_geometry import MODEL as WSL_MODEL
from app.storage.local_storage import LocalStorageService


def expected_calc_plan(initial: dict[str, Any]) -> dict[str, Any]:
    """Predict the explicit migration before native preparation, without saving it."""
    saved = initial["saved"]
    if saved["geometryModelVersion"] != WSL_MODEL:
        raise ValueError("Calc comparison requires an existing measured WSL draft")
    evidence = copy.deepcopy(saved["geometryEvidence"])
    evidence.update(model=CALC_MODEL, exportPolicy=calc_workbook_geometry.EXPORT_POLICY,
                    fodsSha256=evidence["importReport"]["fodsSha256"])
    source = calc_workbook_geometry.apply_dimensions(initial["source"], evidence)
    request = PrintLayoutRequest.model_validate({
        **{key: saved[key] for key in ("paperSize", "orientation", "margins",
                                     "horizontalCentering", "verticalCentering")},
        "baseRevision": initial["revision"],
        "breakAfterRows": [p["endRow"] for p in saved["pages"][:-1]],
    })
    return page_plan(source, request, saved)


def field_identity(pages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    # Only computed print geometry is allowed to change with the explicit model.
    geometry = {"x", "y", "width", "height", "rectangle"}
    return [[{k: v for k, v in f.items() if k not in geometry} for f in p["fields"]]
            for p in pages]


def dry_run(version_id: str, runtime_id: str, data: Path,
            *, geometry_model: str | None = None) -> dict[str, Any]:
    if geometry_model not in {None, CALC_MODEL}:
        raise ValueError("Unsupported explicit geometry model")
    source = LocalStorageService(data.resolve(strict=True))
    uri = (source.root / "app.db").as_uri() + "?mode=ro"
    destination = Path(tempfile.mkdtemp(prefix="llmocr-prepare-copy-"))
    copied = LocalStorageService(destination)
    copied.initialize()
    original_files: dict[str, str] = {}

    def snapshot(connection: sqlite3.Connection) -> tuple[Any, ...]:
        row = connection.execute(
            "SELECT status,layout_json,quality_json,config_relative_path,source_job_id "
            "FROM template_versions WHERE id=?", (version_id,),
        ).fetchone()
        if row is None or row[0] != "DRAFT":
            raise ValueError("An existing DRAFT version is required")
        job = connection.execute(
            "SELECT config_snapshot_json FROM processing_jobs WHERE id=?", (row[4],),
        ).fetchone()
        return row, job

    with closing(sqlite3.connect(uri, uri=True)) as original:
        original.execute("PRAGMA query_only=ON")
        before = snapshot(original)
        with closing(sqlite3.connect(destination / "app.db")) as target:
            original.backup(target)

    # Only the copy is opened by the application's writable Database wrapper.
    database = Database(DatabaseSettings(url=f"sqlite:///{destination / 'app.db'}"))
    try:
        with closing(sqlite3.connect(destination / "app.db")) as target:
            if snapshot(target) != before:
                raise ValueError("Source changed while making the database copy")
            row = before[0]
            paths = {row[3]}
            paths.update(item[0] for item in target.execute(
                "SELECT relative_path FROM job_artifacts WHERE job_id=? OR id=("
                "SELECT clean_template_artifact_id FROM template_versions WHERE id=?)",
                (row[4], version_id),
            ))
        for relative in sorted(paths):
            payload = source.read_bytes(relative)
            original_files[relative] = hashlib.sha256(payload).hexdigest()
            copied.write_bytes(relative, payload)

        service = PrintPreparationService(database, copied)
        initial = service.get(version_id)
        prediction = expected_calc_plan(initial) if geometry_model == CALC_MODEL else None
        with database.session() as session:
            record = session.get(TemplateVersion, version_id)
            assert record is not None
            layout = copy.deepcopy(record.layout_json)
        result = service.prepare(version_id, initial["revision"], "Ubuntu-22.04",
                                 runtime_id=runtime_id, geometry_model=geometry_model)
        old_pages, new_pages = initial["saved"]["pages"], result["saved"]["pages"]
        preserved = {
            "pageRanges": [p["range"] for p in old_pages] == [p["range"] for p in new_pages],
            "fieldsAndBindings": ([p["fields"] for p in (
                                      prediction["pages"] if prediction else old_pages)]
                                  == [p["fields"] for p in new_pages]),
            "physicalIdentityAndMeaning": field_identity(old_pages) == field_identity(new_pages),
            "referenceImages": [p["referenceImage"]["sha256"] for p in old_pages]
                               == [p["referenceImage"]["sha256"] for p in new_pages],
        }
        if prediction:
            preserved["explicitModel"] = result["saved"]["geometryModelVersion"] == CALC_MODEL
            preserved["predictedScale"] = (
                result["saved"]["commonScale"] == prediction["commonScale"])
            preserved["predictedTransforms"] = [p["transform"] for p in new_pages] == [
                p["transform"] for p in prediction["pages"]]
        with database.session() as session:
            record = session.get(TemplateVersion, version_id)
            assert record is not None
            preserved["canonicalLayout"] = record.layout_json == layout
            preserved["draftStatus"] = record.status == "DRAFT"
        preserved["savedAndGetEqual"] = service.get(version_id) == result
        preserved["copiedInputs"] = all(
            hashlib.sha256(copied.read_bytes(name)).hexdigest() == sha
            for name, sha in original_files.items()
        )
        if not all(preserved.values()):
            raise ValueError(f"Repreparation changed protected data: {preserved}")
        report_path = result["saved"]["printFiles"]["report"]["relativePath"]
        report = json.loads(copied.read_bytes(report_path))
        return {
            "copyDirectory": str(destination), "versionId": version_id,
            "beforeRevision": initial["revision"], "afterRevision": result["revision"],
            "beforeModel": initial["saved"]["geometryModelVersion"],
            "afterModel": result["saved"]["geometryModelVersion"],
            "fieldComparison": "predicted-new-model" if prediction else "unchanged",
            "preserved": preserved, "runtimeImage": report["runtimeImage"],
            "workerIdentity": report["workerIdentity"],
            "fontDirectoryIdentity": report["fontDirectoryIdentity"],
            "originalUnchanged": True, "productionQualified": False,
        }
    finally:
        database.dispose()
        with closing(sqlite3.connect(uri, uri=True)) as original:
            original.execute("PRAGMA query_only=ON")
            if snapshot(original) != before:
                raise ValueError("Original version or source job changed during dry run")
        if any(hashlib.sha256(source.read_bytes(name)).hexdigest() != sha
               for name, sha in original_files.items()):
            raise ValueError("Original input artifact changed during dry run")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version_id")
    parser.add_argument("runtime_id")
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--geometry-model", choices=[CALC_MODEL])
    args = parser.parse_args()
    print(json.dumps(dry_run(args.version_id, args.runtime_id, args.data,
                             geometry_model=args.geometry_model), ensure_ascii=True))
