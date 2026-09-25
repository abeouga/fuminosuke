from __future__ import annotations

import copy

import pytest

from app.db.models import ProcessingJob, Template, TemplateVersion
from app.services.template_version_service import TemplateVersionService
from tests.integration import test_page_authoring as existing
from tests.integration.test_print_layout_api import print_template  # noqa: F401

paged_draft = existing.paged_draft


def heading(page, name="virtual_group"):
    return {"id": name, "role": "parent_label", "source": "manual", "virtual": True,
            "pageRange": page["range"], "text": "検査区分", "confidence": 1,
            "x": page["width"] - 150, "y": page["height"] - 50, "width": 140, "height": 32}


def save(client, url, state, layout):
    return client.put(url + "/authoring", params={"base_revision": state["revision"]},
                      json=layout)


def test_page_virtual_headings_roundtrip_move_delete_and_fork(client, paged_draft):
    version, job, url, state, layout = paged_draft
    # The shared fixture puts a mapping directly in config, not in layout relations.
    # Establish the equivalent authored relation before comparing heading-only edits.
    layout["relations"] = [{
        "id": "meaning", "inputCellId": "xlsx_C32", "labelCellIds": [],
        "axis": "horizontal", "order": 1, "targetPath": "document.unifiedEarTag",
    }]
    baseline = save(client, url, state, layout)
    assert baseline.status_code == 200, baseline.text
    layout, state = baseline.json()["layout"], baseline.json()["state"]
    mappings_url = f"/api/v1/template-versions/{version}/field-mappings"
    mappings_before = client.get(mappings_url).json()["mappings"]
    before = copy.deepcopy(layout)
    virtuals = [heading(page, f"virtual_{i}") for i, page in enumerate(state["saved"]["pages"])]
    layout["cells"].extend(virtuals)
    # Explicit group membership and a nested heading; source geometry stays unchanged.
    next(c for c in layout["cells"] if c["id"] == "xlsx_C32")["parentLabelCellId"] = "virtual_1"
    nested = {**virtuals[1], "id": "virtual_nested", "parentLabelCellId": "virtual_1", "x": 10}
    layout["cells"].append(nested)
    response = save(client, url, state, layout)
    assert response.status_code == 200, response.text
    saved, updated = response.json()["layout"], response.json()["state"]
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == saved
    for original, cell in zip(before["cells"], saved["cells"], strict=False):
        assert all(original[k] == cell[k] for k in ("id", "x", "y", "width", "height"))
        assert "pageRange" not in cell  # No null metadata added to legacy cells/hashes.
    for old, new in zip(state["saved"]["pages"], updated["saved"]["pages"], strict=True):
        for key in ("range", "transform", "referenceImage", "cells", "fields"):
            assert new[key] == old[key]
        assert client.get(url + f"/pages/{new['page']}/image").status_code == 200
    assert updated["source"]["unsupported"] == []
    mappings_after = client.get(mappings_url).json()["mappings"]
    for old, new in zip(mappings_before, mappings_after, strict=True):
        for key in ("mappingId", "targetPath", "x", "y", "width", "height"):
            assert new[key] == old[key]
    assert saved["relations"] == before["relations"]
    assert state["saved"]["sourceSha256"] == updated["saved"]["sourceSha256"]
    with client.app.state.database.session() as session:
        assert (session.get(ProcessingJob, job).config_snapshot_json["printLayout"]
                == updated["saved"])
    moved = copy.deepcopy(saved)
    moved["cells"][-1]["x"] += 20
    response = save(client, url, updated, moved)
    assert response.status_code == 200, response.text
    saved, updated = response.json()["layout"], response.json()["state"]
    assert saved["cells"][-1]["x"] == 30
    assert not client.get(url + "/qualification").json()["qualified"]
    assert (client.post(f"/api/v1/template-versions/{version}/approve").json()["code"]
            == "PRINT_LAYOUT_OCR_NOT_AVAILABLE")

    with client.app.state.database.session() as session:
        original = session.get(TemplateVersion, version)
        original.status = "ACTIVE"  # Fixture only, never bypass production qualification.
        session.get(Template, original.template_id).active_version_id = version
        session.commit()
    fork = TemplateVersionService(client.app.state.database, client.app.state.storage).edit(
        version, expected_revision=updated["revision"]
    )
    fork_layout = client.get(f"/api/v1/template-versions/{fork.id}/layout").json()
    fork_virtuals = [c for c in fork_layout["cells"] if c.get("virtual")]
    assert [c["pageRange"] for c in fork_virtuals] == [c["pageRange"] for c in saved["cells"][-3:]]
    assert fork_virtuals[-1]["parentLabelCellId"] == fork_virtuals[1]["id"]
    assert {c["id"] for c in fork_virtuals}.isdisjoint(c["id"] for c in saved["cells"])
    fork_url = f"/api/v1/template-versions/{fork.id}/print-layout"
    fork_state = client.get(fork_url).json()
    fork_layout["cells"] = [c for c in fork_layout["cells"] if c["id"] != fork_virtuals[-1]["id"]]
    response = save(client, fork_url, fork_state, fork_layout)
    assert response.status_code == 200, response.text
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == saved


