import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, select

from app.core.errors import AppError
from app.db.models import OcrResult, PagedReview, PagedReviewRevision, ProcessingJob
from app.domain.paged_review import PageCorrection
from app.services.paged_review_service import PagedReviewService
from tests.integration.test_job_pages import accept, execution, pages_case  # noqa: F401
from tests.integration.test_print_layout_api import print_template  # noqa: F401


@pytest.fixture
def reviewed_case(client, pages_case, monkeypatch):  # noqa: F811
    service, _, _ = pages_case
    job = accept(pages_case, monkeypatch)
    execution(service).execute(job.id)
    with service.database.session() as session:
        current = session.get(ProcessingJob, job.id)
        current.status = "REVIEW_REQUIRED"
        session.commit()
    url = f"/api/v1/ocr-jobs/{job.id}/page-review"
    response = client.get(url)
    assert response.status_code == 200, response.text
    return service, job, url, response.json()


def edit_payload(revision, mapping_id, value="任意の補正文字"):
    return {
        "baseRevision": revision,
        "fields": [
            {
                "mappingId": mapping_id,
                "raw": value,
                "normalized": value,
                "status": "readable",
                "alternatives": [],
                "reviewReason": None,
            }
        ],
    }


def test_page_correction_preserves_identity_other_page_and_raw(client, reviewed_case):
    service, job, url, before = reviewed_case
    first, second = before["pages"]
    assert before["reviewStructure"] == job.config_snapshot_json["jobPages"][
        "documentSnapshot"
    ]["reviewStructure"]
    mapping = second["fields"][0]
    payload = edit_payload(before["revision"], mapping["mappingId"])
    with service.database.session() as session:
        raw = copy.deepcopy(session.get(PagedReview, job.id).raw_pages_json)
        snapshot = copy.deepcopy(session.get(ProcessingJob, job.id).config_snapshot_json)
    response = client.post(f"{url}/{second['pageId']}/corrections", json=payload)
    assert response.status_code == 200, response.text
    after = response.json()
    assert after["revision"] == 2 and not after["confirmed"]
    assert after["pages"][0] == first
    updated = after["pages"][1]["fields"][0]
    assert updated["reading"]["raw"] == "任意の補正文字"
    for key in ("mappingId", "targetPath", "cropUrl"):
        assert updated[key] == mapping[key]
    for key in ("sourceRegionId", "boundingBox"):
        assert updated["reading"][key] == mapping["reading"][key]
    assert second["pageId"] in updated["cropUrl"]
    assert client.get(updated["cropUrl"]).status_code == 200
    assert second["pageId"] in after["pages"][1]["pageImageUrl"]
    assert client.get(after["pages"][1]["pageImageUrl"]).status_code == 200
    assert client.get(url).json() == after
    with service.database.session() as session:
        stored = session.get(PagedReview, job.id)
        assert stored.raw_pages_json == raw
        assert session.get(ProcessingJob, job.id).config_snapshot_json == snapshot
        assert session.scalar(select(OcrResult).where(OcrResult.job_id == job.id)) is None
        revisions = list(
            session.scalars(
                select(PagedReviewRevision)
                .where(PagedReviewRevision.job_id == job.id)
                .order_by(PagedReviewRevision.revision)
            )
        )
        assert [(r.revision, r.source, r.page_id) for r in revisions] == [
            (1, "OCR", None),
            (2, "USER_EDIT", second["pageId"]),
        ]
        assert revisions[0].pages_json == raw
        assert revisions[1].pages_json == stored.current_pages_json
    conflict = client.post(f"{url}/{second['pageId']}/corrections", json=payload)
    assert conflict.status_code == 409 and conflict.json()["code"] == "REVISION_CONFLICT"
    assert client.get(url).json() == after
    # Same successful OCR on retry is idempotent, never a correction reset.
    results = client.get(f"/api/v1/ocr-jobs/{job.id}/page-results").json()["pages"]
    PagedReviewService(service.database).initialize(job.id, results)
    assert client.get(url).json() == after


@pytest.mark.parametrize("kind", ["other_page", "duplicate", "identity", "crop", "unknown_page"])
def test_reject_cross_page_or_client_identity_changes(client, reviewed_case, kind):
    _, _, url, before = reviewed_case
    first, second = before["pages"]
    target = first["pageId"]
    payload = edit_payload(1, first["fields"][0]["mappingId"])
    if kind == "other_page":
        payload["fields"][0]["mappingId"] = second["fields"][0]["mappingId"]
    elif kind == "duplicate":
        payload["fields"].append(copy.deepcopy(payload["fields"][0]))
    elif kind == "identity":
        payload["fields"][0]["sourceRegionId"] = "other"
    elif kind == "crop":
        payload["fields"][0]["boundingBox"] = {"x": 0, "y": 0, "width": 3, "height": 3}
    else:
        target = "unknown-page"
    response = client.post(f"{url}/{target}/corrections", json=payload)
    assert response.status_code == (
        422 if kind in {"identity", "crop"} else 404 if kind == "unknown_page" else 409
    )
    assert client.get(url).json() == before


