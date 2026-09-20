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
