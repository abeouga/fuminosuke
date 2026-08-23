from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class RegistrationQuality:
    registration_method: str = "ORB_RANSAC_ECC"
    detected_keypoints_source: int = 0
    detected_keypoints_template: int = 0
    match_count: int = 0
    inlier_count: int = 0
    inlier_ratio: float = 0.0
    mean_reprojection_error_px: float = 0.0
    median_reprojection_error_px: float = 0.0
    ecc_score: float | None = None
    edge_overlap: float = 0.0
    coverage_ratio: float = 0.0
    spatial_distribution_score: float = 0.0
    homography_valid: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {_camel(key): value for key, value in data.items()}


@dataclass(slots=True)
class DocumentQuality:
    boundary_confidence: float
    fallback_used: bool
    manual_boundary_used: bool
    source_width: int
    source_height: int
    output_width: int
    output_height: int
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {_camel(key): value for key, value in data.items()}


def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)