@pytest.mark.parametrize("kind", ["missing", "duplicate", "input", "snapshot", "failed", "fields"])
def test_result_publication_rejects_wrong_ownership(client, reviewed_case, kind):
    service, job, url, before = reviewed_case
    results = client.get(f"/api/v1/ocr-jobs/{job.id}/page-results").json()["pages"]
    if kind == "missing":
        results.pop()
    elif kind == "duplicate":
        results.append(copy.deepcopy(results[0]))
    elif kind == "input":
        results[0]["inputId"] = results[1]["inputId"]
    elif kind == "snapshot":
        results[0]["pageSnapshotSha256"] = "0" * 64
    elif kind == "failed":
        results[0]["status"] = "FAILED"
    else:
        results[0]["fields"] = results[1]["fields"]
    with pytest.raises(AppError) as error:
        PagedReviewService(service.database).initialize(job.id, results)
    assert error.value.code == "PAGE_RESULT_CHANGED"
    assert client.get(url).json() == before


def test_failed_job_cannot_edit(client, reviewed_case):
    service, job, url, before = reviewed_case
    with service.database.session() as session:
        stored_job = session.get(ProcessingJob, job.id)
        stored_job.status = "FAILED"
        session.commit()
    page = before["pages"][0]
    response = client.post(
        f"{url}/{page['pageId']}/corrections", json=edit_payload(1, page["fields"][0]["mappingId"])
    )
    assert response.status_code == 409 and response.json()["code"] == "PAGE_RESULT_NOT_READY"
    with service.database.session() as session:
        assert session.get(PagedReview, job.id).revision == 1


def test_untrusted_readings_never_become_initial_review(client, reviewed_case):
    service, job, _, before = reviewed_case
    original = client.get(f"/api/v1/ocr-jobs/{job.id}/page-results").json()["pages"]
    with service.database.session() as session:
        session.delete(session.get(PagedReview, job.id))
        session.commit()
    for kind in ("region", "crop", "foreign_value"):
        results = copy.deepcopy(original)
        reading = results[0]["extraction"]["result"]["document"]["cattleName"]
        if kind == "region":
            reading["sourceRegionId"] = "wrong-region"
        elif kind == "crop":
            reading["boundingBox"]["x"] += 1
        else:
            reading = results[0]["extraction"]["result"]["document"]["owner"]
            reading.update(raw="other page value", normalized="other page value", status="readable")
        with pytest.raises(AppError) as error:
            PagedReviewService(service.database).initialize(job.id, results)
        assert error.value.code == "PAGE_RESULT_CHANGED"
        with service.database.session() as session:
            assert session.get(PagedReview, job.id) is None
    PagedReviewService(service.database).initialize(job.id, list(reversed(original)))
    assert PagedReviewService(service.database).get(job.id) == before


def test_review_ignores_live_template_and_runtime_changes(client, reviewed_case):
    from app.db.models import TemplateVersion

    service, job, url, before = reviewed_case
    with service.database.session() as session:
        version = session.get(TemplateVersion, job.template_version_id)
        version.quality_json = {}
        service.storage.write_text(version.config_relative_path, "changed")
        session.commit()
    assert client.get(url).json() == before
    # No JobPageService.validate/runtime re-execution is used by review.
    first = before["pages"][0]
    response = client.post(
        f"{url}/{first['pageId']}/corrections",
        json=edit_payload(1, first["fields"][0]["mappingId"]),
    )
    assert response.status_code == 200, response.text


def test_revision_write_failure_rolls_back_and_concurrent_pages_cannot_overwrite(
    client, reviewed_case
):
    service, job, url, before = reviewed_case
    reviews = PagedReviewService(service.database)
    first = before["pages"][0]
    body = PageCorrection.model_validate(edit_payload(1, first["fields"][0]["mappingId"]))

    def fail_history(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO paged_review_revisions"):
            raise RuntimeError("injected history write failure")

    event.listen(service.database.engine, "before_cursor_execute", fail_history)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            reviews.correct(job.id, first["pageId"], body)
    finally:
        event.remove(service.database.engine, "before_cursor_execute", fail_history)
    assert client.get(url).json() == before
    barrier = Barrier(2)

    def change(page):
        correction = PageCorrection.model_validate(edit_payload(1, page["fields"][0]["mappingId"]))
        barrier.wait(timeout=5)
        try:
            return reviews.correct(job.id, page["pageId"], correction)["revision"]
        except AppError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(change, before["pages"]))
    assert sorted(str(value) for value in outcomes) == ["2", "REVISION_CONFLICT"]
    after = client.get(url).json()
    assert after["revision"] == 2
    assert sum(a != b for a, b in zip(after["pages"], before["pages"], strict=True)) == 1
