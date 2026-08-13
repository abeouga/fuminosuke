"""Export active local templates as a Git-trackable bootstrap data set.

The local runtime database contains OCR jobs, uploaded source images, confirmed
records, and logs in addition to templates. Those records are deliberately not
portable. This tool retains only active templates, their source jobs, template
artifacts, template YAML files, and the optional sire-name candidate pool.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "backend" / "data"
DEFAULT_DESTINATION = PROJECT_ROOT / "backend" / "bootstrap" / "templates"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    return parser.parse_args()


def placeholders(values: set[str]) -> str:
    if not values:
        raise ValueError("At least one value is required")
    return ", ".join("?" for _ in values)


def copy_database(source: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    with (
        sqlite3.connect(source) as input_connection,
        sqlite3.connect(destination) as output_connection,
    ):
        input_connection.backup(output_connection)


def export(source_root: Path, destination_root: Path) -> None:
    source_root = source_root.resolve()
    destination_root = destination_root.resolve()
    source_db = source_root / "app.db"
    if not source_db.is_file():
        raise FileNotFoundError(f"Source database is missing: {source_db}")

    if destination_root.exists():
        shutil.rmtree(destination_root)
    destination_root.mkdir(parents=True)
    destination_db = destination_root / "app.db"
    copy_database(source_db, destination_db)

    with sqlite3.connect(destination_db) as connection:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA foreign_keys = OFF")
        active_versions = connection.execute(
            """
            SELECT t.id, t.active_version_id, v.source_job_id,
                   v.clean_template_artifact_id, v.mask_artifact_id
            FROM templates AS t
            JOIN template_versions AS v ON v.id = t.active_version_id
            WHERE t.active_version_id IS NOT NULL
            """
        ).fetchall()
        if not active_versions:
            raise RuntimeError("The source database has no active templates")

        template_ids = {row[0] for row in active_versions}
        version_ids = {row[1] for row in active_versions}
        job_ids = {row[2] for row in active_versions if row[2]}
        artifact_ids = {
            artifact_id
            for row in active_versions
            for artifact_id in row[3:]
            if artifact_id
        }
        artifact_rows = connection.execute(
            f"""
            SELECT id, relative_path
            FROM job_artifacts
            WHERE id IN ({placeholders(artifact_ids)})
               OR (job_id IN ({placeholders(job_ids)}) AND artifact_name = 'normalized')
            """,
            (*artifact_ids, *job_ids),
        ).fetchall()
        config_rows = connection.execute(
            "SELECT config_relative_path FROM template_versions "
            f"WHERE id IN ({placeholders(version_ids)})",
            tuple(version_ids),
        ).fetchall()
        artifact_ids = {row[0] for row in artifact_rows}
        required_paths = {row[1] for row in artifact_rows} | {row[0] for row in config_rows}

        for relative_path in required_paths:
            source_path = source_root / relative_path
            if not source_path.is_file():
                raise FileNotFoundError(f"Referenced template file is missing: {source_path}")
            destination_path = destination_root / relative_path
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_path)

        connection.execute("DELETE FROM confirmed_record_fields")
        connection.execute("DELETE FROM confirmed_results")
        connection.execute("DELETE FROM ocr_result_revisions")
        connection.execute("DELETE FROM ocr_results")
        connection.execute("DELETE FROM job_events")
        connection.execute("DELETE FROM job_inputs")
        connection.execute(
            f"DELETE FROM job_artifacts WHERE id NOT IN ({placeholders(artifact_ids)})",
            tuple(artifact_ids),
        )
        connection.execute(
            f"DELETE FROM processing_jobs WHERE id NOT IN ({placeholders(job_ids)})",
            tuple(job_ids),
        )
        connection.execute(
            f"DELETE FROM template_versions WHERE id NOT IN ({placeholders(version_ids)})",
            tuple(version_ids),
        )
        connection.execute(
            f"DELETE FROM templates WHERE id NOT IN ({placeholders(template_ids)})",
            tuple(template_ids),
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.commit()
        connection.execute("VACUUM")


if __name__ == "__main__":
    options = parse_args()
    export(options.source, options.destination)
