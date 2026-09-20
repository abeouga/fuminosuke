import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import event, select

from app.core.errors import AppError
from app.db.models import PagedConfirmation, PagedReview, PagedReviewRevision, ProcessingJob
from app.domain.paged_review import PageConfirmation, PageCorrection
from app.services.paged_review_service import PagedReviewService
from tests.integration.test_job_pages import pages_case  # noqa: F401
from tests.integration.test_paged_review import edit_payload, reviewed_case  # noqa: F401
from tests.integration.test_print_layout_api import print_template  # noqa: F401


def body(result):
    return {
        "baseRevision": result["revision"],
        "reviewedPageIds": [p["pageId"] for p in result["pages"]],
        "documentName": "確認用の任意名",
    }


def ready(client, url, result):
    page = result["pages"][1]
    response = client.post(
        f"{url}/{page['pageId']}/corrections",
        json=edit_payload(result["revision"], page["fields"][0]["mappingId"], "1234567890"),
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_all_page_confirmation_reopen_and_reconfirmation_preserve_every_snapshot(
    client, reviewed_case  # noqa: F811 - imported pytest fixture
):
    service, job, url, initial = reviewed_case
    with service.database.session() as session:
        raw = copy.deepcopy(session.get(PagedReview, job.id).raw_pages_json)
        accepted = copy.deepcopy(session.get(ProcessingJob, job.id).config_snapshot_json)
    for ids in (
        [initial["pages"][0]["pageId"]],
        ["unknown"],
        [p["pageId"] for p in initial["pages"]] * 2,
    ):
        response = client.post(url + "/confirm", json={**body(initial), "reviewedPageIds": ids})
        assert response.status_code == 409 and response.json()["code"] == "PAGE_REVIEW_INCOMPLETE"
    response = client.post(url + "/confirm", json=body(initial))
    assert response.status_code == 409 and response.json()["code"] == "UNRESOLVED_REVIEW_ITEMS"
    reviewed = ready(client, url, initial)
    first = client.post(url + "/confirm", json=body(reviewed))
    assert first.status_code == 200, first.text
    first_result = first.json()
    assert first_result["confirmed"] and not first_result["confirmAvailable"]
    assert first_result["revision"] == 3
    assert first_result["pages"] == reviewed["pages"]
    assert first_result["documentName"] == "確認用の任意名"
    assert first_result["outputAvailable"]
    assert client.get(url).json() == first_result
    page = reviewed["pages"][0]
    locked = client.post(
        f"{url}/{page['pageId']}/corrections", json=edit_payload(3, page["fields"][0]["mappingId"])
    )
    assert locked.status_code == 409 and locked.json()["code"] == "PAGE_RESULT_LOCKED"
    reopened = client.post(url + "/reopen", json={"baseRevision": 3}).json()
    assert not reopened["confirmed"] and reopened["revision"] == 4
    assert reopened["pages"] == reviewed["pages"]
    stale = client.post(url + "/confirm", json=body(reviewed))
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_CONFLICT"
    changed = client.post(
        f"{url}/{page['pageId']}/corrections",
        json=edit_payload(4, page["fields"][0]["mappingId"], "再編集後の値"),
    ).json()
    final = client.post(url + "/confirm", json=body(changed))
    assert final.status_code == 200 and final.json()["revision"] == 6
    with service.database.session() as session:
        stored = session.get(PagedReview, job.id)
        confirmations = list(
            session.scalars(
                select(PagedConfirmation)
                .where(PagedConfirmation.job_id == job.id)
                .order_by(PagedConfirmation.revision)
            )
        )
        assert [c.revision for c in confirmations] == [3, 6]
        assert confirmations[0].pages_json != confirmations[1].pages_json
        assert confirmations[1].pages_json == stored.current_pages_json
        assert stored.raw_pages_json == raw
        assert session.get(ProcessingJob, job.id).config_snapshot_json == accepted
        history = list(
            session.scalars(
                select(PagedReviewRevision)
                .where(PagedReviewRevision.job_id == job.id)
                .order_by(PagedReviewRevision.revision)
            )
        )
        assert [h.source for h in history] == [
            "OCR",
            "USER_EDIT",
            "CONFIRM",
            "REOPEN",
            "USER_EDIT",
            "CONFIRM",
        ]
        assert history[2].pages_json == confirmations[0].pages_json == history[3].pages_json
        session.delete(confirmations[1])
        session.commit()
    missing = client.get(url)
    assert missing.status_code == 409 and missing.json()["code"] == "CONFIRMED_RESULT_MISSING"


def test_confirmation_is_atomic_and_conflicting_correction_cannot_overwrite(client, reviewed_case):  # noqa: F811
    service, job, url, initial = reviewed_case
    before = ready(client, url, initial)
    reviews = PagedReviewService(service.database)
    confirmation = PageConfirmation.model_validate(body(before))

    def fail_snapshot(connection, cursor, statement, parameters, context, many):
        if statement.startswith("INSERT INTO paged_confirmations"):
            raise RuntimeError("injected confirmation failure")

    event.listen(service.database.engine, "before_cursor_execute", fail_snapshot)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            reviews.confirm(job.id, confirmation)
    finally:
        event.remove(service.database.engine, "before_cursor_execute", fail_snapshot)
    assert client.get(url).json() == before
    barrier = Barrier(2)

    def mutate(action):
        barrier.wait(timeout=5)
        try:
            if action == "confirm":
                return reviews.confirm(job.id, confirmation)["revision"]
            page = before["pages"][0]
            return reviews.correct(
                job.id,
                page["pageId"],
                PageCorrection.model_validate(edit_payload(2, page["fields"][0]["mappingId"])),
            )["revision"]
        except AppError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(mutate, ["confirm", "correct"]))
    assert outcomes.count(3) == 1
    assert any(o in {"REVISION_CONFLICT", "PAGE_RESULT_LOCKED"} for o in outcomes)
    after = client.get(url).json()
    assert after["revision"] == 3
    with service.database.session() as session:
        job_row = session.get(ProcessingJob, job.id)
        assert after["confirmed"] == (job_row.status == "CONFIRMED")
        rows = list(
            session.scalars(select(PagedConfirmation).where(PagedConfirmation.job_id == job.id))
        )
        assert len(rows) == int(after["confirmed"])
