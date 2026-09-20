import copy
import hashlib

import pytest

from app.core.errors import AppError
from app.services import paged_transcription_resources as resources
from app.services.print_layout_service import digest


def snapshot(monkeypatch):
    monkeypatch.setattr(resources, "load_transcription_prompt", lambda: "受付\r\nprompt \u03b1")
    bundle = {"transcriptionResources": resources.capture_transcription_resources()}
    return {"jobPages": bundle, "jobPagesSha256": digest(bundle)}


def test_capture_and_reload_preserve_exact_text_without_current_file(monkeypatch):
    saved = snapshot(monkeypatch)
    original = copy.deepcopy(saved)

    def missing():
        raise FileNotFoundError("gone")

    monkeypatch.setattr(resources, "load_transcription_prompt", missing)
    assert resources.accepted_transcription_prompt(saved) == "受付\r\nprompt \u03b1"
    assert saved == original
    assert saved["jobPages"]["transcriptionResources"]["prompt"]["sha256"] == hashlib.sha256(
        "受付\r\nprompt \u03b1".encode()
    ).hexdigest()


@pytest.mark.parametrize("change,code", [
    ("bundle_sha", "PAGE_TRANSCRIPTION_RESOURCES_INVALID"),
    ("missing", "PAGE_TRANSCRIPTION_RESOURCES_REQUIRED"),
    ("adapter", "PAGE_TRANSCRIPTION_RESOURCES_REQUIRED"),
    ("prompt_sha", "PAGE_TRANSCRIPTION_RESOURCES_INVALID"),
    ("prompt_empty", "PAGE_TRANSCRIPTION_RESOURCES_INVALID"),
    ("prompt_type", "PAGE_TRANSCRIPTION_RESOURCES_INVALID"),
    ("schema_sha", "PAGE_TRANSCRIPTION_RESOURCES_INVALID"),
    ("schema_type", "PAGE_TRANSCRIPTION_RESOURCES_INVALID"),
    ("schema_changed", "PAGE_TRANSCRIPTION_SCHEMA_CHANGED"),
    ("unknown", "PAGE_TRANSCRIPTION_RESOURCES_INVALID"),
])
def test_invalid_or_incompatible_resources_are_not_replaced(monkeypatch, change, code):
    saved = snapshot(monkeypatch)
    bundle = saved["jobPages"]
    item = bundle["transcriptionResources"]
    if change == "missing":
        bundle.pop("transcriptionResources")
    elif change == "adapter":
        item["adapter"] = "unknown"
    elif change == "prompt_sha":
        item["prompt"]["text"] += "changed"
    elif change == "prompt_empty":
        item["prompt"]["text"] = ""
    elif change == "prompt_type":
        item["prompt"]["text"] = []
    elif change == "schema_sha":
        item["responseModelSchema"]["sha256"] = "0" * 64
    elif change == "schema_type":
        item["responseModelSchema"]["json"] = []
    elif change == "schema_changed":
        item["responseModelSchema"]["json"]["title"] = "DifferentResponse"
        item["responseModelSchema"]["sha256"] = digest(item["responseModelSchema"]["json"])
    elif change == "unknown":
        item["futureOption"] = True
    saved["jobPagesSha256"] = "0" * 64 if change == "bundle_sha" else digest(bundle)
    original = copy.deepcopy(saved)
    with pytest.raises(AppError) as caught:
        resources.accepted_transcription_prompt(saved)
    assert caught.value.code == code
    assert saved == original


def test_current_response_model_change_is_detected(monkeypatch):
    saved = snapshot(monkeypatch)
    monkeypatch.setattr(resources.CompactTranscriptionBatch, "model_json_schema", lambda: {})
    with pytest.raises(AppError) as caught:
        resources.accepted_transcription_prompt(saved)
    assert caught.value.code == "PAGE_TRANSCRIPTION_SCHEMA_CHANGED"


@pytest.mark.parametrize("value", ["", " \n", None])
def test_missing_or_empty_acceptance_prompt_is_rejected(monkeypatch, value):
    def load():
        if value is None:
            raise FileNotFoundError("fixture")
        return value

    monkeypatch.setattr(resources, "load_transcription_prompt", load)
    with pytest.raises(AppError) as caught:
        resources.capture_transcription_resources()
    assert caught.value.code == "PAGE_TRANSCRIPTION_RESOURCES_INVALID"
