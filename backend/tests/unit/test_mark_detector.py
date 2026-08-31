import cv2
import numpy as np

from app.domain.ocr_models import ReadingStatus
from app.domain.template_config import (
    ExportConfig,
    FieldMappingConfig,
    MarkOptionConfig,
    RegistrationConfig,
    TemplateConfig,
)
from app.extraction.mark_detector import MarkDetector
from app.services.ocr_workflow_service import OcrWorkflowService


def mark_config() -> TemplateConfig:
    return TemplateConfig(
        template_id="mark-test",
        template_version=1,
        name="mark test",
        canonical_width=120,
        canonical_height=40,
        registration=RegistrationConfig(
            preferred_method="ecc",
            min_feature_matches=4,
            min_inliers=4,
            min_inlier_ratio=0.5,
            max_reprojection_error_px=3,
            min_ecc_score=0.5,
        ),
        regions=[],
        field_mappings=[
            FieldMappingConfig(
                mapping_id="calving_outcome",
                target_path="calvings.0.outcome",
                label="結果",
                x=10,
                y=5,
                width=80,
                height=30,
                mapping_type="mark",
                mark_options=[
                    MarkOptionConfig(
                        id="twin", label="双", value="twin",
                        x=0, y=0, width=30, height=30,
                    ),
                    MarkOptionConfig(
                        id="early", label="早", value="premature",
                        x=40, y=0, width=30, height=30,
                    ),
                ],
            )
        ],
        marks=[],
        export=ExportConfig(csv_columns=[]),
    )


def test_detects_a_pencil_circle_by_comparing_with_the_blank_template() -> None:
    config = mark_config()
    template = np.full((40, 120, 3), 255, dtype=np.uint8)
    source = template.copy()
    cv2.circle(source, (25, 20), 11, (40, 40, 40), thickness=3)

    results = MarkDetector(config).detect_all(source, template)

    assert results[0].mapping_id == "calving_outcome"
    assert results[0].option_label == "双"
    assert results[0].status == "detected"
    assert results[1].option_label == "早"
    assert results[1].status == "blank"


def test_combines_detected_options_in_template_order() -> None:
    mapping = mark_config().field_mappings[0]
    state = OcrWorkflowService._mark_state(
        mapping,
        [
            {"mappingId": "calving_outcome", "optionValue": "premature", "status": "detected"},
            {"mappingId": "calving_outcome", "optionValue": "twin", "status": "detected"},
        ],
    )

    assert state == (ReadingStatus.READABLE, "双・早", None)


def test_manual_choice_is_not_detected_or_sent_to_automatic_resolution() -> None:
    config = mark_config()
    manual_mapping = config.field_mappings[0].model_copy(
        update={"mark_recognition_mode": "manual"}
    )
    manual_config = config.model_copy(
        update={"field_mappings": [manual_mapping]},
        deep=True,
    )
    template = np.full((40, 120, 3), 255, dtype=np.uint8)
    source = template.copy()
    cv2.circle(source, (25, 20), 11, (40, 40, 40), thickness=3)

    assert MarkDetector(manual_config).detect_all(source, template) == []
    assert OcrWorkflowService._mark_state(manual_mapping, []) == (
        ReadingStatus.UNCERTAIN,
        None,
        "MANUAL_CHOICE_REQUIRED",
    )
