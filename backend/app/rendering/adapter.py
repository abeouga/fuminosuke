"""Linux transport adapters for existing print use cases."""

from __future__ import annotations

from typing import Any

from app.rendering.client import render


def import_geometry(workbook: bytes, **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
    if kwargs.get("dimension_request") is not None:
        raise ValueError("The Windows Calc observation policy cannot be used by Linux")
    files, report = render(workbook, "fods")
    return files["page.fods"], report


def reference(workbook: bytes, **kwargs: Any) -> tuple[bytes, list[bytes], dict[str, Any]]:
    files, report = render(workbook, "reference")
    return (
        files["page.pdf"],
        [files[f"page-{n}.png"] for n in range(1, report["pageCount"] + 1)],
        report,
    )