@pytest.mark.parametrize("mutation", [
    "unknown_page", "bounds", "nonvirtual", "input", "source_id", "existing_id", "merge_part",
])
def test_invalid_virtual_page_nodes_fail_before_writes(client, paged_draft, mutation):
    version, _, url, state, layout = paged_draft
    before = copy.deepcopy(layout)
    cell = heading(state["saved"]["pages"][1])
    if mutation == "unknown_page":
        cell["pageRange"] = "A99:D100"
    elif mutation == "bounds":
        cell["x"] = state["saved"]["pages"][1]["width"]
    elif mutation == "nonvirtual":
        cell["virtual"] = False
    elif mutation == "input":
        cell["role"] = "input"
    elif mutation == "source_id":
        cell["id"] = "xlsx_B999"
    elif mutation == "existing_id":
        cell["id"] = layout["cells"].pop()["id"]
    elif mutation == "merge_part":
        layout["cellMerges"] = [{"id": "bad_merge", "parentCellId": layout["cells"][0]["id"],
                                  "direction": "horizontal", "parts": [
                                      {"cell": cell, "partType": "ignored", "literal": ""}]}]
    if mutation != "merge_part":
        layout["cells"].append(cell)
    response = save(client, url, state, layout)
    assert response.status_code in (409, 422), response.text
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == before
    assert client.get(url).json() == state


def test_page_setup_cannot_orphan_virtual_headings(client, paged_draft):
    version, _, url, state, layout = paged_draft
    layout["cells"].append(heading(state["saved"]["pages"][1]))
    response = save(client, url, state, layout)
    assert response.status_code == 200, response.text
    state = response.json()["state"]
    before = response.json()["layout"]
    changed = client.put(url, json={"baseRevision": state["revision"], "breakAfterRows": [30]})
    assert changed.status_code == 409, changed.text
    assert changed.json()["code"] == "PRINT_VIRTUAL_HEADING_PAGE_INVALID"
    cleared = client.delete(url, params={"base_revision": state["revision"]})
    assert cleared.status_code == 409, cleared.text
    assert client.get(url).json() == state
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == before


def test_normal_layout_route_cannot_bypass_page_heading_validation(client, paged_draft):
    version, _, url, state, layout = paged_draft
    layout["cells"].append({**heading(state["saved"]["pages"][0]), "pageRange": "A99:D100"})
    response = client.put(f"/api/v1/template-versions/{version}/layout", json=layout)
    assert response.status_code == 409, response.text
    assert client.get(url).json() == state


@pytest.mark.parametrize("invalid", [None, "missing", "unknown", "physical", "duplicate",
                                      "old_range", "destination", "bounds", "stale", "active"])
def test_explicit_heading_repage_is_atomic_and_preserves_semantics(client, paged_draft, invalid):
    version, job_id, url, state, layout = paged_draft
    virtual = heading(state["saved"]["pages"][1])
    layout["cells"].append(virtual)
    layout["cells"][0]["parentLabelCellId"] = virtual["id"]
    response = save(client, url, state, layout)
    assert response.status_code == 200, response.text
    before, state = response.json()["layout"], response.json()["state"]
    to_range = state["saved"]["pages"][1]["range"].replace("32:", "31:")
    move = {"cellId": virtual["id"], "fromRange": virtual["pageRange"], "toRange": to_range}
    payload = {"baseRevision": state["revision"], "breakAfterRows": [30],
               "pageHeadingMoves": [move]}
    if invalid == "missing":
        payload["pageHeadingMoves"] = []
    if invalid == "unknown":
        move["cellId"] = "unknown"
    if invalid == "physical":
        move["cellId"] = before["cells"][0]["id"]
    if invalid == "duplicate":
        payload["pageHeadingMoves"].append(dict(move))
    if invalid == "old_range":
        move["fromRange"] = "A99:D100"
    if invalid == "destination":
        move["toRange"] = "A99:D100"
    if invalid == "bounds":
        payload["orientation"] = "landscape"
    if invalid == "stale":
        payload["baseRevision"] = "0" * 64
    with client.app.state.database.session() as session:
        v = session.get(TemplateVersion, version)
        config_path = v.config_relative_path
        if invalid == "active":
            v.status = "ACTIVE"  # Fixture-only authorization guard test.
            session.commit()
    config = client.app.state.storage.read_bytes(config_path)
    stored_state = client.get(url).json()
    preview = client.post(url + "/preview", json=payload)
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == before
    response = client.put(url, json=payload)
    if invalid:
        assert response.status_code in (409, 422), response.text
        assert client.get(url).json() == stored_state
        assert client.get(f"/api/v1/template-versions/{version}/layout").json() == before
    else:
        assert preview.status_code == 200, preview.text
        assert response.status_code == 200, response.text
        expected = copy.deepcopy(before)
        expected["cells"][-1]["pageRange"] = to_range
        assert response.json()["layout"] == expected
        assert client.get(f"/api/v1/template-versions/{version}/layout").json() == expected
        assert [p["endRow"] for p in response.json()["saved"]["pages"]] == [30, 63]
        assert not client.get(url + "/qualification").json()["qualified"]
        assert client.put(url, json=payload).status_code == 409
        with client.app.state.database.session() as session:
            snapshot = session.get(ProcessingJob, job_id).config_snapshot_json
            assert snapshot["printLayout"] == response.json()["saved"]
    assert client.app.state.storage.read_bytes(config_path) == config


