from collections.abc import Callable

import cv2
import numpy as np
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.template_config import load_template_config
from app.extraction.handwriting_isolator import HandwritingIsolator
from app.image_processing.document_boundary import DocumentNormalizer
from app.image_processing.line_detection import LineDetector
from app.image_processing.registration import TemplateAligner
from app.segmentation.heuristic import HeuristicHandwritingSegmenter
from app.template_recovery.pipelines import TemplateRecoveryPipelines
from tests.synthetic.image_factory import ledger_image


def test_document_normalizer_canonicalizes_full_scan() -> None:
    config = load_template_config()
    result = DocumentNormalizer(
        canonical_width=config.canonical_width,
        canonical_height=config.canonical_height,
    ).normalize(ledger_image())

    assert result.image_rgb.shape == (config.canonical_height, config.canonical_width, 3)
    assert 0.5 <= result.quality.boundary_confidence <= 1.0


def test_document_normalizer_corrects_perspective_before_layout_detection() -> None:
    source = ledger_image()
    source_height, source_width = source.shape[:2]
    canvas = np.full((1100, 850, 3), 55, dtype=np.uint8)
    source_corners = np.float32(
        [
            [0, 0],
            [source_width - 1, 0],
            [source_width - 1, source_height - 1],
            [0, source_height - 1],
        ]
    )
    photographed_corners = np.float32(
        [[95, 65], [735, 25], [785, 1040], [40, 1015]]
    )
    transform = cv2.getPerspectiveTransform(source_corners, photographed_corners)
    photographed = cv2.warpPerspective(
        source,
        transform,
        (canvas.shape[1], canvas.shape[0]),
        dst=canvas,
        borderMode=cv2.BORDER_TRANSPARENT,
    )

    result = DocumentNormalizer(
        canonical_width=source_width,
        canonical_height=source_height,
    ).normalize(photographed)

    assert result.image_rgb.shape == source.shape
    assert result.quality.fallback_used is False
    assert result.quality.boundary_confidence > 0.45
    assert not np.array_equal(
        result.source_corners,
        np.float32([[0, 0], [849, 0], [849, 1099], [0, 1099]]),
    )


def test_registration_and_handwriting_isolation(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    template = cv2.resize(ledger_image(), (1240, 1754))
    source = cv2.resize(ledger_image(handwriting_seed=2), (1240, 1754))

    registration = TemplateAligner(settings.registration).align(source, template)
    handwriting = HandwritingIsolator(threshold=settings.segmentation.threshold).isolate(
        registration.aligned_rgb, template
    )

    assert registration.quality.edge_overlap >= settings.registration.min_edge_overlap
    assert handwriting.mask.shape == template.shape[:2]
    assert 0 < handwriting.coverage_ratio < 0.1


def test_registration_rejects_unrelated_image(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    template = cv2.resize(ledger_image(), (1240, 1754))
    unrelated = np.random.default_rng(4).integers(0, 255, template.shape, dtype=np.uint8)

    with pytest.raises(AppError) as caught:
        TemplateAligner(settings.registration).align(unrelated, template)
    assert caught.value.code == "LOW_REGISTRATION_CONFIDENCE"


def test_single_mode_requires_layout_review(
    settings_factory: Callable[..., Settings],
) -> None:
    settings = settings_factory()
    pipelines = TemplateRecoveryPipelines(settings, load_template_config())
    single = pipelines.single_experimental([ledger_image(handwriting_seed=1)])

    assert single.clean_template_rgb.shape == (1754, 1240, 3)
    assert single.handwriting_mask is None
    assert single.quality["processingOrder"] == [
        "PERSPECTIVE_CORRECTION",
        "LAYOUT_CLASSIFICATION",
        "HANDWRITING_EXTRACTION",
    ]
    assert "LAYOUT_CLASSIFICATION_REQUIRED_BEFORE_HANDWRITING_EXTRACTION" in (
        single.quality["warnings"]
    )
    assert "SINGLE_IMAGE_REQUIRES_HUMAN_REVIEW" in single.quality["warnings"]


def test_single_mode_detects_gray_handwriting_on_shaded_paper(
    settings_factory: Callable[..., Settings],
) -> None:
    image = ledger_image()
    height, width = image.shape[:2]
    shade = np.linspace(18, 48, width, dtype=np.uint8)
    shaded = np.clip(image.astype(np.int16) - shade[None, :, None], 0, 255).astype(np.uint8)
    cv2.putText(
        shaded,
        "27083",
        (250, 145),
        cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
        0.8,
        (125, 125, 125),
        2,
        cv2.LINE_AA,
    )

    probability = HeuristicHandwritingSegmenter().predict(shaded)
    handwriting_area = probability[120:155, 245:340]
    line_mask = LineDetector().detect(cv2.cvtColor(shaded, cv2.COLOR_RGB2GRAY))

    assert float(handwriting_area.max()) >= settings_factory().segmentation.threshold
    assert float((line_mask > 0).mean()) < 0.08
    assert float((line_mask[79:83, 20 : width - 20] > 0).mean()) > 0.7
