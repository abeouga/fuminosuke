"""Version/DB/HTTP boundaries with explicit renderer stubs, not native qualification."""
import copy
import hashlib
import math

import pytest
from sqlalchemy import select

from app.db.models import JobArtifact, ProcessingJob, TemplateVersion
from app.domain.calc_print_grid import MODEL, dimension_grid
from app.services import calc_workbook_geometry as calc
from app.services import print_preparation_service as preparation
from tests.integration import test_pinned_print_preparation as pinned

prepared_fixture = pinned.prepared_fixture
print_template = pinned.print_template
saved_template = pinned.saved_template


@pytest.fixture
def calc_fixture(client, prepared_fixture, monkeypatch):
    _, _, _, _, _, _, original_render = prepared_fixture
    original_import = preparation.import_wsl_geometry
    original_dimensions = preparation.dimensions
    monkeypatch.setattr(client.app.state.settings.app, "print_wsl_runtime_id", pinned.RUNTIME_ID)

    def environment(report):
        return {**report, "runtimeImage": copy.deepcopy(pinned.RUNTIME),
                "fontDirectoryIdentity": copy.deepcopy(pinned.FONTS),
                "workerIdentity": copy.deepcopy(pinned.WORKER)}

    def imported(data, **kwargs):
        fods, report = original_import(data, **kwargs)
        report = environment({**report, "fodsSha256": hashlib.sha256(fods).hexdigest()})
        if "dimension_request" not in kwargs:
            return fods, report
        request = kwargs["dimension_request"]
        values = measured(fods, request["sheetName"], request["rows"], request["columns"])
        observation = {"policy": "wsl-uno-dimension-v1", "request": copy.deepcopy(request),
                       "sourceSha256": hashlib.sha256(data).hexdigest(),
                       "fodsSha256": report["fodsSha256"]}
        for axis in ("rows", "columns"):
            cumulative, records = 0, []
            hidden = set(values["hidden" + axis.title()])
            for index, twips in enumerate(dimension_grid(
                    values[axis], values["hidden" + axis.title()])["twips"], 1):
                first = (cumulative * 127 + 36) // 72
                cumulative += twips
                records.append([(twips * 127 + 36) // 72,
                                (cumulative * 127 + 36) // 72 - first + 1, index not in hidden])
            observation[axis] = records
        return fods, {**report, "sourceSha256": observation["sourceSha256"],
                      "dimensionObservation": observation}

    def measured(fods, *args):
        result = original_dimensions(fods, *args)
        for axis in ("rows", "columns"):
            values = []
            for value in result[axis]:
                twips = math.floor(value * 20 + .5)
                mm100 = (twips * 127 + 36) // 72
                values.append(((mm100 * 10000 + 1270) // 2540) * 72 / 10000)
            result[axis] = values
        result.update(model=MODEL, exportPolicy=calc.EXPORT_POLICY,
                      fodsSha256=hashlib.sha256(fods).hexdigest())
        return result

    def rendered(data, **kwargs):
        pdf, images, report = original_render(data, **kwargs)
        return pdf, images, environment(report)

    monkeypatch.setattr(preparation, "import_wsl_geometry", imported)
    monkeypatch.setattr(calc, "dimensions", measured)
    monkeypatch.setattr(preparation, "render_wsl_reference", rendered)
    return prepared_fixture


def test_explicit_calc_prepare_and_get_preserve_meaning_and_download_identity(
    client, calc_fixture, monkeypatch,
):
    version, job, _, url, state, calls, _ = calc_fixture
    # The shared print fixture injects config mappings directly, without layout
    # relations. Establish actual authoring state through the public save path
    # before testing that a subsequent no-op save retains its meaning.
    layout = client.get(f"/api/v1/template-versions/{version}/layout").json()
    layout["relations"] = [{
        "id": "meaning", "inputCellId": "xlsx_C32", "labelCellIds": [],
        "axis": "horizontal", "order": 1, "targetPath": "document.unifiedEarTag",
    }]
    authored = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                          json=layout)
    assert authored.status_code == 200, authored.text
    state = authored.json()["state"]
    with client.app.state.database.session() as db:
        original_layout = copy.deepcopy(db.get(TemplateVersion, version).layout_json)
    response = client.post(url + "/prepare", params={
        "base_revision": state["revision"], "geometry_model": MODEL})
    assert response.status_code == 200, response.text
    ready = response.json()
    plan = ready["saved"]
    assert plan["geometryModelVersion"] == MODEL
    assert ready["source"]["geometryModelVersion"] == MODEL
    assert [b for p in plan["pages"] for f in p["fields"] for b in f["bindings"]] == [{
        "mappingId": "layout_xlsx_C32", "sourceRegionId": "layout_xlsx_C32",
        "targetPath": "document.unifiedEarTag", "mappingType": "text",
    }]
    assert [p["range"] for p in plan["pages"]] == [p["range"] for p in state["saved"]["pages"]]
    assert client.get(url).json() == ready
    with client.app.state.database.session() as db:
        current = db.get(TemplateVersion, version)
        assert current.layout_json == original_layout and current.status == "DRAFT"
        assert db.get(ProcessingJob, job).config_snapshot_json["printLayout"] == plan
    for kind in ("xlsx", "pdf"):
        response = client.get(url + "/files/" + kind,
                              params={"base_revision": ready["revision"]})
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == plan["printFiles"][kind]["sha256"]
    assert client.post(url + "/prepare", params={
        "base_revision": state["revision"], "geometry_model": MODEL}).status_code == 409
    assert len(calls) == 1
    assert client.get(url).json() == ready
    authored = client.put(url + "/authoring", params={"base_revision": ready["revision"]},
                          json=original_layout)
    assert authored.status_code == 200, authored.text
    author_state = authored.json()["state"]
    assert author_state["saved"]["geometryModelVersion"] == MODEL
    assert author_state["saved"]["printFiles"] == plan["printFiles"]
    assert [p["transform"] for p in author_state["saved"]["pages"]] == [
        p["transform"] for p in plan["pages"]]
    for new_page, old_page in zip(author_state["saved"]["pages"], plan["pages"], strict=True):
        assert [(f["cellId"], f["sourceRange"], f["rectangle"], f["bindings"])
                for f in new_page["fields"]] == [
            (f["cellId"], f["sourceRange"], f["rectangle"], f["bindings"])
            for f in old_page["fields"]]
    assert len(calls) == 1
    # A subsequent explicit preparation from an older client retains this model.
    again = client.post(url + "/prepare", params={"base_revision": author_state["revision"]})
    assert again.status_code == 200, again.text
    assert again.json()["saved"]["geometryModelVersion"] == MODEL
    assert client.post(f"/api/v1/template-versions/{version}/approve").json()["code"] == (
        "PRINT_LAYOUT_OCR_NOT_AVAILABLE")


@pytest.mark.parametrize("failure", ["runtime_missing", "dimension", "renderer",
                                    "observation_missing", "observation_wrapped"])
def test_calc_prepare_failure_keeps_all_existing_state(client, calc_fixture, monkeypatch, failure):
    version, job, _, url, state, _, _ = calc_fixture
    with client.app.state.database.session() as db:
        original = copy.deepcopy(db.get(TemplateVersion, version).quality_json)
        snapshot = copy.deepcopy(db.get(ProcessingJob, job).config_snapshot_json)
        artifacts = list(db.scalars(select(JobArtifact.id)))
    if failure == "runtime_missing":
        monkeypatch.setattr(client.app.state.settings.app, "print_wsl_runtime_id", None)
    elif failure.startswith("observation_"):
        original_import = preparation.import_wsl_geometry

        def changed(*args, **kwargs):
            fods, report = original_import(*args, **kwargs)
            if failure == "observation_missing":
                report.pop("dimensionObservation")
            else:
                report["dimensionObservation"]["rows"][0][1] += 65536
            return fods, report

        monkeypatch.setattr(preparation, "import_wsl_geometry", changed)
    else:
        def fail(*args, **kwargs):
            raise ValueError("Injected failure")
        if failure == "dimension":
            monkeypatch.setattr(calc, "dimensions", fail)
        else:
            monkeypatch.setattr(preparation, "render_wsl_reference", fail)
    response = client.post(url + "/prepare", params={
        "base_revision": state["revision"], "geometry_model": MODEL})
    assert response.status_code == 409, response.text
    with client.app.state.database.session() as db:
        assert db.get(TemplateVersion, version).quality_json == original
        assert db.get(ProcessingJob, job).config_snapshot_json == snapshot
        assert list(db.scalars(select(JobArtifact.id))) == artifacts
