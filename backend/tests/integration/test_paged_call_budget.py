import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.base import Base
from app.db.models import PagedOcrBudget, PagedOcrCall, ProcessingJob
from app.db.session import Database
from app.domain.ocr_models import ReadingStatus
from app.ocr.base import OcrProviderRequest, OcrRegionInput
from app.ocr.openai_provider import OpenAIOcrProvider
from app.ocr.transcription_models import TranscriptionItem
from app.services.paged_ocr_budget import (
    PagedCallBudget,
    budget_policy,
    call_budget_plan,
    initialize_budget,
    require_call_budget_plan,
)
from app.services.print_layout_service import digest
from tests.integration.test_job_pages import accept, execution, pages_case  # noqa: F401
from tests.integration.test_print_layout_api import print_template  # noqa: F401
from tests.unit.test_openai_call_budget import _response, _settings


@pytest.fixture
def budget_case(settings_factory):
    settings = settings_factory()
    database = Database(settings.database)
    Base.metadata.create_all(database.engine)
    bundle = {
        "ocrCallBudget": budget_policy(3),
        "executionConfig": {"ocr": {"max_calls_per_job": 3}},
        "pages": [
            {"pageId": page, "ocrProvider": "openai", "ocrEnabled": True}
            for page in ("alpha", "beta")
        ],
    }
    with database.session() as session:
        job = ProcessingJob(
            job_type="EXTRACT_DOCUMENT",
            status="PROCESSING",
            attempt_number=1,
            request_hash="a" * 64,
            config_snapshot_json={"jobPages": bundle, "jobPagesSha256": digest(bundle)},
        )
        session.add(job)
        session.flush()
        initialize_budget(session, job)
        session.commit()
    yield database, settings, job
    database.dispose()


def control(case, page="alpha", attempt=1):
    database, _, job = case
    return PagedCallBudget(
        database, job.id, page, attempt, job.config_snapshot_json["jobPagesSha256"]
    )


def offline_provider(calls, *, fail=False):
    provider = object.__new__(OpenAIOcrProvider)
    provider._settings = _settings(max_calls=3, max_retries=2)

    async def send(request, model, ordinal):
        calls.append((ordinal, [region.region_id for region in request.regions]))
        if fail:
            raise provider._provider_error(504, "OCR_TIMEOUT", "fixture timeout")
        return _response(
            model,
            ordinal,
            items=[
                TranscriptionItem(
                    mapping_id=region.region_id, raw=region.region_id, status=ReadingStatus.READABLE
                )
                for region in request.regions
            ],
        )

    provider._call = send
    return provider


def request(case, page, count):
    return OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id=f"{page}_{i}",
                image_png=b"fixture-not-sent",
                target_path=f"customFields.{page}_{i}",
            )
            for i in range(count)
        ],
        call_budget=control(case, page),
    )


def test_page_two_cannot_reset_document_budget(budget_case):
    calls = []
    first = asyncio.run(offline_provider(calls).extract(request(budget_case, "alpha", 61)))
    assert first.api_call_count == 3
    with pytest.raises(AppError, match="最大呼び出し") as error:
        asyncio.run(offline_provider(calls).extract(request(budget_case, "beta", 61)))
    assert error.value.code == "OCR_CALL_BUDGET_TOO_SMALL"
    assert len(calls) == 3 and control(budget_case, "beta").remaining() == 0


def test_document_ordinals_continue_but_response_count_is_page_local(budget_case):
    calls = []
    results = [
        asyncio.run(offline_provider(calls).extract(request(budget_case, page, 1)))
        for page in ("alpha", "beta")
    ]
    assert [r.api_call_count for r in results] == [1, 1]
    assert [c[0] for c in calls] == [1, 2]
    assert [c[1] for c in calls] == [["alpha_0"], ["beta_0"]]
    assert control(budget_case).remaining() == 1


