from __future__ import annotations

import copy
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.db.models import ProcessingJob, Template, TemplateVersion
from app.domain.print_layout import transform_rect
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


@pytest.mark.parametrize("row, text", [(3, "分類 A\u3000\u3000"), (33, " \u3000Reference 42 ")])
def test_imported_literal_boundary_whitespace_can_save_on_any_page(
    client, monkeypatch, request, row, text,
):
    def workbook_bytes():
        workbook = load_workbook(BytesIO(draft_workbook()))
        workbook.active[f"C{row}"] = text
        result = BytesIO()
        workbook.save(result)
        return result.getvalue()

    monkeypatch.setattr(existing, "workbook_bytes", workbook_bytes)
    version, job, content, layout = request.getfixturevalue("print_template")
    url = f"/api/v1/template-versions/{version}/print-layout"
    initial = client.get(url).json()
    response = client.put(url, json={"baseRevision": initial["revision"]})
    assert response.status_code == 200, response.text
    state = response.json()
    proposed = merge_cells(layout, [f"xlsx_B{row}", f"xlsx_C{row}"])
    literal = proposed["cellMerges"][0]["parts"][1]
    assert literal["cell"]["text"] == text.strip()
    literal.update(partType="literal", literal=text.strip())
    # A real text change is rejected before any authoring data is saved.
    rejected = copy.deepcopy(proposed)
    rejected["cellMerges"][0]["parts"][1]["literal"] = "different text"
    response = client.put(url + "/authoring",
                          params={"base_revision": state["revision"]}, json=rejected)
    assert response.status_code == 409
    assert response.json()["code"] == "SPREADSHEET_OUTPUT_MERGE_CONFLICT"
    assert client.get(url).json() == state
    response = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                          json=proposed)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["layout"]["cellMerges"][0]["parts"] == proposed["cellMerges"][0]["parts"]
    assert client.get(url).json() == saved["state"]
    for before, after in zip(
        state["saved"]["pages"], saved["state"]["saved"]["pages"], strict=True,
    ):
        for key in ("range", "transform", "referenceImage", "cells"):
            assert before[key] == after[key]
    assert load_workbook(BytesIO(content)).active[f"C{row}"].value == text


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


def test_nested_heading_and_repeated_band_parent_save_reload_without_geometry_changes(
    client, paged_draft,
):
    version, job, url, state, layout = paged_draft
    before = copy.deepcopy(layout)
    for cell in layout["cells"]:
        if cell["id"] in {"xlsx_B2", "xlsx_B3"}:
            cell.update(role="parent_label", source="manual")
        if cell["id"] in {"xlsx_B5", "xlsx_C5"}:
            cell.update(role="label", source="manual")
        if cell["id"] == "xlsx_B3":
            cell["parentLabelCellId"] = "xlsx_B2"
    layout["recordSections"] = [{"id": "section", "title": "検査一覧",
                                 "recordType": "generic", "collectionPath": "genericRecords.tests",
                                 "recordLabelCellIds": ["xlsx_B5", "xlsx_C5"],
                                 "parentLabelCellId": "xlsx_B3", "rowKeyStrategy": "position",
                                 "source": "manual"}]
    response = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                          json=layout)
    assert response.status_code == 200, response.text
    saved = response.json()["layout"]
    reloaded = client.get(f"/api/v1/template-versions/{version}/layout").json()
    assert reloaded == saved
    assert saved["recordSections"][0]["parentLabelCellId"] == "xlsx_B3"
    assert next(c for c in saved["cells"] if c["id"] == "xlsx_B3")["parentLabelCellId"] == "xlsx_B2"
    for original, cell in zip(before["cells"], saved["cells"], strict=True):
        assert all(cell[key] == original[key] for key in ("id", "x", "y", "width", "height"))
    assert saved["relations"] == before["relations"]