def test_virtual_heading_uses_page_bounds_not_legacy_canonical_bounds(client, paged_draft):
    _, _, url, state, layout = paged_draft
    response = client.put(url, json={"baseRevision": state["revision"], "paperSize": "A3",
                                     "breakAfterRows": [31]})
    assert response.status_code == 200, response.text
    state = response.json()
    cell = heading(state["saved"]["pages"][1])
    assert cell["x"] > layout["canonicalWidth"]
    assert cell["y"] > layout["canonicalHeight"]
    layout["cells"].append(cell)
    response = save(client, url, state, layout)
    assert response.status_code == 200, response.text
    assert response.json()["layout"]["cells"][-1]["x"] == cell["x"]
    assert response.json()["layout"]["cells"][-1]["y"] == cell["y"]


def test_heading_repage_commit_failure_rolls_back_layout_and_pages(client, paged_draft):
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.domain.print_layout import PrintLayoutRequest
    from app.services.print_layout_service import PrintLayoutService

    version, job_id, url, state, layout = paged_draft
    layout["cells"].append(heading(state["saved"]["pages"][1]))
    response = save(client, url, state, layout)
    before, state = response.json()["layout"], response.json()["state"]
    request = PrintLayoutRequest.model_validate({
        "baseRevision": state["revision"], "breakAfterRows": [30],
        "pageHeadingMoves": [{"cellId": "virtual_group",
                              "fromRange": before["cells"][-1]["pageRange"],
                              "toRange": before["cells"][-1]["pageRange"].replace("32:", "31:")}],
    })
    with client.app.state.database.session() as session:
        snapshot = copy.deepcopy(session.get(ProcessingJob, job_id).config_snapshot_json)

    def fail(session):
        if any(isinstance(item, TemplateVersion) and item.id == version for item in session.dirty):
            session.flush()
            raise RuntimeError("injected commit failure")

    event.listen(Session, "before_commit", fail)
    try:
        with pytest.raises(RuntimeError, match="injected commit failure"):
            service = PrintLayoutService(client.app.state.database, client.app.state.storage)
            service.save(version, request)
    finally:
        event.remove(Session, "before_commit", fail)
    assert client.get(url).json() == state
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == before
    with client.app.state.database.session() as session:
        assert session.get(ProcessingJob, job_id).config_snapshot_json == snapshot


def test_repeated_section_connects_to_page_virtual_heading_and_rejects_cycles(client, paged_draft):
    version, _, url, state, layout = paged_draft
    page = state["saved"]["pages"][1]
    layout["cells"].append(heading(page))
    for cell in layout["cells"]:
        if cell["id"] in {"xlsx_B33", "xlsx_C33"}:
            cell.update(role="label", source="manual")
    layout["recordSections"] = [{
        "id": "band", "title": "検査一覧", "recordType": "generic",
        "collectionPath": "genericRecords.tests", "recordLabelCellIds": ["xlsx_B33", "xlsx_C33"],
        "parentLabelCellId": "virtual_group", "rowKeyStrategy": "position", "source": "manual",
    }]
    response = save(client, url, state, layout)
    assert response.status_code == 200, response.text
    before, state = response.json()["layout"], response.json()["state"]
    assert before["recordSections"][0]["parentLabelCellId"] == "virtual_group"
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == before
    proposed = copy.deepcopy(before)
    proposed["cells"][-1]["parentLabelCellId"] = "virtual_group"
    assert save(client, url, state, proposed).status_code == 422
    assert client.get(url).json() == state
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == before


def test_merge_parent_cannot_be_replaced_by_a_virtual_heading(client, paged_draft):
    version, _, url, state, layout = paged_draft
    merged = existing.merge_cells(layout, ["xlsx_B3", "xlsx_C3"])
    response = save(client, url, state, merged)
    assert response.status_code == 200, response.text
    before, state = response.json()["layout"], response.json()["state"]
    proposed = copy.deepcopy(before)
    proposed["cells"][-1] = heading(state["saved"]["pages"][0], "merged_test")
    proposed["cellMerges"] = []
    response = save(client, url, state, proposed)
    assert response.status_code == 409, response.text
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == before
    assert client.get(url).json() == state
