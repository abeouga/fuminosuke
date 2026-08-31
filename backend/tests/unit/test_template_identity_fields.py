import pytest
from pydantic import ValidationError

from app.domain.template_config import TemplateConfig


def _config() -> dict[str, object]:
    return {
        "templateId": "identity-test",
        "templateVersion": 1,
        "name": "識別設定テスト",
        "canonicalWidth": 300,
        "canonicalHeight": 100,
        "registration": {
            "preferredMethod": "ORB_HOMOGRAPHY",
            "minFeatureMatches": 4,
            "minInliers": 4,
            "minInlierRatio": 0.3,
            "maxReprojectionErrorPx": 8,
            "minEccScore": 0.4,
        },
        "regions": [],
        "fieldMappings": [
            {
                "mappingId": "individual_id",
                "targetPath": "document.unifiedEarTag",
                "label": "個体識別番号",
                "x": 0,
                "y": 0,
                "width": 100,
                "height": 30,
            },
            {
                "mappingId": "cattle_name",
                "targetPath": "document.cattleName",
                "label": "牛名",
                "x": 110,
                "y": 0,
                "width": 100,
                "height": 30,
            },
        ],
        "marks": [],
        "export": {"csvColumns": []},
    }


def test_legacy_single_identity_becomes_an_effective_primary_title() -> None:
    payload = _config()
    payload["documentIdentity"] = {
        "mappingId": "individual_id",
        "targetPath": "document.unifiedEarTag",
        "label": "個体識別番号",
        "valueType": "digits",
        "exactLength": 10,
    }

    config = TemplateConfig.model_validate(payload)

    fields = config.effective_document_identity_fields()
    assert len(fields) == 1
    assert fields[0].match_role == "primary"
    assert fields[0].display_role == "title"


def test_multiple_identity_fields_separate_matching_and_card_display() -> None:
    payload = _config()
    payload["documentIdentity"] = {
        "mappingId": "individual_id",
        "targetPath": "document.unifiedEarTag",
        "label": "個体識別番号",
        "valueType": "digits",
        "exactLength": 10,
        "matchRole": "primary",
        "displayRole": "subtitle",
    }
    payload["documentIdentityFields"] = [
        payload["documentIdentity"],
        {
            "mappingId": "cattle_name",
            "targetPath": "document.cattleName",
            "label": "牛名",
            "valueType": "text",
            "exactLength": None,
            "matchRole": "confirmation",
            "displayRole": "title",
        },
    ]

    config = TemplateConfig.model_validate(payload)

    assert [field.target_path for field in config.effective_document_identity_fields()] == [
        "document.unifiedEarTag",
        "document.cattleName",
    ]
    assert config.effective_document_identity_fields()[1].display_role == "title"


def test_multiple_identity_fields_reject_two_primary_fields() -> None:
    payload = _config()
    payload["documentIdentityFields"] = [
        {
            "mappingId": "individual_id",
            "targetPath": "document.unifiedEarTag",
            "label": "個体識別番号",
            "matchRole": "primary",
            "displayRole": "subtitle",
        },
        {
            "mappingId": "cattle_name",
            "targetPath": "document.cattleName",
            "label": "牛名",
            "matchRole": "primary",
            "displayRole": "title",
        },
    ]

    with pytest.raises(ValidationError, match="exactly one primary"):
        TemplateConfig.model_validate(payload)


def test_multiple_confirmation_fields_form_an_all_different_combination() -> None:
    payload = _config()
    payload["documentIdentityFields"] = [
        {
            "mappingId": "individual_id",
            "targetPath": "document.unifiedEarTag",
            "label": "個体識別番号",
            "matchRole": "confirmation",
            "displayRole": "subtitle",
        },
        {
            "mappingId": "cattle_name",
            "targetPath": "document.cattleName",
            "label": "牛名",
            "matchRole": "confirmation",
            "displayRole": "title",
        },
    ]

    config = TemplateConfig.model_validate(payload)

    assert config.document_identity is None
    assert [field.match_role for field in config.effective_document_identity_fields()] == [
        "confirmation",
        "confirmation",
    ]


def test_single_confirmation_field_is_not_a_valid_combination() -> None:
    payload = _config()
    payload["documentIdentityFields"] = [
        {
            "mappingId": "individual_id",
            "targetPath": "document.unifiedEarTag",
            "label": "個体識別番号",
            "matchRole": "confirmation",
            "displayRole": "title",
        },
    ]

    with pytest.raises(ValidationError, match="at least two confirmation fields"):
        TemplateConfig.model_validate(payload)


def test_grouped_identity_and_document_name_are_independent_rules() -> None:
    payload = _config()
    payload["documentIdentityFields"] = [
        {
            "mappingId": "individual_id",
            "targetPath": "document.unifiedEarTag",
            "label": "個体識別番号",
            "matchRole": "confirmation",
            "displayRole": "none",
            "matchGroupId": "person",
            "matchGroupLabel": "氏名",
            "componentOrder": 1,
        },
        {
            "mappingId": "cattle_name",
            "targetPath": "document.cattleName",
            "label": "牛名",
            "matchRole": "confirmation",
            "displayRole": "none",
            "matchGroupId": "person",
            "matchGroupLabel": "氏名",
            "componentOrder": 0,
        },
    ]
    payload["documentName"] = {
        "fieldPaths": ["document.cattleName"],
        "separator": "_",
        "prefix": "面接カード_",
        "suffix": "",
    }

    config = TemplateConfig.model_validate(payload)

    fields = config.effective_document_identity_fields()
    assert [field.match_group_id for field in fields] == ["person", "person"]
    assert config.document_name is not None
    assert config.document_name.field_paths == ["document.cattleName"]


def test_grouped_identity_rejects_a_partially_grouped_configuration() -> None:
    payload = _config()
    payload["documentIdentityFields"] = [
        {
            "mappingId": "individual_id",
            "targetPath": "document.unifiedEarTag",
            "label": "個体識別番号",
            "matchGroupId": "person",
            "matchGroupLabel": "氏名",
        },
        {
            "mappingId": "cattle_name",
            "targetPath": "document.cattleName",
            "label": "牛名",
            "matchRole": "confirmation",
            "displayRole": "title",
        },
    ]

    with pytest.raises(ValidationError, match="cannot mix grouped and legacy matching"):
        TemplateConfig.model_validate(payload)