def test_page_authoring_can_delete_cells_without_changing_survivor_or_page_geometry(
    client, paged_draft,
):
    version, job, url, state, layout = paged_draft
    removed_id = "xlsx_C32"
    before_cells = {cell["id"]: copy.deepcopy(cell) for cell in layout["cells"]}
    layout["cells"] = [cell for cell in layout["cells"] if cell["id"] != removed_id]

    response = client.put(
        url + "/authoring",
        params={"base_revision": state["revision"]},
        json=layout,
    )

    assert response.status_code == 200, response.text
    saved, next_state = response.json()["layout"], response.json()["state"]
    assert removed_id not in {cell["id"] for cell in saved["cells"]}
    for cell in saved["cells"]:
        assert cell == before_cells[cell["id"]]
    for old, new in zip(state["saved"]["pages"], next_state["saved"]["pages"], strict=True):
        assert new["range"] == old["range"]
        assert new["referenceImage"] == old["referenceImage"]
        assert new["transform"] == old["transform"]
    assert removed_id not in {
        field["cellId"]
        for page in next_state["saved"]["pages"]
        for field in page["fields"]
    }
    mappings_response = client.get(f"/api/v1/template-versions/{version}/field-mappings")
    assert mappings_response.status_code == 200, mappings_response.text
    assert "layout_xlsx_C32" not in {
        mapping["mappingId"]
        for mapping in mappings_response.json().get("mappings", [])
    }
    with client.app.state.database.session() as session:
        assert (
            session.get(ProcessingJob, job).config_snapshot_json["printLayout"]
            == next_state["saved"]
        )


@pytest.mark.parametrize(
    "mutation", ["revision", "geometry", "add", "duplicate", "active"]
)
def test_stale_or_structural_page_edits_fail_without_mutating_draft(client, paged_draft, mutation):
    version, _, url, state, layout = paged_draft
    if mutation == "active":
        with client.app.state.database.session() as session:
            session.get(TemplateVersion, version).status = "ACTIVE"
            session.commit()
    before = client.get(f"/api/v1/template-versions/{version}/layout").json()
    if mutation == "geometry":
        layout["cells"][0]["x"] += 1
    elif mutation == "add":
        added = copy.deepcopy(layout["cells"][0])
        added["id"] = "manual_added"
        layout["cells"].append(added)
    elif mutation == "duplicate":
        layout["cells"].append(copy.deepcopy(layout["cells"][0]))
    revision = "0" * 64 if mutation == "revision" else state["revision"]
    response = client.put(url + "/authoring", params={"base_revision": revision},
                          json=layout)
    assert response.status_code == (422 if mutation == "duplicate" else 409), response.text
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


def merge_cells(layout, ids):
    result = copy.deepcopy(layout)
    parts = [c for c in result["cells"] if c["id"] in ids]
    assert len(parts) == len(ids)
    x, y = min(c["x"] for c in parts), min(c["y"] for c in parts)
    parent = {**parts[0], "id": "merged_test", "role": "input", "source": "manual",
              "x": x, "y": y,
              "width": max(c["x"] + c["width"] for c in parts) - x,
              "height": max(c["y"] + c["height"] for c in parts) - y}
    result["cells"] = [c for c in result["cells"] if c["id"] not in ids] + [parent]
    result["cellMerges"] = [{"id": "merge_test", "parentCellId": parent["id"],
                             "direction": ("horizontal" if parts[0]["y"] == parts[1]["y"]
                                           else "vertical"),
                             "parts": [{"cell": c, "partType": "input", "literal": ""}
                                       for c in parts]}]
    return result


