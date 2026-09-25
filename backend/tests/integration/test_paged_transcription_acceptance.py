"""Synthetic admission only. The real renderer and ACTIVE gates stay closed."""

import copy

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.db.models import PagedOcrBudget, PagedOcrCall, ProcessingJob
from app.domain.ocr_models import ReadingStatus
from app.ocr.openai_provider import OpenAIOcrProvider
from app.ocr.transcription_models import TranscriptionItem
from app.services import paged_transcription_resources as resources
from app.services.print_layout_service import digest
from tests.integration.test_job_pages import execution, pages_case  # noqa: F401
from tests.integration.test_paged_call_budget import accepted_openai_case
from tests.integration.test_print_layout_api import print_template  # noqa: F401
from tests.unit.test_openai_call_budget import _response


def test_acceptance_to_both_page_requests_uses_saved_prompt(pages_case, monkeypatch):  # noqa: F811
    monkeypatch.setattr(resources, "load_transcription_prompt", lambda: "accepted \u03b1")
    service, job = accepted_openai_case(pages_case, monkeypatch, 4)
    original = copy.deepcopy(job.config_snapshot_json)
    assert resources.accepted_transcription_prompt(original) == "accepted \u03b1"
    monkeypatch.setattr(resources, "load_transcription_prompt", lambda: "changed β")
    executor = execution(service)
    provider = object.__new__(OpenAIOcrProvider)
    provider._settings = service.settings.ocr
    calls = []

    async def send(request, model, ordinal):
        calls.append((request.transcription_prompt, [r.region_id for r in request.regions]))
        return _response(model, ordinal, items=[TranscriptionItem(
            mapping_id=r.region_id, raw="fixture", status=ReadingStatus.READABLE,
        ) for r in request.regions])

    provider._call = send
    executor.workflow._create_provider = lambda name: provider
    executor.execute(job.id)
    pages = original["jobPages"]["pages"]
    assert calls == [("accepted \u03b1", [p["fieldMappings"][0]["mappingId"]]) for p in pages]
    with service.database.session() as session:
        assert session.get(ProcessingJob, job.id).config_snapshot_json == original
        assert session.get(PagedOcrBudget, job.id).used_calls == 2
        assert len(session.scalars(select(PagedOcrCall)).all()) == 2


@pytest.mark.parametrize("missing", [True, False])
def test_resource_failure_precedes_extraction_and_budget_use(pages_case, monkeypatch, missing):  # noqa: F811
    service, job = accepted_openai_case(pages_case, monkeypatch, 4)
    with service.database.session() as session:
        stored = session.get(ProcessingJob, job.id)
        changed = copy.deepcopy(stored.config_snapshot_json)
        if missing:
            changed["jobPages"].pop("transcriptionResources")
        else:
            schema = changed["jobPages"]["transcriptionResources"]["responseModelSchema"]
            schema["json"]["title"] = "DifferentResponse"
            schema["sha256"] = digest(schema["json"])
        changed["jobPagesSha256"] = digest(changed["jobPages"])
        stored.config_snapshot_json = changed
        session.commit()
    executor = execution(service)
    executor.extraction.process = lambda *args, **kwargs: pytest.fail("must not extract")
    executor.workflow._create_provider = lambda *args: pytest.fail("must not create provider")
    with pytest.raises(AppError) as caught:
        executor.execute(job.id)
    assert caught.value.code == (
        "PAGE_TRANSCRIPTION_RESOURCES_REQUIRED" if missing else "PAGE_TRANSCRIPTION_SCHEMA_CHANGED"
    )
    with service.database.session() as session:
        assert session.get(ProcessingJob, job.id).config_snapshot_json == changed
        assert session.get(PagedOcrBudget, job.id).used_calls == 0
        assert session.scalars(select(PagedOcrCall)).all() == []
