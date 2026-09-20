"""Identity of immutable printable files, independent of authoring semantics."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def render_key(plan: dict[str, Any]) -> str:
    identity = {
        key: plan[key]
        for key in (
            "sourceSha256",
            "sheetName",
            "paperSize",
            "orientation",
            "margins",
            "horizontalCentering",
            "verticalCentering",
            "commonScale",
            "geometryEvidence",
            "printAdapter",
            "printScalePolicy",
        )
    }
    identity["pages"] = [
        {key: page[key] for key in ("range", "width", "height", "transform")}
        for page in plan["pages"]
    ]
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
