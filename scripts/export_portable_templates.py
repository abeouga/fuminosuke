"""Export active local templates as a Git-trackable bootstrap data set.

The local runtime database contains OCR jobs, uploaded source images, confirmed
records, and logs in addition to templates. Those records are deliberately not
portable. This tool retains only active templates, their source jobs, template
artifacts, template YAML files, and the optional sire-name candidate pool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import struct
import unicodedata
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "backend" / "data"
DEFAULT_DESTINATION = PROJECT_ROOT / "backend" / "bootstrap" / "templates"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the destination bundle without exporting it again",
    )
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


def normalized_template_name(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().casefold()


def png_dimensions(content: bytes) -> tuple[int, int]:
    if (
        len(content) < 24
        or content[:8] != b"\x89PNG\r\n\x1a\n"
        or content[12:16] != b"IHDR"
    ):
        raise ValueError("Template artifact is not a valid PNG")
    return struct.unpack(">II", content[16:24])


def is_portable_active_template(
    *, status: str, quality_json: str, layout_json: str
) -> bool:
    quality = json.loads(quality_json)
    layout = json.loads(layout_json)
    warnings = quality.get("warnings", [])
    boundary_fallback = quality.get("fallbackUsed") is True or (
        isinstance(warnings, list) and "FULL_IMAGE_BOUNDARY_FALLBACK" in warnings
    )
    relations = layout.get("relations", [])
    array_relations = [
        relation
        for relation in relations
        if isinstance(relation, dict)
        and isinstance(relation.get("targetPath"), str)
        and relation["targetPath"].startswith(("saleCalves.", "calvings."))
    ]
    grouped_inputs = {
        input_id
        for group in layout.get("recordGroups", [])
        if isinstance(group, dict)
        for input_id in group.get("inputCellIds", [])
    }
    record_structure_valid = not array_relations or (
        layout.get("recordStructureVerified") is True
        and bool(layout.get("recordSections"))
        and bool(layout.get("recordGroups"))
        and all(
            relation.get("inputCellId") in grouped_inputs
            for relation in array_relations
        )
    )
    return (
        status == "ACTIVE"
        and not boundary_fallback
        and quality.get("layoutFinalized") is True
        and quality.get("layoutVerified") is True
        and isinstance(layout.get("cells"), list)
        and bool(layout["cells"])
        and isinstance(relations, list)
        and bool(relations)
        and record_structure_valid
    )


def export(source_root: Path, destination_root: Path) -> dict[str, int]:
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
        active_candidates = connection.execute(
            """
            SELECT t.id, t.name, t.active_version_id, v.source_job_id,
                   v.clean_template_artifact_id, v.mask_artifact_id,
                   v.status, v.quality_json, v.layout_json
            FROM templates AS t
            JOIN template_versions AS v ON v.id = t.active_version_id
            WHERE t.active_version_id IS NOT NULL
            """
        ).fetchall()
        active_versions = [
            row
            for row in active_candidates
            if is_portable_active_template(
                status=row[6], quality_json=row[7], layout_json=row[8]
            )
        ]
        if not active_versions:
            raise RuntimeError(
                "The source database has no safe, verified active templates"
            )

        names: set[str] = set()
        for row in active_versions:
            normalized_name = normalized_template_name(row[1])
            if normalized_name in names:
                raise RuntimeError(
                    f"Duplicate active template name cannot be exported: {row[1]}"
                )
            names.add(normalized_name)

        template_ids = {row[0] for row in active_versions}
        version_ids = {row[2] for row in active_versions}
        job_ids = {row[3] for row in active_versions if row[3]}
        artifact_ids = {
            artifact_id
            for row in active_versions
            for artifact_id in row[4:6]
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
        required_paths = {row[1] for row in artifact_rows} | {
            row[0] for row in config_rows
        }

        for relative_path in required_paths:
            source_path = source_root / relative_path
            if not source_path.is_file():
                raise FileNotFoundError(
                    f"Referenced template file is missing: {source_path}"
                )
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
    return validate_bundle(destination_root)


def validate_bundle(root: Path) -> dict[str, int]:
    root = root.resolve()
    database_path = root / "app.db"
    if not database_path.is_file():
        raise FileNotFoundError(f"Portable database is missing: {database_path}")

    referenced_files: set[Path] = {database_path}
    with sqlite3.connect(
        f"file:{database_path.as_posix()}?mode=ro", uri=True
    ) as connection:
        connection.row_factory = sqlite3.Row
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Portable database integrity check failed: {integrity}")
        foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_issues:
            raise RuntimeError("Portable database has foreign-key violations")

        forbidden_tables = (
            "confirmed_record_fields",
            "confirmed_results",
            "job_events",
            "job_inputs",
            "ocr_result_revisions",
            "ocr_results",
        )
        for table in forbidden_tables:
            count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if count:
                raise RuntimeError(f"Portable database contains forbidden {table} rows")

        templates = connection.execute(
            """
            SELECT t.id, t.name, t.active_version_id,
                   v.id AS version_id, v.status, v.source_job_id,
                   v.clean_template_artifact_id, v.mask_artifact_id,
                   v.canonical_width, v.canonical_height,
                   v.quality_json, v.layout_json, v.config_relative_path,
                   j.status AS job_status, j.job_type
            FROM templates AS t
            JOIN template_versions AS v ON v.id = t.active_version_id
            JOIN processing_jobs AS j ON j.id = v.source_job_id
            ORDER BY t.created_at, t.id
            """
        ).fetchall()
        if not templates:
            raise RuntimeError("Portable database has no templates")
        if connection.execute("SELECT COUNT(*) FROM template_versions").fetchone()[
            0
        ] != len(templates):
            raise RuntimeError("Portable database contains inactive template versions")
        if connection.execute("SELECT COUNT(*) FROM processing_jobs").fetchone()[
            0
        ] != len(templates):
            raise RuntimeError("Portable database contains unrelated processing jobs")

        names: set[str] = set()
        for template in templates:
            normalized_name = normalized_template_name(template["name"])
            if not normalized_name or normalized_name in names:
                raise RuntimeError(
                    "Portable template names must be non-empty and unique"
                )
            names.add(normalized_name)
            if template["active_version_id"] != template["version_id"]:
                raise RuntimeError(
                    "Portable template does not point to its exported version"
                )
            if (
                template["job_status"] != "CONFIRMED"
                or template["job_type"] != "PREPARE_TEMPLATE"
            ):
                raise RuntimeError("Portable template source job is not confirmed")
            if not is_portable_active_template(
                status=template["status"],
                quality_json=template["quality_json"],
                layout_json=template["layout_json"],
            ):
                raise RuntimeError(f"Unsafe template was exported: {template['name']}")

            layout = json.loads(template["layout_json"])
            cell_ids = {
                cell.get("id") for cell in layout["cells"] if isinstance(cell, dict)
            }
            for relation in layout["relations"]:
                if (
                    not isinstance(relation, dict)
                    or relation.get("inputCellId") not in cell_ids
                ):
                    raise RuntimeError(
                        "Portable layout relation has an invalid input cell"
                    )
                label_ids = relation.get("labelCellIds", [])
                if not isinstance(label_ids, list) or not set(label_ids) <= cell_ids:
                    raise RuntimeError(
                        "Portable layout relation has an invalid label cell"
                    )
                if (
                    not isinstance(relation.get("targetPath"), str)
                    or not relation["targetPath"]
                ):
                    raise RuntimeError("Portable layout relation has no target path")

            config_path = root / template["config_relative_path"]
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise RuntimeError(
                    f"Portable template config is invalid: {config_path}"
                )
            if (
                config.get("canonicalWidth") != template["canonical_width"]
                or config.get("canonicalHeight") != template["canonical_height"]
            ):
                raise RuntimeError(
                    "Portable template config dimensions do not match the database"
                )
            referenced_files.add(config_path)

            artifacts = connection.execute(
                """
                SELECT id, artifact_name, relative_path, sha256, width, height
                FROM job_artifacts WHERE job_id = ?
                """,
                (template["source_job_id"],),
            ).fetchall()
            by_name = {artifact["artifact_name"]: artifact for artifact in artifacts}
            if set(by_name) != {"cleanTemplate", "handwritingMask", "normalized"}:
                raise RuntimeError("Portable template has an incomplete artifact set")
            if by_name["cleanTemplate"]["id"] != template["clean_template_artifact_id"]:
                raise RuntimeError("Portable clean-template reference is inconsistent")
            if by_name["handwritingMask"]["id"] != template["mask_artifact_id"]:
                raise RuntimeError(
                    "Portable handwriting-mask reference is inconsistent"
                )
            for artifact in artifacts:
                artifact_path = root / artifact["relative_path"]
                content = artifact_path.read_bytes()
                if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
                    raise RuntimeError(
                        f"Portable artifact hash mismatch: {artifact_path}"
                    )
                if png_dimensions(content) != (artifact["width"], artifact["height"]):
                    raise RuntimeError(
                        f"Portable artifact dimensions mismatch: {artifact_path}"
                    )
                referenced_files.add(artifact_path)

        sire_names = connection.execute(
            "SELECT name, normalized_name FROM sire_names ORDER BY created_at, id"
        ).fetchall()
        normalized_sire_names: set[str] = set()
        for item in sire_names:
            normalized = normalized_template_name(item["name"])
            if (
                normalized != item["normalized_name"]
                or normalized in normalized_sire_names
            ):
                raise RuntimeError(
                    "Portable sire-name master is not normalized and unique"
                )
            normalized_sire_names.add(normalized)

    actual_files = {path.resolve() for path in root.rglob("*") if path.is_file()}
    if actual_files != referenced_files:
        unexpected = sorted(
            str(path.relative_to(root)) for path in actual_files - referenced_files
        )
        missing = sorted(
            str(path.relative_to(root)) for path in referenced_files - actual_files
        )
        raise RuntimeError(
            f"Portable bundle file mismatch; unexpected={unexpected}, missing={missing}"
        )
    return {"templates": len(templates), "sire_names": len(sire_names)}


if __name__ == "__main__":
    options = parse_args()
    summary = (
        validate_bundle(options.destination)
        if options.validate_only
        else export(options.source, options.destination)
    )
    print(
        f"portable_templates={summary['templates']} "
        f"portable_sire_names={summary['sire_names']} validation=passed"
    )