def test_failed_calls_and_new_attempt_keep_used_budget_after_reconnection(budget_case):
    database, settings, job = budget_case
    calls = []
    with pytest.raises(AppError) as error:
        asyncio.run(offline_provider(calls, fail=True).extract(request(budget_case, "alpha", 1)))
    assert error.value.code == "OCR_TIMEOUT" and len(calls) == 3
    with database.session() as session:
        session.get(ProcessingJob, job.id).attempt_number = 2
        session.commit()
    replacement = Database(settings.database)
    try:
        second = PagedCallBudget(
            replacement, job.id, "beta", 2, job.config_snapshot_json["jobPagesSha256"]
        )
        assert second.remaining() == 0 and second.reserve() is None
    finally:
        replacement.dispose()
    with database.session() as session:
        rows = session.scalars(select(PagedOcrCall).where(PagedOcrCall.job_id == job.id)).all()
        assert [(row.call_number, row.page_id, row.attempt_number) for row in rows] == [
            (1, "alpha", 1),
            (2, "alpha", 1),
            (3, "alpha", 1),
        ]


def test_parallel_connections_cannot_overreserve(budget_case):
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(
            pool.map(
                lambda n: control(budget_case, "alpha" if n % 2 else "beta").reserve(), range(12)
            )
        )
    assert sorted(n for n in reservations if n is not None) == [1, 2, 3]
    assert control(budget_case).remaining() == 0


def test_remaining_read_is_consistent_when_another_connection_reserves(budget_case, monkeypatch):
    original_get = Session.get
    intervened = False

    def interleaved_get(session, entity, *args, **kwargs):
        nonlocal intervened
        value = original_get(session, entity, *args, **kwargs)
        if entity is PagedOcrBudget and not intervened:
            intervened = True
            assert control(budget_case, "beta").reserve() == 1
        return value

    monkeypatch.setattr(Session, "get", interleaved_get)
    assert control(budget_case).remaining() == 3
    assert control(budget_case).remaining() == 2


@pytest.mark.parametrize("committed", [False, True])
def test_uncertain_reservation_commit_never_sends_or_refunds(budget_case, monkeypatch, committed):
    original_commit = Session.commit

    def broken_commit(session):
        if committed:
            original_commit(session)
        raise RuntimeError("lost commit acknowledgement")

    monkeypatch.setattr(Session, "commit", broken_commit)
    calls = []
    with pytest.raises(RuntimeError, match="lost commit acknowledgement"):
        asyncio.run(offline_provider(calls).extract(request(budget_case, "alpha", 1)))
    assert calls == []
    assert control(budget_case).remaining() == (2 if committed else 3)


def test_cancellation_after_reservation_keeps_charge_without_send(budget_case):
    persisted = control(budget_case)
    cancelled = False

    class CancelAfterReserve:
        def remaining(self):
            return persisted.remaining()

        def reserve(self):
            nonlocal cancelled
            ordinal = persisted.reserve()
            cancelled = True
            return ordinal

    calls = []
    prepared = replace(
        request(budget_case, "alpha", 1),
        call_budget=CancelAfterReserve(),
        should_cancel=lambda: cancelled,
    )
    with pytest.raises(AppError) as error:
        asyncio.run(offline_provider(calls).extract(prepared))
    assert error.value.code == "JOB_CANCELLED" and calls == []
    assert persisted.remaining() == 2


def test_pending_pages_keep_their_minimum_capacity(budget_case):
    database, _, job = budget_case
    first = PagedCallBudget(
        database, job.id, "alpha", 1, job.config_snapshot_json["jobPagesSha256"], protected_calls=2
    )
    assert first.remaining() == 1 and first.reserve() == 1
    assert first.remaining() == 0 and first.reserve() is None
    assert control(budget_case, "beta").remaining() == 2
    assert control(budget_case, "beta").reserve() == 2


@pytest.mark.parametrize(
    "count,identity_count", [(0, 0), (1, 0), (30, 0), (31, 0), (61, 0), (60, 1), (30, 30), (61, 5)]
)
def test_minimum_preflight_matches_existing_batch_plan(count, identity_count):
    provider = object.__new__(OpenAIOcrProvider)
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id=str(i), image_png=b"fixture", is_document_identity=i < identity_count
            )
            for i in range(count)
        ]
    )
    assert provider.minimum_call_count(count, identity_count) == len(
        provider._batched_requests(request)
    )


