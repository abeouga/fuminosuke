from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from app.template_packages.canonical_json import canonical_json_bytes
from app.template_packages.manifest_models import SHA256_PATTERN
from app.template_packages.package_paths import path_collision_key, validate_package_path


def build_content_digest(
    *,
    documents: Mapping[str, object],
    asset_sha256: Mapping[str, str],
) -> str:
    document_entries = _normalized_entries(documents, value_name="document")
    asset_entries = _normalized_entries(asset_sha256, value_name="asset")
    for path, digest in asset_entries:
        if not isinstance(digest, str) or re.fullmatch(SHA256_PATTERN, digest) is None:
            raise ValueError(f"Asset {path} does not contain a lowercase SHA-256 digest")
    digest_payload = {
        "digestSchema": "llm-ocr-template-content-v1",
        "documents": [{"path": path, "value": value} for path, value in document_entries],
        "assets": [{"path": path, "sha256": digest} for path, digest in asset_entries],
    }
    return f"sha256:{hashlib.sha256(canonical_json_bytes(digest_payload)).hexdigest()}"


def _normalized_entries(
    values: Mapping[str, object], *, value_name: str
) -> list[tuple[str, object]]:
    keyed: dict[str, tuple[str, object]] = {}
    for raw_path, value in values.items():
        path = validate_package_path(raw_path)
        collision_key = path_collision_key(path)
        if collision_key in keyed:
            raise ValueError(f"Duplicate {value_name} path after normalization: {path}")
        keyed[collision_key] = (path, value)
    return sorted(keyed.values(), key=lambda entry: entry[0])
