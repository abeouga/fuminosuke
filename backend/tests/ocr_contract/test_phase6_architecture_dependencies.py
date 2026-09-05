from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
COMPUTATION_FILES = (
    APP / "services/ocr_request_preparation_service.py",
    APP / "services/ocr_response_finalization_service.py",
)
FORBIDDEN_MODULES = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "google",
    "googleapiclient",
    "openpyxl",
    "app.api",
    "app.db",
    "app.repositories",
    "app.jobs",
    "app.storage",
    "app.services.google",
    "app.services.excel_export_service",
    "app.services.spreadsheet_template_output_service",
    "app.services.output_layout_template_service",
)


def _imports(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize(
    "path",
    [*COMPUTATION_FILES, *sorted((APP / "ocr").rglob("*.py"))],
    ids=lambda path: str(path.relative_to(APP)),
)
def test_ocr_computation_has_no_direct_transport_or_persistence_imports(path: Path) -> None:
    # Direct dependency contract only. ExtractionOutput and encode_png currently
    # live in modules which also import persistence; this test does not claim
    # that their transitive module import graph is persistence-free.
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for name in _imports(tree):
        assert not name.startswith(FORBIDDEN_MODULES), name
        assert not name.endswith(".ArtifactWriter"), name
        if path in COMPUTATION_FILES:
            assert not name.startswith(
                ("app.ocr.openai_provider", "app.ocr.mock_provider", "app.ocr.disabled_provider")
            ), name


@pytest.mark.parametrize("path", COMPUTATION_FILES, ids=lambda path: path.stem)
def test_computation_helpers_do_not_call_filesystem_operations(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    forbidden = {
        "open", "read_bytes", "read_text", "write_bytes", "write_text",
        "mkdir", "unlink", "rmdir", "remove", "rename", "imwrite",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden, node.func.id
        elif isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden, node.func.attr
