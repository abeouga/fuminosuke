import copy
import hashlib

import numpy as np
import pytest

from app.domain.paged_document_metadata import page_context, proposed_name, request_identity_fields
from app.domain.template_config import TemplateConfig, build_blank_template_config
from app.services.extraction_service import ExtractionOutput
from app.services.job_page_service import page_config
from app.services.ocr_request_preparation_service import OcrRequestPreparer
from tests.ocr_contract.test_phase5a_request_preparation_contract import _crop


def configured():
    raw = build_blank_template_config(
        template_id="fixture",
        template_version=1,
        name="任意",
        canonical_width=300,
        canonical_height=300,
    ).model_dump(mode="json", by_alias=True)
    raw.update(
        canonicalWidth=300,
        canonicalHeight=300,
        regions=[],
        marks=[],
        fieldMappings=[
            {
                "mappingId": name,
                "targetPath": f"customFields.{name}",
                "label": "任意同名",
                "x": 20,
                "y": 30,
                "width": 30,
                "height": 20,
                "padding": 0,
            }
            for name in ("alpha", "beta", "plain")
        ],
        documentIdentity=None,
        documentIdentityFields=[
            {
                "mappingId": name,
                "targetPath": f"customFields.{name}",
                "label": "任意同名",
                "matchRole": "confirmation",
                "displayRole": "title" if number == 0 else "none",
                "valueType": "text" if number == 0 else "digits",
                "exactLength": None if number == 0 else 6,
                "matchGroupId": "arbitrary-group",
                "matchGroupLabel": "任意グループ",
                "componentOrder": number,
            }
            for number, name in enumerate(("alpha", "beta"))
        ],
        documentName={
            "fieldPaths": ["customFields.beta", "customFields.alpha"],
            "separator": "／",  # noqa: RUF001 - intentional fullwidth filename separator
            "prefix": "記録-",
            "suffix": "-控",
        },
    )
    return TemplateConfig.model_validate(raw)


def page_for(config, name):
    mapping = next(m for m in config.field_mappings if m.mapping_id == name)
    return {
        "width": 300,
        "height": 300,
        "fields": [
            {
                "bindings": [{"mappingId": name, "targetPath": mapping.target_path}],
                "rectangle": {"x": 40, "y": 50, "width": 30, "height": 20},
            }
        ],
    }


@pytest.mark.parametrize("name", ["alpha", "beta", "plain"])
def test_page_identity_hints_preserve_group_roles_and_physical_request(name):
    whole = configured()
    before = whole.model_dump(mode="json", by_alias=True)
    local = page_config(whole, page_for(whole, name))
    context = page_context(
        whole, [m.model_dump(mode="json", by_alias=True) for m in local.field_mappings]
    )
    snapshot = {"pageId": f"page-{name}", "documentContext": context}
    hints = request_identity_fields(local, snapshot)
    assert [f.mapping_id for f in hints] == ([] if name == "plain" else [name])
    if hints:
        assert hints[0] == next(f for f in whole.document_identity_fields if f.mapping_id == name)
    assert not local.document_identity_fields and local.document_name is None
    mapping = local.field_mappings[0]
    image = np.full((300, 300, 3), 255, dtype=np.uint8)
    extraction = ExtractionOutput(
        job_id="job",
        attempt_number=1,
        template_version_id="version",
        aligned_rgb=image,
        template_rgb=image.copy(),
        crops={name: _crop(mapping, 7, ink=True)},
        marks=[],
        quality={},
        unknown_overlap_regions=[],
        artifacts={},
        config=local,
        page_snapshot=snapshot,
    )
    preparer = OcrRequestPreparer()
    kwargs = {"extraction": extraction, "provider_name": "openai", "should_cancel": lambda: False}
    previous = preparer.prepare(job_snapshot={"pageId": f"page-{name}"}, **kwargs)
    current = preparer.prepare(job_snapshot=snapshot, **kwargs)
    a, b = previous.request.regions[0], current.request.regions[0]
    assert b.is_document_identity == (name != "plain")
    assert b.identity_value_type == (
        "digits" if name == "beta" else "text" if name == "alpha" else None
    )
    assert b.identity_exact_length == (6 if name == "beta" else None)
    for key in ("region_id", "source_region_id", "target_path", "bounding_box"):
        assert getattr(a, key) == getattr(b, key)
    assert hashlib.sha256(a.image_png).digest() == hashlib.sha256(b.image_png).digest()
    assert a.handwriting_png == b.handwriting_png
    assert current.mapped_cells_payload == previous.mapped_cells_payload
    assert whole.model_dump(mode="json", by_alias=True) == before


@pytest.mark.parametrize("change", ["page", "adapter", "mapping", "path", "duplicate", "mixed"])
def test_invalid_page_context_is_not_used_for_recognition(change):
    config = configured()
    local = page_config(config, page_for(config, "alpha"))
    context = page_context(
        config, [m.model_dump(mode="json", by_alias=True) for m in local.field_mappings]
    )
    snapshot = {"pageId": "page", "documentContext": context}
    if change == "page":
        snapshot.pop("pageId")
    elif change == "adapter":
        context["adapter"] = "unknown"
    elif change == "mapping":
        context["identityFields"][0]["mappingId"] = "beta"
    elif change == "path":
        context["identityFields"][0]["targetPath"] = "customFields.beta"
    elif change == "duplicate":
        context["identityFields"] *= 2
    else:
        local = config
    with pytest.raises(ValueError):
        request_identity_fields(local, snapshot)


def test_name_uses_explicit_whole_document_order_without_partial_or_uncertain_inference():
    config = configured()
    readings = {
        "customFields.alpha": {"raw": "任意名", "normalized": "任意名", "status": "readable"},
        "customFields.beta": {"raw": "001234", "normalized": "001234", "status": "readable"},
    }
    before = copy.deepcopy(readings)
    assert proposed_name(config, readings) == "記録-001234／任意名-控"  # noqa: RUF001
    for missing in readings:
        assert proposed_name(config, {k: v for k, v in readings.items() if k != missing}) is None
    uncertain = copy.deepcopy(readings)
    uncertain["customFields.beta"].update(status="uncertain", normalized=None)
    assert proposed_name(config, uncertain) is None
    assert readings == before


def test_legacy_full_document_identity_contract_is_unchanged():
    config = configured()
    assert request_identity_fields(config, {}) == config.effective_document_identity_fields()


@pytest.mark.parametrize("mode", ["primary", "combined"])
def test_non_grouped_whole_document_constraints_are_not_rewritten_for_each_page(mode):
    raw = configured().model_dump(mode="json", by_alias=True)
    for index, field in enumerate(raw["documentIdentityFields"]):
        field["matchGroupId"] = None
        field["matchGroupLabel"] = None
        field["matchRole"] = "primary" if mode == "primary" and index == 0 else "confirmation"
    config = TemplateConfig.model_validate(raw)
    for field in config.document_identity_fields:
        local = page_config(config, page_for(config, field.mapping_id))
        context = page_context(
            config, [m.model_dump(mode="json", by_alias=True) for m in local.field_mappings]
        )
        assert request_identity_fields(
            local, {"pageId": field.mapping_id, "documentContext": context}
        ) == [field]