@pytest.mark.parametrize("ids,page_index,expected_range", [
    (["xlsx_B3", "xlsx_C3"], 0, "B3:C3"),
    (["xlsx_B33", "xlsx_C33"], 1, "B33:C33"),
    (["xlsx_C33", "xlsx_C34"], 1, "C33:C34"),
])
def test_page_merge_save_reload_and_unmerge_preserve_identity(
    client, paged_draft, ids, page_index, expected_range,
):
    version, _, url, state, layout = paged_draft
    proposed = merge_cells(layout, ids)
    historical = {"templateSnapshot": {"old": True}, "fieldMappings": [{"mappingId": "old"}]}
    with client.app.state.database.session() as session:
        old_job = ProcessingJob(job_type="EXTRACT_DOCUMENT", mode="SPREADSHEET_TEMPLATE",
                                status="REVIEW_REQUIRED", template_version_id=version,
                                request_hash="e" * 64, config_snapshot_json=historical)
        session.add(old_job)
        session.commit()
        old_id = old_job.id
    response = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                          json=proposed)
    assert response.status_code == 200, response.text
    result = response.json()
    reloaded = client.get(url).json()
    assert reloaded == result["state"]
    page = reloaded["saved"]["pages"][page_index]
    field = next(f for f in page["fields"] if f["cellId"] == "merged_test")
    assert field["sourceRange"] == expected_range
    assert {p["cellId"] for p in field["sourceParts"]} == set(ids)
    assert field["rectangle"] == transform_rect(field, page["transform"])
    for old, new in zip(state["saved"]["pages"], reloaded["saved"]["pages"], strict=True):
        for key in ("range", "transform", "referenceImage", "cells"):
            assert new[key] == old[key]
        if new["page"] != page["page"]:
            # The fixture's old mapping is intentionally replaced by the draft layout save.
            assert [{k: v for k, v in f.items() if k != "bindings"} for f in new["fields"]] == [
                {k: v for k, v in f.items() if k != "bindings"} for f in old["fields"]]
    saved_layout = result["layout"]
    parts = saved_layout["cellMerges"][0]["parts"]
    assert parts == proposed["cellMerges"][0]["parts"]
    # Meaning assignment to the parent survives the same page authoring path.
    meaning = copy.deepcopy(saved_layout)
    meaning["relations"] = [{"id": "merged_meaning", "inputCellId": "merged_test",
                             "labelCellIds": [], "axis": "horizontal", "order": 1,
                             "targetPath": "document.unifiedEarTag"}]
    assigned = client.put(url + "/authoring", params={"base_revision": reloaded["revision"]},
                          json=meaning)
    assert assigned.status_code == 200, assigned.text
    bound = next(f for f in assigned.json()["state"]["saved"]["pages"][page_index]["fields"]
                 if f["cellId"] == "merged_test")
    assert bound["bindings"][0]["mappingId"] == "layout_merged_test"
    assert bound["bindings"][0]["targetPath"] == "document.unifiedEarTag"
    restored = copy.deepcopy(saved_layout)
    restored["cells"] = [c for c in restored["cells"] if c["id"] != "merged_test"] + [
        p["cell"] for p in parts]
    restored["cellMerges"] = []
    unmerged = client.put(url + "/authoring",
                          params={"base_revision": assigned.json()["state"]["revision"]},
                          json=restored)
    assert unmerged.status_code == 200, unmerged.text
    assert {c["id"]: c for c in unmerged.json()["layout"]["cells"]} == {
        c["id"]: c for c in layout["cells"]}
    with client.app.state.database.session() as session:
        assert session.get(ProcessingJob, old_id).config_snapshot_json == historical
    assert not client.get(url + "/qualification").json()["qualified"]


@pytest.mark.parametrize("mutation", ["cross-page", "gap", "parent", "part", "duplicate-part"])
def test_invalid_merge_is_rejected_before_layout_config_or_plan_write(
    client, paged_draft, mutation,
):
    version, _, url, state, layout = paged_draft
    ids = (["xlsx_C31", "xlsx_C32"] if mutation == "cross-page" else
           ["xlsx_B3", "xlsx_D3"] if mutation == "gap" else ["xlsx_B3", "xlsx_C3"])
    proposed = merge_cells(layout, ids)
    if mutation == "parent":
        proposed["cells"][-1]["x"] += 1
    elif mutation == "part":
        proposed["cellMerges"][0]["parts"][0]["cell"]["x"] += 1
    elif mutation == "duplicate-part":
        proposed["cells"].append(copy.deepcopy(proposed["cellMerges"][0]["parts"][0]["cell"]))
    with client.app.state.database.session() as session:
        path = session.get(TemplateVersion, version).config_relative_path
    config_before = client.app.state.storage.read_bytes(path)
    response = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                          json=proposed)
    assert response.status_code in (409, 422), response.text
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == layout
    assert client.get(url).json() == state
    assert client.app.state.storage.read_bytes(path) == config_before


def test_merge_on_all_four_pages_and_delete_merged_parent(client, paged_draft):
    version, _, url, state, layout = paged_draft
    response = client.put(url, json={"baseRevision": state["revision"],
                                     "breakAfterRows": [15, 31, 47]})
    assert response.status_code == 200, response.text
    state = response.json()
    for index, row in enumerate([3, 20, 33, 50]):
        proposed = merge_cells(layout, [f"xlsx_B{row}", f"xlsx_C{row}"])
        response = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                              json=proposed)
        assert response.status_code == 200, response.text
        merged = response.json()
        assert len(merged["state"]["saved"]["pages"]) == 4
        assert [p["page"] for p in merged["state"]["saved"]["pages"]
                if any(f["cellId"] == "merged_test" for f in p["fields"])] == [index + 1]
        # Undo the merge from its stored parts, retaining the same four pages.
        response = client.put(url + "/authoring",
                              params={"base_revision": merged["state"]["revision"]}, json=layout)
        assert response.status_code == 200, response.text
        state = response.json()["state"]
    proposed = merge_cells(layout, ["xlsx_B50", "xlsx_C50"])
    response = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                          json=proposed).json()
    deleted = response["layout"]
    deleted["cells"] = [c for c in deleted["cells"] if c["id"] != "merged_test"]
    deleted["cellMerges"] = []
    result = client.put(url + "/authoring",
                        params={"base_revision": response["state"]["revision"]}, json=deleted)
    assert result.status_code == 200, result.text
    assert not {"merged_test", "xlsx_B50", "xlsx_C50"} & {
        f["cellId"] for p in result.json()["state"]["saved"]["pages"] for f in p["fields"]}