def test_admission_plan_is_derived_from_every_template_page():
    pages = [
        {
            "pageId": "page-a",
            "ocrProvider": "openai",
            "ocrEnabled": True,
            "fieldMappings": [{}] * 59,
            "documentContext": {"identityFields": [{}]},
        },
        {
            "pageId": "page-b",
            "ocrProvider": "openai",
            "ocrEnabled": True,
            "fieldMappings": [{}] * 51,
        },
        {
            "pageId": "page-c",
            "ocrProvider": "openai",
            "ocrEnabled": True,
            "fieldMappings": [{}] * 37,
        },
        {
            "pageId": "page-d",
            "ocrProvider": "openai",
            "ocrEnabled": True,
            "fieldMappings": [{}] * 3,
        },
    ]

    plan = require_call_budget_plan(pages, 16)

    assert plan == call_budget_plan(pages, 16)
    assert plan["pageMinimumCalls"] == [
        {"pageId": "page-a", "minimumCalls": 3},
        {"pageId": "page-b", "minimumCalls": 2},
        {"pageId": "page-c", "minimumCalls": 2},
        {"pageId": "page-d", "minimumCalls": 1},
    ]
    assert plan["minimumCalls"] == 8
    assert plan["documentRetryReserveCalls"] == 8
    assert plan["requiredCalls"] == 16


def test_admission_plan_rejects_any_template_larger_than_configured_ceiling():
    pages = [{
        "pageId": "large-page",
        "ocrProvider": "openai",
        "ocrEnabled": True,
        "fieldMappings": [{}] * 61,
    }]

    with pytest.raises(AppError) as error:
        require_call_budget_plan(pages, 5)

    assert error.value.code == "OCR_CALL_BUDGET_RETRY_RESERVE_REQUIRED"
    assert "最低3回" in error.value.detail
    assert "再試行枠を含めて6回" in error.value.detail


def accepted_openai_case(case, monkeypatch, maximum):
    service, _, _ = case
    service.settings = replace(service.settings, ocr=_settings(max_calls=maximum, max_retries=1))
    job = accept(case, monkeypatch)
    with service.database.session() as session:
        stored = session.get(ProcessingJob, job.id)
        stored.status = "PROCESSING"
        session.commit()
    return service, job


def test_acceptance_reserves_budget_and_preflight_rejects_before_extraction(
    pages_case, monkeypatch  # noqa: F811
):
    service, job = accepted_openai_case(pages_case, monkeypatch, 4)
    assert job.config_snapshot_json["jobPages"]["ocrCallBudgetPlan"] == {
        "adapter": "paged-ocr-retry-reserve-v1",
        "pageMinimumCalls": [
            {"pageId": page["pageId"], "minimumCalls": 1}
            for page in job.config_snapshot_json["jobPages"]["pages"]
        ],
        "minimumCalls": 2,
        "documentRetryReserveCalls": 2,
        "requiredCalls": 4,
        "maxCalls": 4,
    }
    pages = job.config_snapshot_json["jobPages"]["pages"]
    budget = PagedCallBudget(
        service.database,
        job.id,
        pages[0]["pageId"],
        1,
        job.config_snapshot_json["jobPagesSha256"],
    )
    assert [budget.reserve(), budget.reserve(), budget.reserve()] == [1, 2, 3]
    executor = execution(service)
    executor.extraction.process = lambda *args, **kwargs: pytest.fail("must not extract")
    with pytest.raises(AppError) as error:
        executor.execute(job.id)
    assert error.value.code == "OCR_CALL_BUDGET_TOO_SMALL"
    with service.database.session() as session:
        budget = session.get(PagedOcrBudget, job.id)
        assert budget.max_calls == 4 and budget.used_calls == 3
        assert budget.snapshot_sha256 == job.config_snapshot_json["jobPagesSha256"]
        assert len(session.scalars(select(PagedOcrCall)).all()) == 3


def test_acceptance_rejects_before_writing_job_when_retry_reserve_does_not_fit(
    pages_case, monkeypatch  # noqa: F811
):
    service, _, _ = pages_case
    service.settings = replace(service.settings, ocr=_settings(max_calls=3, max_retries=1))
    monkeypatch.setattr(service, "_require_qualified_reference", lambda session, version: None)

    with pytest.raises(AppError) as error:
        accept(pages_case, monkeypatch)

    assert error.value.code == "OCR_CALL_BUDGET_RETRY_RESERVE_REQUIRED"
    with service.database.session() as session:
        assert session.scalars(select(PagedOcrBudget)).all() == []


def test_catalog_marks_any_template_unavailable_when_retry_reserve_does_not_fit(
    pages_case, monkeypatch  # noqa: F811
):
    service, manifest, _ = pages_case
    service.settings = replace(service.settings, ocr=_settings(max_calls=3, max_retries=1))
    monkeypatch.setattr(service, "_require_qualified_reference", lambda session, version: None)

    catalog = service.catalog(manifest["templateVersionId"])

    assert catalog["available"] is False
    assert catalog["reason"] == "OCR_CALL_BUDGET_RETRY_RESERVE_REQUIRED"


