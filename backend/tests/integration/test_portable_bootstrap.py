from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_checked_in_portable_bootstrap_is_valid() -> None:
    project_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "export_portable_templates.py"),
            "--validate-only",
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "portable_templates=1" in result.stdout
    assert "portable_sire_names=4" in result.stdout

    database_path = project_root / "backend" / "bootstrap" / "templates" / "app.db"
    with sqlite3.connect(database_path) as connection:
        artifact = connection.execute(
            """
            SELECT relative_path
            FROM job_artifacts
            WHERE artifact_name = 'handwritingCandidates'
            """
        ).fetchone()
    assert artifact is not None
    payload = json.loads(
        (database_path.parent / artifact[0]).read_text(encoding="utf-8")
    )
    assert payload["verifiedInputCellCount"] > 0
    assert payload["candidates"]
