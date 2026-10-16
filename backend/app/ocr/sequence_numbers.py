from collections.abc import Iterable, Mapping

from app.domain.ocr_models import BoundingBox, FieldReading, OcrDocument, ReadingStatus
from app.domain.template_config import RegionConfig
from app.extraction.region_cropper import RegionCrops
from app.ocr.deterministic_mapper import assign_mapped_reading


def apply_sequence_numbers(
    document: OcrDocument,
    regions: Iterable[RegionConfig],
    crops: Mapping[str, RegionCrops],
) -> None:
    """Materialize saved template numbers without consulting pixels or a provider."""
    for region in regions:
        if region.sequence_number is None:
            continue
        if len(region.expected_fields) != 1 or region.region_id not in crops:
            raise ValueError("Sequence number requires its accepted field and physical crop")
        value = str(region.sequence_number)
        box = crops[region.region_id].bbox
        assign_mapped_reading(
            document,
            region.expected_fields[0],
            FieldReading(
                raw=value,
                normalized=value,
                status=ReadingStatus.READABLE,
                alternatives=[],
                review_reason=None,
                source_region_id=region.region_id,
                bounding_box=BoundingBox(x=box[0], y=box[1], width=box[2], height=box[3]),
            ),
        )
