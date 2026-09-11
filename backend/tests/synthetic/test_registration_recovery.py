from collections.abc import Callable

import cv2
import numpy as np
import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.image_processing import registration as registration_module
from app.image_processing.registration import RegistrationFailure, TemplateAligner
from tests.synthetic.image_factory import ledger_image


def _template() -> np.ndarray:
    return cv2.resize(ledger_image(), (1240, 1754))


def _legacy_rejected(*args: object, **kwargs: object) -> None:
    # Explicitly exercise the fallback boundary; real-image reproduction is
    # recorded separately and does not depend on a forced legacy rejection.
    raise AppError(status=422, code="LOW_REGISTRATION_CONFIDENCE", title="test", detail="test")


@pytest.mark.parametrize("seed", [2, 7])
def test_recovery_preserves_known_physical_coordinates(
    settings_factory: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch, seed: int,
) -> None:
    aligner = TemplateAligner(settings_factory().registration)
    monkeypatch.setattr(aligner, "_align_legacy", _legacy_rejected)
    template = _template()
    source = cv2.resize(ledger_image(handwriting_seed=seed), (1240, 1754))
    transform = np.array([
        [0.96, 0.006, 12], [-0.005, 0.97, 15], [0.000003, -0.000002, 1.0],
    ])
    source = cv2.warpPerspective(source, transform, (1240, 1754), borderValue=(255, 255, 255))
    original = source.copy()
    result = aligner.align(source, template, validation_regions=[(180, 160, 240, 180)])
    assert result.quality.registration_method == "MUTUAL_ORB_RANSAC_ECC"
    # Validate the entire field lattice, not just points selected by RANSAC.
    expected = np.float32([[[x, y] for y in (80, 340, 660, 1060, 1400)
                           for x in (180, 420, 740, 1000)]])
    observed = cv2.perspectiveTransform(
        cv2.perspectiveTransform(expected, transform), result.homography,
    )
    assert np.max(np.linalg.norm(observed - expected, axis=2)) < 2.0
    assert np.array_equal(source, original)
    assert result.diagnostics is not None
    assert min(result.diagnostics["localTemplateEdgeSupport"]["fields"]) >= 0.7


def test_legacy_success_does_not_enter_recovery(
    settings_factory: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch,
) -> None:
    aligner = TemplateAligner(settings_factory().registration)
    source = cv2.resize(ledger_image(handwriting_seed=5), (1240, 1754))
    expected = aligner._align_legacy(source, _template())

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("Existing successful registrations must not enter recovery")

    monkeypatch.setattr(aligner, "_recover_mutual", unexpected)
    result = aligner.align(source, _template())
    assert np.array_equal(result.aligned_rgb, expected.aligned_rgb)
    assert result.quality.to_dict() == expected.quality.to_dict()
    assert result.diagnostics is None


@pytest.mark.parametrize("damage", ["field", "tile", "partial", "blank", "unrelated"])
def test_recovery_rejects_missing_or_unrelated_print_evidence(
    settings_factory: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    aligner = TemplateAligner(settings_factory().registration)
    monkeypatch.setattr(aligner, "_align_legacy", _legacy_rejected)
    template = _template()
    source = template.copy()
    regions = []
    if damage == "field":
        source[70:140, 50:170] = 255
        regions = [(50, 70, 120, 70)]
    elif damage == "tile":
        source[584:1169, 413:826] = 255
    elif damage == "partial":
        source[1100:] = 255
    elif damage == "blank":
        source[:] = 255
    else:
        source = np.random.default_rng(4).integers(0, 256, template.shape, dtype=np.uint8)
    with pytest.raises(RegistrationFailure) as caught:
        aligner.align(source, template, validation_regions=regions)
    assert caught.value.code == "LOW_REGISTRATION_CONFIDENCE"
    if damage in {"field", "tile", "partial"}:
        # These candidates meet the global threshold; the independent local
        # check must still prevent partial forms from being accepted.
        assert caught.value.metadata["recovery"]["edgeOverlap"] >= 0.7
        assert caught.value.metadata["rejectionReason"] == "LOCAL_TEMPLATE_SUPPORT_REJECTED"


@pytest.mark.parametrize("fault", ["row_drift", "reflection", "singular", "no_ecc"])
def test_recovery_rejects_bad_ecc_even_with_high_image_score(
    settings_factory: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    aligner = TemplateAligner(settings_factory().registration)
    monkeypatch.setattr(aligner, "_align_legacy", _legacy_rejected)

    def bad_ecc(source: np.ndarray, template: np.ndarray) -> tuple:
        warp = np.eye(3)
        if fault == "row_drift":
            warp[1, 2] = 140
        elif fault == "reflection":
            warp[0, 0], warp[0, 2] = -1, template.shape[1] - 1
        elif fault == "singular":
            warp[0, 0] = 0
        return template.copy(), None if fault == "no_ecc" else 1.0, warp

    monkeypatch.setattr(registration_module, "_ecc_refine_transform", bad_ecc)
    with pytest.raises(RegistrationFailure) as caught:
        aligner.align(_template(), _template())
    assert caught.value.metadata["rejectionReason"] in {
        "REFINED_GEOMETRY_REJECTED", "DEGENERATE_ECC_TRANSFORM",
    }


def test_field_selection_does_not_change_registration_geometry(
    settings_factory: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch,
) -> None:
    aligner = TemplateAligner(settings_factory().registration)
    monkeypatch.setattr(aligner, "_align_legacy", _legacy_rejected)
    template = _template()
    minimal = aligner.align(template, template, validation_regions=[(180, 160, 240, 180)])
    full = aligner.align(template, template, validation_regions=[
        (180, y, 240, 180) for y in (160, 660, 1060, 1400)
    ])
    assert np.array_equal(minimal.aligned_rgb, full.aligned_rgb)
    assert np.array_equal(minimal.homography, full.homography)


def test_non_registration_failure_is_not_masked(
    settings_factory: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch,
) -> None:
    aligner = TemplateAligner(settings_factory().registration)
    original = AppError(status=409, code="OTHER_ERROR", title="test", detail="test")

    def fail(*args: object, **kwargs: object) -> None:
        raise original

    monkeypatch.setattr(aligner, "_align_legacy", fail)
    with pytest.raises(AppError) as caught:
        aligner.align(_template(), _template())
    assert caught.value is original
