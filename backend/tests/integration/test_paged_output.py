import copy
import hashlib
from io import BytesIO
from urllib.parse import quote

from openpyxl import load_workbook

from app.core.errors import AppError
from app.db.models import PagedConfirmation, PagedReview, ProcessingJob, TemplateVersion
from app.domain.paged_review import PageReopen
from app.services.paged_review_service import PagedReviewService
from app.services.print_layout_service import digest
from tests.integration.test_job_pages import pages_case  # noqa: F401
from tests.integration.test_paged_confirmation import body, ready
from tests.integration.test_paged_review import edit_payload, reviewed_case  # noqa: F401
from tests.integration.test_print_layout_api import print_template  # noqa: F401


def test_confirmed_all_page_output_uses_owned_snapshot_and_never_changes_results(
    client, reviewed_case  # noqa: F811
):
    service, job, url, initial = reviewed_case
    endpoint = url + "/output.xlsx"
    assert client.get(endpoint).status_code == 422
    assert client.get(endpoint, params={"revision": 1}).status_code == 409
    current = ready(client, url, initial)
    page = current["pages"][0]
    current = client.post(
        f"{url}/{page['pageId']}/corrections",
        json=edit_payload(
            current["revision"], page["fields"][0]["mappingId"], "=00123 & <任意名>\n複数行"
        ),
    ).json()
    confirmed = client.post(url + "/confirm", json=body(current)).json()
    assert confirmed["confirmed"] and confirmed["outputAvailable"]
    revision = confirmed["revision"]
    with service.database.session() as session:
        accepted = copy.deepcopy(session.get(ProcessingJob, job.id).config_snapshot_json)
        snapshot = accepted["jobPages"]["documentSnapshot"]
        original_review = copy.deepcopy(session.get(PagedReview, job.id).current_pages_json)
        original_raw = copy.deepcopy(session.get(PagedReview, job.id).raw_pages_json)
        original_confirmation = copy.deepcopy(
            session.get(PagedConfirmation, (job.id, revision)).pages_json
        )
        source = service.storage.read_bytes(snapshot["sourceWorkbook"]["relativePath"])
        version = session.get(TemplateVersion, job.template_version_id)
        version.layout_json = {"currentVersionChanged": True}
        session.commit()
    response = client.get(endpoint, params={"revision": revision})
    assert response.status_code == 200, response.text
    assert response.headers["x-result-revision"] == str(revision)
    assert response.headers["cache-control"] == "no-store"
    assert (
        quote(confirmed["documentName"] + ".xlsx", safe="")
        in response.headers["content-disposition"]
    )
    assert response.headers["etag"] == '"' + hashlib.sha256(response.content).hexdigest() + '"'
    output = load_workbook(BytesIO(response.content))
    sheet = output[snapshot["spreadsheetOutput"]["sourceSheetName"]]
    for binding in snapshot["outputBindings"]:
        page = next(p for p in confirmed["pages"] if p["pageId"] == binding["pageId"])
        field = next(f for f in page["fields"] if f["mappingId"] == binding["mappingId"])
        assert sheet[binding["anchorCell"]].value == field["reading"]["normalized"]
        assert sheet[binding["anchorCell"]].data_type == "s"
    assert len(snapshot["outputBindings"]) == 2
    assert client.get(endpoint, params={"revision": revision}).content == response.content
    service.storage.write_bytes(snapshot["sourceWorkbook"]["relativePath"], b"changed owned source")
    invalid = client.get(endpoint, params={"revision": revision})
    assert invalid.status_code == 409 and invalid.json()["code"] == "PAGE_OUTPUT_SOURCE_CHANGED"
    assert client.get(url).json() == confirmed
    service.storage.write_bytes(snapshot["sourceWorkbook"]["relativePath"], source)
    with service.database.session() as session:
        stored = session.get(PagedReview, job.id)
        assert (
            stored.raw_pages_json == original_raw and stored.current_pages_json == original_review
        )
        assert (
            session.get(PagedConfirmation, (job.id, revision)).pages_json == original_confirmation
        )
        assert session.get(ProcessingJob, job.id).config_snapshot_json == accepted
    reopened = client.post(url + "/reopen", json={"baseRevision": revision}).json()
    assert not reopened["outputAvailable"]
    assert client.get(endpoint, params={"revision": revision}).status_code == 409
    new_confirmation = client.post(url + "/confirm", json=body(reopened)).json()
    assert new_confirmation["revision"] > revision and new_confirmation["outputAvailable"]
    assert client.get(endpoint, params={"revision": revision}).status_code == 409
    assert (
        client.get(endpoint, params={"revision": new_confirmation["revision"]}).status_code == 200
    )


def test_generation_failure_or_concurrent_reopen_never_publishes_stale_output(
    client, reviewed_case, monkeypatch  # noqa: F811
):
    import app.services.paged_output_service as module

    service, job, url, initial = reviewed_case
    confirmed = client.post(url + "/confirm", json=body(ready(client, url, initial))).json()
    revision = confirmed["revision"]
    original = module.render_workbook

    def failed(*args):
        raise AppError(status=409, code="OUTPUT_TEST_FAILURE", title="fixture", detail="fixture")

    monkeypatch.setattr(module, "render_workbook", failed)
    response = client.get(url + "/output.xlsx", params={"revision": revision})
    assert response.status_code == 409 and response.json()["code"] == "OUTPUT_TEST_FAILURE"
    assert client.get(url).json() == confirmed

    def concurrent(*args):
        content = original(*args)
        PagedReviewService(service.database).reopen(job.id, PageReopen(baseRevision=revision))
        return content

    monkeypatch.setattr(module, "render_workbook", concurrent)
    response = client.get(url + "/output.xlsx", params={"revision": revision})
    assert response.status_code == 409 and response.json()["code"] == "PAGE_OUTPUT_REVISION_CHANGED"
    after = client.get(url).json()
    assert not after["confirmed"] and after["pages"] == confirmed["pages"]


def test_unversioned_output_snapshot_is_not_silently_upgraded(client, reviewed_case):  # noqa: F811
    service, job, url, initial = reviewed_case
    with service.database.session() as session:
        row = session.get(ProcessingJob, job.id)
        snapshot = copy.deepcopy(row.config_snapshot_json)
        snapshot["jobPages"]["documentSnapshot"].pop("outputAdapter")
        snapshot["jobPagesSha256"] = digest(snapshot["jobPages"])
        row.config_snapshot_json = snapshot
        stored = session.get(PagedReview, job.id)
        stored.snapshot_sha256 = snapshot["jobPagesSha256"]
        session.commit()
    confirmed = client.post(url + "/confirm", json=body(ready(client, url, initial))).json()
    assert confirmed["confirmed"] and not confirmed["outputAvailable"]
    response = client.get(url + "/output.xlsx", params={"revision": confirmed["revision"]})
    assert response.status_code == 409 and response.json()["code"] == "PAGE_OUTPUT_ADAPTER_REQUIRED"
    with service.database.session() as session:
        assert session.get(ProcessingJob, job.id).config_snapshot_json == snapshot
