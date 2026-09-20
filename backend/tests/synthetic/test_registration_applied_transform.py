"""The reported transform must describe the pixels actually returned."""
from collections.abc import Callable

import cv2
import numpy as np
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.image_processing import registration as module
from app.image_processing.registration import TemplateAligner
from tests.synthetic.image_factory import ledger_image


@pytest.mark.parametrize("fallback", [False, True])
def test_legacy_reports_final_transform_without_changing_pixels(
    settings_factory: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch, fallback: bool,
) -> None:
    template = cv2.resize(ledger_image(), (1240, 1754))
    source = cv2.resize(template, (620, 877)) if fallback else template.copy()
    aligner = TemplateAligner(settings_factory().registration)
    monkeypatch.setattr(aligner, "_orb_quality_sufficient", lambda _: not fallback)
    # ECC's warp is destination -> pre-refinement source (WARP_INVERSE_MAP).
    ecc_warp = np.array([[1.0, 0.002, 3.25], [-0.001, 1.0, -2.5], [0.0, 0.0, 1.0]])
    observed = {}

    def refine(aligned: np.ndarray, reference: np.ndarray) -> tuple:
        pixels = cv2.warpAffine(
            aligned, ecc_warp[:2], (reference.shape[1], reference.shape[0]),
            flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=(255, 255, 255),
        )
        observed["pixels"] = pixels.copy()
        return pixels, 1.0, ecc_warp.copy()

    monkeypatch.setattr(module, "_ecc_refine_transform", refine)
    monkeypatch.setattr(module, "_edge_overlap", lambda *_: 1.0)
    result = aligner._align_legacy(source, template)
    resize = (np.array([[2.0, 0.0, 0.5], [0.0, 2.0, 0.5], [0.0, 0.0, 1.0]])
              if fallback else np.eye(3))
    expected = np.linalg.inv(ecc_warp) @ resize
    points = np.float32([[[50, 60], [200, 350], [500, 700]]])
    assert np.max(np.abs(cv2.perspectiveTransform(points, result.homography)
                         - cv2.perspectiveTransform(points, expected))) < 0.01
    assert np.array_equal(result.aligned_rgb, observed["pixels"])
    assert result.diagnostics is None


@pytest.mark.parametrize("warp", [np.zeros((3, 3)), np.full((3, 3), np.nan)])
def test_legacy_does_not_report_an_invalid_applied_transform(
    settings_factory: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch, warp: np.ndarray,
) -> None:
    template = cv2.resize(ledger_image(), (1240, 1754))
    aligner = TemplateAligner(settings_factory().registration)
    monkeypatch.setattr(module, "_ecc_refine_transform", lambda aligned, _: (aligned, 1.0, warp))
    with pytest.raises(AppError) as caught:
        aligner._align_legacy(template, template)
    assert caught.value.code == "LOW_REGISTRATION_CONFIDENCE"
    assert caught.value.metadata["rejectionReason"] == "DEGENERATE_ECC_TRANSFORM"