def test_fork_keeps_merged_page_sources_owned_by_new_version(client, paged_draft):
    version_id, _, url, state, layout = paged_draft
    proposed = merge_cells(layout, ["xlsx_B33", "xlsx_C33"])
    response = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                          json=proposed)
    assert response.status_code == 200, response.text
    saved = response.json()
    database, storage = client.app.state.database, client.app.state.storage
    with database.session() as session:
        version = session.get(TemplateVersion, version_id)
        version.status = "ACTIVE"  # version isolation fixture, not an activation bypass
        session.get(Template, version.template_id).active_version_id = version_id
        session.commit()
    fork = TemplateVersionService(database, storage).edit(version_id)
    fork_url = f"/api/v1/template-versions/{fork.id}/print-layout"
    fork_state = client.get(fork_url).json()
    assert fork_state["source"]["unsupported"] == []
    field = next(f for f in fork_state["saved"]["pages"][1]["fields"] if f.get("sourceParts"))
    assert field["cellId"] != "merged_test"
    assert all(p["cellId"] not in {"xlsx_B33", "xlsx_C33"} for p in field["sourceParts"])
    assert {p["sourceRange"] for p in field["sourceParts"]} == {"B33:B33", "C33:C33"}
    assert client.get(url).json()["saved"] == saved["state"]["saved"]


def test_imported_excel_merged_ranges_keep_complete_source_parts(client, monkeypatch, request):
    original_workbook = draft_workbook

    def source_with_merges():
        book = load_workbook(BytesIO(original_workbook()))
        book.active.merge_cells("B33:C33")
        book.active.merge_cells("B34:C34")
        output = BytesIO()
        book.save(output)
        return output.getvalue()

    monkeypatch.setattr(__name__ + ".draft_workbook", source_with_merges)
    version, _, url, state, layout = request.getfixturevalue("paged_draft")
    proposed = merge_cells(layout, ["xlsx_B33", "xlsx_B34"])
    response = client.put(url + "/authoring", params={"base_revision": state["revision"]},
                          json=proposed)
    assert response.status_code == 200, response.text
    field = next(f for f in response.json()["state"]["saved"]["pages"][1]["fields"]
                 if f["cellId"] == "merged_test")
    assert field["sourceRange"] == "B33:C34"
    assert field["sourceParts"] == [{"cellId": "xlsx_B33", "sourceRange": "B33:C33"},
                                     {"cellId": "xlsx_B34", "sourceRange": "B34:C34"}]
    # Version copying changes IDs, while original merged-range coverage remains exact.
    database, storage = client.app.state.database, client.app.state.storage
    with database.session() as session:
        original = session.get(TemplateVersion, version)
        original.status = "ACTIVE"
        session.get(Template, original.template_id).active_version_id = version
        session.commit()
    fork = TemplateVersionService(database, storage).edit(version)
    fork_url = f"/api/v1/template-versions/{fork.id}/print-layout"
    fork_state = client.get(fork_url).json()
    fork_field = next(f for f in fork_state["source"]["fields"] if f.get("sourceParts"))
    assert fork_field["sourceRange"] == "B33:C34"
    assert {p["sourceRange"] for p in fork_field["sourceParts"]} == {"B33:C33", "B34:C34"}
    fork_layout = client.get(f"/api/v1/template-versions/{fork.id}/layout").json()
    fork_saved = client.put(fork_url + "/authoring",
                            params={"base_revision": fork_state["revision"]}, json=fork_layout)
    assert fork_saved.status_code == 200, fork_saved.text
    with database.session() as session:
        session.get(TemplateVersion, version).status = "DRAFT"  # restore fixture for unmerge
        session.commit()
    restored = client.put(url + "/authoring",
                          params={"base_revision": response.json()["state"]["revision"]},
                          json=layout)
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/template-versions/{version}/layout").json() == layout
