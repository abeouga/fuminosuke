import copy
from io import BytesIO

import pytest
import yaml
from openpyxl import load_workbook

from app.core.errors import AppError
from app.db.models import ProcessingJob, TemplateVersion
from app.domain.paged_document_metadata import ADAPTER
from app.domain.template_config import TemplateConfig, parse_template_config
from app.services.print_layout_service import digest
from tests.integration.test_job_pages import accept, execution, pages_case  # noqa: F401
from tests.integration.test_paged_confirmation import body
from tests.integration.test_paged_review import edit_payload
from tests.integration.test_print_layout_api import print_template  # noqa: F401


@pytest.fixture
def metadata_case(pages_case):  # noqa: F811
    service, manifest, images = pages_case
    with service.database.session() as session:
        version = session.get(TemplateVersion, manifest["templateVersionId"])
        raw = parse_template_config(
            service.storage.read_bytes(version.config_relative_path).decode()
        ).model_dump(mode="json", by_alias=True)
        raw["documentIdentityFields"] = [
            {
                "mappingId": "layout_xlsx_C2",
                "targetPath": "document.cattleName",
                "label": "任意識別名",
                "matchRole": "confirmation",
                "displayRole": "title",
                "matchGroupId": "both-pages",
                "matchGroupLabel": "二項目",
                "componentOrder": 0,
            },
            {
                "mappingId": "layout_xlsx_C32",
                "targetPath": "document.unifiedEarTag",
                "label": "任意番号",
                "matchRole": "confirmation",
                "displayRole": "none",
                "matchGroupId": "both-pages",
                "matchGroupLabel": "二項目",
                "componentOrder": 1,
                "valueType": "digits",
                "exactLength": 10,
            },
        ]
        raw["documentName"] = {
            "fieldPaths": ["document.unifiedEarTag", "document.cattleName"],
            "separator": "／",  # noqa: RUF001 - intentional fullwidth filename separator
            "prefix": "記録-",
            "suffix": "-控",
        }
        config = TemplateConfig.model_validate(raw)
        service.storage.write_text(
            version.config_relative_path,
            yaml.safe_dump(config.model_dump(mode="json", by_alias=True), allow_unicode=True),
        )
        manifest = {**manifest, "templateRevision": service.revision(version)}
    return service, manifest, images


def test_document_fields_cross_pages_through_request_correction_confirmation_and_output(
    client, metadata_case, monkeypatch
):
    service, manifest, _ = metadata_case
    job = accept(metadata_case, monkeypatch)
    original = copy.deepcopy(job.config_snapshot_json)
    assert original["jobPages"]["documentSnapshot"]["documentMetadataAdapter"] == ADAPTER
    executor = execution(service)
    requests = []
    provider = executor.workflow._create_provider("mock")

    class Capture:
        async def extract(self, request):
            requests.append(request)
            return await provider.extract(request)

    executor.workflow._create_provider = lambda name: Capture()
    # Current version changes must not alter the accepted name or identity hints.
    with service.database.session() as session:
        version = session.get(TemplateVersion, manifest["templateVersionId"])
        service.storage.write_text(version.config_relative_path, "changed after acceptance")
    executor.execute(job.id)
    assert len(requests) == 2
    for request, accepted in zip(requests, original["jobPages"]["pages"], strict=True):
        region = request.regions[0]
        identity = accepted["documentContext"]["identityFields"][0]
        assert region.is_document_identity
        assert region.region_id == identity["mappingId"]
        assert region.target_path == identity["targetPath"]
        assert region.identity_value_type == identity["valueType"]
        assert region.identity_exact_length == identity["exactLength"]
    with service.database.session() as session:
        session.get(ProcessingJob, job.id).status = "REVIEW_REQUIRED"
        session.commit()
    url = f"/api/v1/ocr-jobs/{job.id}/page-review"
    response = client.get(url)
    assert response.status_code == 200, response.text
    current = response.json()
    assert current["documentMetadata"]["proposedName"] is None
    original_metadata = current["documentMetadata"]["identityFields"]
    assert [f["pageId"] for f in original_metadata] == [p["pageId"] for p in current["pages"]]
    crops = [
        (f["mappingId"], f["cropUrl"], f["reading"]["boundingBox"])
        for p in current["pages"]
        for f in p["fields"]
    ]
    for page, value in zip(current["pages"], ("任意名", "0123456789"), strict=True):
        response = client.post(
            f"{url}/{page['pageId']}/corrections",
            json=edit_payload(current["revision"], page["fields"][0]["mappingId"], value),
        )
        assert response.status_code == 200, response.text
        current = response.json()
    assert current["documentMetadata"]["proposedName"] == "記録-0123456789／任意名-控"  # noqa: RUF001
    confirm_body = body(current)
    confirm_body["documentName"] = current["documentMetadata"]["proposedName"]
    response = client.post(url + "/confirm", json=confirm_body)
    assert response.status_code == 200, response.text
    confirmed = response.json()
    assert confirmed["documentName"] == "記録-0123456789／任意名-控"  # noqa: RUF001
    assert confirmed["documentMetadata"]["identityFields"] == original_metadata
    assert [
        (f["mappingId"], f["cropUrl"], f["reading"]["boundingBox"])
        for p in confirmed["pages"]
        for f in p["fields"]
    ] == crops
    response = client.get(url + "/output.xlsx", params={"revision": confirmed["revision"]})
    assert response.status_code == 200, response.text
    worksheet = load_workbook(BytesIO(response.content)).active
    assert worksheet["C2"].value == "任意名" and worksheet["C32"].value == "0123456789"
    with service.database.session() as session:
        assert session.get(ProcessingJob, job.id).config_snapshot_json == original


def test_missing_or_cross_page_document_context_is_rejected_before_provider(
    metadata_case, monkeypatch
):
    service, _, _ = metadata_case
    job = accept(metadata_case, monkeypatch)
    original = copy.deepcopy(job.config_snapshot_json)
    for change in ("missing", "wrong_page", "unknown_adapter", "format"):
        snapshot = copy.deepcopy(original)
        bundle = snapshot["jobPages"]
        if change == "missing":
            bundle["pages"][0].pop("documentContext")
        elif change == "wrong_page":
            bundle["pages"][0]["documentContext"] = bundle["pages"][1]["documentContext"]
        elif change == "unknown_adapter":
            bundle["documentSnapshot"]["documentMetadataAdapter"] = "unknown"
        else:
            bundle["pages"][1]["documentContext"]["identityFields"][0]["exactLength"] = 9
        snapshot["jobPagesSha256"] = digest(bundle)
        job.config_snapshot_json = snapshot
        with service.database.session() as session, pytest.raises(AppError) as error:
            service.validate(session, job)
        assert error.value.code == "JOB_PAGE_SNAPSHOT_INVALID"
    with service.database.session() as session:
        assert session.get(ProcessingJob, job.id).config_snapshot_json == original
