import base64

import pytest

from app.core.errors import AppError
from app.ocr.base import OcrProviderRequest, OcrRegionInput
from app.ocr.openai_provider import build_mapped_input_content


def test_openai_payload_contains_only_opaque_ids_and_mapped_cell_images() -> None:
    request = OcrProviderRequest(
        template_id="template-secret",
        template_version="8",
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"exact-input-cell",
                handwriting_png=b"handwriting-hint",
                target_path="document.cattleName",
                source_region_id="field_document_cattleName",
                bounding_box=(100, 200, 300, 80),
            )
        ],
    )

    content = build_mapped_input_content(request, image_detail="high")

    text = "\n".join(
        str(item["text"]) for item in content if item["type"] == "input_text"
    )
    assert "cell_0001" in text
    assert "field_document_cattleName" not in text
    assert "document.cattleName" not in text
    assert "template-secret" not in text
    assert "label" not in text.lower()
    assert "100" not in text

    image_items = [item for item in content if item["type"] == "input_image"]
    decoded = [
        base64.b64decode(str(item["image_url"]).split(",", maxsplit=1)[1])
        for item in image_items
    ]
    assert decoded == [b"exact-input-cell", b"handwriting-hint"]


def test_openai_payload_rejects_unmapped_regions() -> None:
    request = OcrProviderRequest(
        regions=[OcrRegionInput(region_id="legacy_region", image_png=b"full-page")]
    )

    with pytest.raises(AppError) as exc_info:
        build_mapped_input_content(request, image_detail="high")

    assert exc_info.value.code == "TEMPLATE_FIELD_MAPPING_REQUIRED"


def test_openai_payload_scopes_sire_candidates_to_the_mapped_cell() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"sire-cell",
                target_path="saleCalves.0.sireName",
                candidates=("勝忠平", "光晴重", "重波平", "百合茂"),
                allowed_suffixes=("ET",),
            ),
            OcrRegionInput(
                region_id="cell_0002",
                image_png=b"price-cell",
                target_path="saleCalves.0.priceYen",
            ),
        ]
    )

    content = build_mapped_input_content(request, image_detail="high")
    text_items = [
        str(item["text"]) for item in content if item["type"] == "input_text"
    ]
    text = "\n".join(text_items)

    assert "勝忠平" in text
    assert "光晴重" in text
    assert "allowedSuffixes" in text
    assert '"ET"' in text
    assert text.index("candidateSireNames") < text.index("cell_0002")
    assert "saleCalves.0.sireName" not in text


def test_locally_excluded_printed_label_is_sent_only_as_an_exclusion_rule() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"handwriting-only",
                target_path="document.cattleName",
                printed_label_excluded=True,
                printed_label_text="名号",
            )
        ]
    )

    content = build_mapped_input_content(request, image_detail="high")

    text = "\n".join(
        str(item["text"]) for item in content if item["type"] == "input_text"
    )
    images = [item for item in content if item["type"] == "input_image"]
    decoded = base64.b64decode(str(images[0]["image_url"]).split(",", maxsplit=1)[1])
    assert decoded == b"handwriting-only"
    assert 'fixedPrintedTextToIgnore: "名号"' in text
    assert "document.cattleName" not in text


def test_birth_date_and_sale_note_receive_cell_specific_constraints() -> None:
    request = OcrProviderRequest(
        regions=[
            OcrRegionInput(
                region_id="cell_0001",
                image_png=b"birth-date",
                target_path="document.birthDate",
                transcription_format="japanese_era_date_numbers",
            ),
            OcrRegionInput(
                region_id="cell_0002",
                image_png=b"sale-note",
                target_path="saleCalves.0.notes",
                candidates=("♂", "♀"),
                candidate_type="sale_calf_note_prefix",
            ),
        ]
    )

    content = build_mapped_input_content(request, image_detail="high")
    text = "\n".join(
        str(item["text"]) for item in content if item["type"] == "input_text"
    )

    assert "japanese_era_date_numbers" in text
    assert "ERA_YEAR MONTH DAY" in text
    assert "sale_calf_note_prefix" in text
    assert '"♂"' in text
    assert '"♀"' in text
    assert "document.birthDate" not in text
    assert "saleCalves.0.notes" not in text
