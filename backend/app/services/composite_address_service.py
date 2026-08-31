from __future__ import annotations

import re

from app.domain.ocr_models import BoundingBox, FieldReading, OcrDocument, ReadingStatus
from app.domain.template_config import FieldMappingConfig
from app.ocr.deterministic_mapper import assign_mapped_reading, get_mapped_reading


PREFECTURES = (
    "北海道",
    "青森県",
    "岩手県",
    "宮城県",
    "秋田県",
    "山形県",
    "福島県",
    "茨城県",
    "栃木県",
    "群馬県",
    "埼玉県",
    "千葉県",
    "東京都",
    "神奈川県",
    "新潟県",
    "富山県",
    "石川県",
    "福井県",
    "山梨県",
    "長野県",
    "岐阜県",
    "静岡県",
    "愛知県",
    "三重県",
    "滋賀県",
    "京都府",
    "大阪府",
    "兵庫県",
    "奈良県",
    "和歌山県",
    "鳥取県",
    "島根県",
    "岡山県",
    "広島県",
    "山口県",
    "徳島県",
    "香川県",
    "愛媛県",
    "高知県",
    "福岡県",
    "佐賀県",
    "長崎県",
    "熊本県",
    "大分県",
    "宮崎県",
    "鹿児島県",
    "沖縄県",
)


def split_japanese_address(value: str) -> tuple[str | None, str | None]:
    compact = re.sub(r"[\s　]+", "", value)
    compact = re.sub(r"^〒?\d{3}-?\d{4}", "", compact)
    prefecture = next(
        (candidate for candidate in PREFECTURES if compact.startswith(candidate)),
        None,
    )
    if prefecture is None:
        return None, None
    remainder = compact[len(prefecture) :]
    municipality = None
    for pattern in (
        r"^(.+?市.+?区)",
        r"^(.+?市.+?市)",
        r"^(.+?郡.+?[町村])",
        r"^(.+?[市区町村])",
    ):
        match = re.match(pattern, remainder)
        if match:
            municipality = match.group(1)
            break
    return prefecture, municipality


def _virtual_bounding_box(
    mapping: FieldMappingConfig,
    *,
    x_ratio: float,
    y_ratio: float,
    width_ratio: float,
    height_ratio: float,
) -> BoundingBox:
    return BoundingBox(
        x=mapping.x + round(mapping.width * x_ratio),
        y=mapping.y + round(mapping.height * y_ratio),
        width=max(1, round(mapping.width * width_ratio)),
        height=max(1, round(mapping.height * height_ratio)),
    )


def apply_composite_address_fields(
    document: OcrDocument,
    mappings: list[FieldMappingConfig],
) -> None:
    for mapping in mappings:
        if not mapping.enabled or not mapping.composite_subfields:
            continue
        parent = get_mapped_reading(document, mapping.target_path)
        if parent is None:
            continue
        source_text = (parent.normalized or parent.raw or "").strip()
        prefecture, municipality = split_japanese_address(source_text)
        values = {
            "address_prefecture": prefecture,
            "address_municipality": municipality,
        }
        for subfield in mapping.composite_subfields:
            value = values[subfield.semantic_type]
            if parent.status is ReadingStatus.BLANK:
                status = ReadingStatus.BLANK
                review_reason = None
            elif value is None:
                status = ReadingStatus.UNCERTAIN
                review_reason = "COMPOSITE_ADDRESS_UNRESOLVED"
            elif subfield.semantic_type == "address_municipality":
                status = ReadingStatus.UNCERTAIN
                review_reason = "COMPOSITE_ADDRESS_CONFIRM_MUNICIPALITY"
            else:
                status = ReadingStatus.READABLE
                review_reason = None
            assign_mapped_reading(
                document,
                subfield.target_path,
                FieldReading(
                    raw=value,
                    normalized=value,
                    status=status,
                    alternatives=[],
                    review_reason=review_reason,
                    source_region_id=parent.source_region_id,
                    bounding_box=_virtual_bounding_box(
                        mapping,
                        x_ratio=subfield.x_ratio,
                        y_ratio=subfield.y_ratio,
                        width_ratio=subfield.width_ratio,
                        height_ratio=subfield.height_ratio,
                    ),
                ),
            )