def test_workflow_protects_later_page_and_records_identity_without_live_client(
    pages_case, monkeypatch  # noqa: F811
):
    service, job = accepted_openai_case(pages_case, monkeypatch, 4)
    original = copy.deepcopy(job.config_snapshot_json)
    executor = execution(service)
    calls = []
    provider = object.__new__(OpenAIOcrProvider)
    provider._settings = service.settings.ocr.model_copy(
        update={"retry_uncertain_high_detail": True}
    )

    async def send(request, model, ordinal):
        calls.append((ordinal, [region.region_id for region in request.regions]))
        return _response(
            model,
            ordinal,
            items=[
                TranscriptionItem(
                    mapping_id=region.region_id, raw=None, status=ReadingStatus.UNCERTAIN
                )
                for region in request.regions
            ],
        )

    async def retry_at_high_detail(request, model, ordinal, *, image_detail):
        calls.append((ordinal, [region.region_id for region in request.regions]))
        assert image_detail == "high"
        return _response(
            model,
            ordinal,
            items=[
                TranscriptionItem(
                    mapping_id=region.region_id,
                    raw="reviewed",
                    status=ReadingStatus.READABLE,
                )
                for region in request.regions
            ],
        )

    provider._call = send
    provider._call_detail = retry_at_high_detail
    executor.workflow._create_provider = lambda name: provider
    executor.execute(job.id)
    pages = original["jobPages"]["pages"]
    assert calls == [
        (1, [pages[0]["fieldMappings"][0]["mappingId"]]),
        (2, [pages[0]["fieldMappings"][0]["mappingId"]]),
        (3, [pages[1]["fieldMappings"][0]["mappingId"]]),
        (4, [pages[1]["fieldMappings"][0]["mappingId"]]),
    ]
    with service.database.session() as session:
        assert session.get(ProcessingJob, job.id).config_snapshot_json == original
        assert session.get(PagedOcrBudget, job.id).used_calls == 4
        rows = session.scalars(select(PagedOcrCall).order_by(PagedOcrCall.call_number)).all()
        assert [r.page_id for r in rows] == [
            pages[0]["pageId"],
            pages[0]["pageId"],
            pages[1]["pageId"],
            pages[1]["pageId"],
        ]


@pytest.mark.parametrize(
    "change,code",
    [
        ("cancel", "JOB_CANCELLED"),
        ("attempt", "PAGE_OCR_ATTEMPT_CHANGED"),
        ("state", "PAGE_OCR_ATTEMPT_CHANGED"),
        ("page", "PAGE_OCR_BUDGET_INVALID"),
        ("missing", "PAGE_OCR_BUDGET_INVALID"),
        ("policy", "PAGE_OCR_BUDGET_REQUIRED"),
        ("snapshot", "PAGE_OCR_BUDGET_REQUIRED"),
        ("ledger", "PAGE_OCR_BUDGET_INVALID"),
    ],
)
def test_invalid_budget_never_reserves(budget_case, change, code):
    database, _, job = budget_case
    current = control(budget_case, "unknown" if change == "page" else "alpha")
    with database.session() as session:
        saved = session.get(ProcessingJob, job.id)
        if change == "cancel":
            saved.cancel_requested = True
        elif change == "attempt":
            saved.attempt_number = 2
        elif change == "state":
            saved.status = "FAILED"
        elif change == "missing":
            session.delete(session.get(PagedOcrBudget, job.id))
        elif change in {"policy", "snapshot"}:
            altered = copy.deepcopy(saved.config_snapshot_json)
            if change == "policy":
                altered["jobPages"].pop("ocrCallBudget")
                altered["jobPagesSha256"] = digest(altered["jobPages"])
            else:
                altered["jobPagesSha256"] = "b" * 64
            saved.config_snapshot_json = altered
        elif change == "ledger":
            session.get(PagedOcrBudget, job.id).used_calls = 1
        session.commit()
    with pytest.raises(AppError) as error:
        current.reserve()
    assert error.value.code == code
    with database.session() as session:
        assert session.scalars(select(PagedOcrCall)).all() == []
