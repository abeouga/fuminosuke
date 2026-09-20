"""Read-only comparison of the connected Calc planner against saved observations.

The new model label is attached only to an in-memory diagnostic candidate.
This does not prepare files, migrate a draft, or issue a qualification.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.models import TemplateVersion
from app.db.session import Database
from app.domain.calc_print_grid import MODEL
from app.domain.print_layout import PrintLayoutRequest, page_plan
from app.services import calc_workbook_geometry as calc
from app.services.prepared_reference_evidence import load_prepared_reference
from app.services.prepared_reference_geometry import load_prepared_geometry
from app.services.print_layout_service import PrintLayoutService, digest
from app.services.reference_vector_measurement import measure_fields
from app.services.wsl_workbook_geometry import MODEL as OLD_MODEL
from app.storage.local_storage import LocalStorageService


def inspect(root: Path, version_id: str) -> dict[str, Any]:
    root = root.resolve(strict=True)
    uri = (root / "app.db").as_uri() + "?mode=ro"
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True))
    storage = LocalStorageService(root)
    # _source/restore_layout don't access the database wrapper; using an
    # uninitialized wrapper prevents its constructor from setting WAL pragmas.
    service = PrintLayoutService(object.__new__(Database), storage)
    try:
        with Session(engine, autoflush=False) as session:
            session.connection().exec_driver_sql("PRAGMA query_only=ON")
            version = session.get(TemplateVersion, version_id)
            if version is None or version.status != "DRAFT":
                raise ValueError("An existing DRAFT is required")
            original = copy.deepcopy((version.status, version.layout_json, version.quality_json))
            saved = copy.deepcopy(version.quality_json["printLayout"])
            if saved["geometryModelVersion"] != OLD_MODEL:
                raise ValueError("A historical WSL measurement is required")
            source = service._source(session, version)
            if source["unsupported"]:
                raise ValueError("Source is not supported by the connected planner")
            evidence = copy.deepcopy(saved["geometryEvidence"])
            evidence.update(model=MODEL, exportPolicy=calc.EXPORT_POLICY,
                            fodsSha256=evidence["importReport"]["fodsSha256"])
            projected = calc.apply_dimensions(source, evidence)
            request = PrintLayoutRequest.model_validate({
                **{key: saved[key] for key in (
                    "paperSize", "orientation", "margins",
                    "horizontalCentering", "verticalCentering")},
                "baseRevision": service.revision(version),
                "breakAfterRows": [p["endRow"] for p in saved["pages"][:-1]],
            })
            candidate = page_plan(projected, request, saved)
            ref = version.quality_json["referenceQualification"]
            raw = storage.read_bytes(ref["relativePath"])
            if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
                raise ValueError("Saved observation changed")
            report = json.loads(raw)
            if (report["templateVersionId"] != version_id
                    or report["planSha256"] != digest(saved)):
                raise ValueError("Observation plan linkage changed")
            prepared = load_prepared_reference(session, storage, version)
            observations = load_prepared_geometry(
                session, storage, version, prepared, report["geometryEvidence"])

            def measured_content(name: str) -> bytes:
                refs = [part for part in report["geometryEvidence"]["artifacts"]
                        if part["file"] == name]
                if len(refs) != 1:
                    raise ValueError("Ambiguous measurement artifact")
                content = storage.read_bytes(refs[0]["relativePath"])
                if hashlib.sha256(content).hexdigest() != refs[0]["sha256"]:
                    raise ValueError("Measurement artifact changed after validation")
                return content

            vectors = json.loads(measured_content("probe-vectors.json"))["vectors"]["pages"]
            results = []
            for old, new, observation, vector in zip(
                saved["pages"], candidate["pages"], observations, vectors, strict=True
            ):
                def identities(page: dict[str, Any]) -> list[tuple[Any, ...]]:
                    return [(f["cellId"], f["sourceRange"], f["bindings"]) for f in page["fields"]]

                if new["range"] != old["range"] or identities(new) != identities(old):
                    raise ValueError("Page range or field meaning changed")
                image = measured_content(f"probe-{new['page']}.png")
                if hashlib.sha256(image).hexdigest() != observation["measurementImageSha256"]:
                    raise ValueError("Measurement image changed")
                new["referenceImage"] = {"sha256": observation["measurementImageSha256"]}
                checked = measure_fields(new, vector, image)
                before = {f["cellId"]: f for f in observation["fields"]}
                for field in checked["fields"]:
                    if (field["status"] != "OBSERVED_WITHIN_DIAGNOSTIC_BUDGET"
                            or [e.get("segmentIndices") for e in field["edges"]] != [
                                e.get("segmentIndices") for e in before[field["cellId"]]["edges"]]):
                        raise ValueError("Field observation or segment correspondence changed")
                results.append({"page": new["page"], "fields": checked["fieldCount"],
                                "maxErrorPx": checked["maxObservedErrorPx"],
                                "bindingsAndSegmentsUnchanged": True})
            if original != (version.status, version.layout_json, version.quality_json):
                raise ValueError("Diagnostic mutated ORM state")
            return {"readOnly": True, "candidateOnly": True,
                    "savedModel": saved["geometryModelVersion"],
                    "candidateModel": candidate["geometryModelVersion"],
                    "oldScale": saved["commonScale"], "newScale": candidate["commonScale"],
                    "pages": results, "nativeCalls": 0, "openaiCalls": 0}
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("version")
    args = parser.parse_args()
    print(json.dumps(inspect(args.data, args.version)))
