from __future__ import annotations

import copy

import pytest

from app.db.models import ProcessingJob, Template, TemplateVersion
from app.services.template_version_service import TemplateVersionService
from tests.integration import test_print_layout_api as existing
from tests.integration.test_print_layout_api import print_template  # noqa: F401
from tests.unit.test_draft_page_authoring import draft_workbook


@pytest.fixture
def paged_draft(client, monkeypatch, request):
    monkeypatch.setattr(existing, "workbook_bytes", draft_workbook)
    version, job, content, layout = request.getfixturevalue("print_template")
    url = f"/api/v1/template-versions/{version}/print-layout"
    state = client.get(url).json()
    assert state["source"]["unsupported"] == []
    response = client.put(url, json={"baseRevision": state["revision"]})
    assert response.status_code == 200, response.text
    assert [p["endRow"] for p in response.json()["saved"]["pages"]] == [31, 63]
    return version, job, url, response.json(), layout


def test_human_roles_and_meaning_save_canonical_geometry_and_page_bindings(client, paged_draft):
    version, job, url, state, layout = paged_draft
    before = copy.deepcopy(layout)
    target = next(c for c in layout["cells"] if c["id"] == "xlsx_C32")
    target["role"] = "input"
    target["source"] = "manual"
    layout["relations"] = [{"id": "human_relation", "inputCellId": target["id"],
                            "labelCellIds": [], "axis": "horizontal", "order": 1,
                            "targetPath": "document.unifiedEarTag"}]
    response = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                          json=layout)
    assert response.status_code == 200, response.text
    saved, next_state = response.json()["layout"], response.json()["state"]
    assert next_state["revision"] != state["revision"]
    for original, cell in zip(before["cells"], saved["cells"], strict=True):
        assert all(cell[k] == original[k] for k in ("id", "x", "y", "width", "height"))
        if cell["id"] != target["id"]:
            assert cell == original
    for old, new in zip(state["saved"]["pages"], next_state["saved"]["pages"], strict=True):
        assert new["range"] == old["range"]
        assert new["referenceImage"] == old["referenceImage"]
        assert new["transform"] == old["transform"]
        assert client.get(url + f"/pages/{new['page']}/image").status_code == 200
    field = next(f for f in next_state["saved"]["pages"][1]["fields"]
                 if f["cellId"] == target["id"])
    assert field["role"] == "input"
    assert field["bindings"][0]["mappingId"] == "layout_xlsx_C32"
    assert field["bindings"][0]["targetPath"] == "document.unifiedEarTag"
    with client.app.state.database.session() as session:
        assert (session.get(ProcessingJob, job).config_snapshot_json["printLayout"]
                == next_state["saved"])
    assert not client.get(url + "/qualification").json()["qualified"]
    assert (client.post(f"/api/v1/template-versions/{version}/approve").json()["code"]
            == "PRINT_LAYOUT_OCR_NOT_AVAILABLE")


@pytest.mark.parametrize("mutation", ["revision", "geometry", "remove", "active"])
def test_stale_or_structural_page_edits_fail_without_mutating_draft(client, paged_draft, mutation):
    version, _, url, state, layout = paged_draft
    if mutation == "active":
        with client.app.state.database.session() as session:
            session.get(TemplateVersion, version).status = "ACTIVE"
            session.commit()
    before = client.get(f"/api/v1/template-versions/{version}/layout").json()
    if mutation == "geometry":
        layout["cells"][0]["x"] += 1
    elif mutation == "remove":
        layout["cells"].pop()
    revision = "0" * 64 if mutation == "revision" else state["revision"]
    response = client.put(url + "/authoring", params={"base_revision": revision},
                          json=layout)
    assert response.status_code == 409, response.text
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == before
    assert client.get(url).json()["saved"] == state["saved"]


def test_version_fork_edits_do_not_change_v1_pages_or_semantics(client, paged_draft):
    version_id, _, url, state, layout = paged_draft
    database, storage = client.app.state.database, client.app.state.storage
    with database.session() as session:
        version = session.get(TemplateVersion, version_id)
        version.status = "ACTIVE"  # fixture setup, never bypass the public activation gate
        session.get(Template, version.template_id).active_version_id = version_id
        session.commit()
    fork = TemplateVersionService(database, storage).edit(version_id)
    fork_url = f"/api/v1/template-versions/{fork.id}/print-layout"
    fork_state = client.get(fork_url).json()
    fork_state = client.put(fork_url, json={"baseRevision": fork_state["revision"],
                                          "breakAfterRows": [30]}).json()
    fork_layout = client.get(f"/api/v1/template-versions/{fork.id}/layout").json()
    fork_layout["cells"][0]["role"] = "ignored"
    response = client.put(fork_url + "/authoring",
                          params={"base_revision": fork_state["revision"]}, json=fork_layout)
    assert response.status_code == 200, response.text
    assert response.json()["state"]["saved"]["pages"][0]["endRow"] == 30
    assert client.get(url).json()["saved"] == state["saved"]
    assert client.get(f"/api/v1/template-versions/{version_id}/layout").json() == layout
