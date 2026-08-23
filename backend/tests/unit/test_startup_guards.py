from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_start_bat_clears_inherited_storage_overrides() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    content = (repository_root / "start.bat").read_text(encoding="utf-8")

    assert "for %%V in (" in content
    for setting in (
        "DATA_DIR",
        "DATABASE_URL",
        "LOG_FILE",
        "OCR_PROVIDER",
        "GOOGLE_SHEETS_EDIT_POLL_INTERVAL_SECONDS",
    ):
        assert setting in content
    assert 'do set "%%V="' in content
    assert '".venv\\Scripts\\python.exe" -c "import app.main"' in content


@pytest.mark.skipif(os.name != "nt", reason="start.bat is Windows-only")
def test_start_bat_runs_setup_when_existing_install_has_no_env(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "llm-ocr"
    scripts_dir = project_root / "backend" / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    (project_root / "frontend" / "node_modules").mkdir(parents=True)
    for executable in ("python.exe", "alembic.exe"):
        (scripts_dir / executable).touch()

    repository_root = Path(__file__).resolve().parents[3]
    shutil.copyfile(repository_root / "start.bat", project_root / "start.bat")
    (project_root / "setup.bat").write_text("@exit /b 7\n", encoding="ascii")

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(project_root / "start.bat")],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 7
    assert "Preparing the application for its first start" in completed.stdout
    assert not (project_root / "backend" / ".env").exists()


@pytest.mark.skipif(os.name != "nt", reason="start.bat is Windows-only")
def test_start_bat_repairs_incomplete_frontend_dependencies(tmp_path: Path) -> None:
    project_root = tmp_path / "llm-ocr"
    scripts_dir = project_root / "backend" / ".venv" / "Scripts"
    scripts_dir.mkdir(parents=True)
    (project_root / "frontend" / "node_modules").mkdir(parents=True)
    (project_root / "backend" / ".env").touch()
    for executable in ("python.exe", "alembic.exe"):
        (scripts_dir / executable).touch()

    repository_root = Path(__file__).resolve().parents[3]
    shutil.copyfile(repository_root / "start.bat", project_root / "start.bat")
    (project_root / "setup.bat").write_text("@exit /b 9\n", encoding="ascii")

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", str(project_root / "start.bat")],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 9
    assert "Preparing the application for its first start" in completed.stdout
