"""Confirmed documents of one tenant database, projected read-only.

Confirmation is decided exactly as the review/export paths decide it (job status plus the
revision/snapshot match); a surviving confirmed row alone is never enough. Nothing here
writes, and no value is normalized, deduplicated or merged across documents.
"""

from __future__ import annotations

import builtins
import unicodedata
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import PagedConfirmation, PagedReview, ProcessingJob, TemplateVersion
from app.db.session import Database
from app.domain.enums import JobStatus, JobType
from app.domain.field_targets import field_key
from app.domain.ocr_models import OcrDocument
from app.editing.errors import reject
from app.layout_analysis.models import LayoutDefinition
from app.repositories.ocr_result_repository import OcrResultRepository
from app.services.document_series import DocumentSeries
from app.services.paged_document_snapshot import load_document_snapshot
from app.services.review_block_order import version_layout, with_blocks
from app.services.template_item_join_service import item_joins, joined_text
from app.structured.contracts import SCHEMA_VERSION
from app.structured.projection import project_values

SUMMARY_KEYS = (
    "jobId",
    "kind",
    "templateId",
    "templateVersionId",
    "templateName",
    "documentName",
    "confirmedAt",
    "confirmedRevision",
    "identity",
    "seriesId",
    "seriesVersion",
    "thumbnailUrl",
)


def _fold(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def page_image_url(job_id: str, page_id: str | None) -> str:
    """The aligned (registered) page image, in the coordinates of the template's cells."""
    name = "alignedSource" if page_id is None else f"page.{page_id}.alignedSource"
    return f"/api/v1/jobs/{quote(job_id, safe='')}/artifacts/{quote(name, safe='')}"


def _box(reading: object, mapping: dict[str, Any]) -> dict[str, float] | None:
    """The reading's own box when stored, else the mapped cell (as the review screen shows it)."""
    source = reading.get("boundingBox") if isinstance(reading, dict) else None
    if not isinstance(source, dict):
        source = mapping
    values = [source.get(key) for key in ("x", "y", "width", "height")]
    numbers = [float(v) for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
    if len(numbers) != 4 or numbers[2] <= 0 or numbers[3] <= 0:
        return None
    return dict(zip(("x", "y", "width", "height"), numbers, strict=True))


def field_crop_url(job_id: str, field_id: str | None, page_id: str | None) -> str | None:
    """The stored aligned crop of one cell (the same artifact the review screen shows)."""
    if field_id is None:
        return None
    name = f"fieldCrop.{field_id}.alignedColor"
    if page_id is not None:
        name = f"page.{page_id}.{name}"
    return f"/api/v1/jobs/{quote(job_id, safe='')}/artifacts/{quote(name, safe='')}"


class StructuredDocumentService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self, job_id: str) -> dict[str, Any]:
        with self._database.session() as session:
            job = session.get(ProcessingJob, job_id)
            if job is None or job.job_type != JobType.EXTRACT_DOCUMENT.value:
                raise reject(
                    "STRUCTURED_DOCUMENT_NOT_FOUND", "指定された読み取り結果が見つかりません。", 404
                )
            _, versions = DocumentSeries(session).view()
            document = self._with_series(self._project(job), versions)
            document["joinedFields"] = joined_fields(
                document["fields"], item_joins(session, document["templateId"])
            )
        for reading in document["fields"]:
            reading["cropUrl"] = field_crop_url(job_id, reading["fieldId"], reading["pageId"])
        return document

    def list(
        self, *, limit: int, template_id: str | None = None, query: str | None = None
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise reject("INVALID_ARGUMENT", "取得件数は1〜1000で指定してください。", 422)
        needle = _fold(query.strip()) if query and query.strip() else None
        items: list[dict[str, Any]] = []
        with self._database.session() as session:
            # Only the newest confirmed version of a document series is listed.
            superseded, versions = DocumentSeries(session).view()
            for job in self._confirmed_jobs(session):
                if job.id in superseded:
                    continue
                try:
                    document = self._with_series(self._project(job), versions)
                except AppError:
                    continue  # Not confirmed by the authoritative check; never listed.
                if template_id and document["templateId"] != template_id:
                    continue
                if needle is not None and not self._matches(document, needle):
                    continue
                items.append({key: document[key] for key in SUMMARY_KEYS})
                if len(items) >= limit:
                    break
        return items

    def field_names(
        self, template_id: str, *, documents: int = 20
    ) -> builtins.list[dict[str, Any]]:
        """Field and column names of a template, taken from its newest confirmed documents.

        Only names the accepted structure already carries; nothing is inferred. A name is
        listed once per (kind, table title, label), in first-seen order.
        """
        seen: set[tuple[str, str | None, str]] = set()
        names: builtins.list[dict[str, Any]] = []

        def add(kind: str, table_title: str | None, label: str) -> None:
            key = (kind, table_title, label)
            if key not in seen:
                seen.add(key)
                names.append({"label": label, "kind": kind, "tableTitle": table_title})

        for summary in self.list(limit=documents, template_id=template_id):
            try:
                document = self.get(summary["jobId"])
            except AppError:
                continue  # Reopened between listing and reading.
            for reading in document["fields"]:
                if reading["tableId"] is None:
                    add("field", None, reading["label"])
            for table in document["tables"]:
                for column in table["columns"]:
                    add("column", table["title"], column["label"])
        return names

    @staticmethod
    def _confirmed_jobs(session: Session) -> Iterator[ProcessingJob]:
        statement = (
            select(ProcessingJob)
            .where(
                ProcessingJob.job_type == JobType.EXTRACT_DOCUMENT.value,
                ProcessingJob.status == JobStatus.CONFIRMED.value,
            )
            .order_by(
                ProcessingJob.confirmed_at.desc(),
                ProcessingJob.created_at.desc(),
                ProcessingJob.id.desc(),
            )
        )
        yield from session.scalars(statement)

    @staticmethod
    def _matches(document: dict[str, Any], needle: str) -> bool:
        haystack = [document.get("documentName")]
        haystack.extend(item.get("value") for item in document["identity"])
        return any(isinstance(text, str) and needle in _fold(text) for text in haystack)

    @staticmethod
    def _with_series(
        document: dict[str, Any], versions: dict[str, tuple[str, int]]
    ) -> dict[str, Any]:
        series_id, version = versions.get(document["jobId"], (document["jobId"], 1))
        return {**document, "seriesId": series_id, "seriesVersion": version}

    def _project(self, job: ProcessingJob) -> dict[str, Any]:
        if "jobPages" in job.config_snapshot_json:
            return self._paged(job)
        return self._single(job)

    @staticmethod
    def _not_confirmed() -> AppError:
        return reject("STRUCTURED_NOT_CONFIRMED", "確定済みの結果だけ取得できます。", 409)

    def _single(self, job: ProcessingJob) -> dict[str, Any]:
        session = Session.object_session(job)
        assert session is not None
        repository = OcrResultRepository(session)
        stored = repository.get_by_job(job.id)
        confirmed = repository.get_confirmed(job.id)
        if (
            job.status != JobStatus.CONFIRMED.value
            or stored is None
            or confirmed is None
            or confirmed.ocr_result_revision != stored.revision
        ):
            raise self._not_confirmed()
        snapshot = job.config_snapshot_json
        template_snapshot = snapshot.get("templateSnapshot")
        template_snapshot = template_snapshot if isinstance(template_snapshot, dict) else {}
        config = template_snapshot.get("config")
        config = config if isinstance(config, dict) else {}
        document = OcrDocument.model_validate(confirmed.confirmed_json).model_dump(
            mode="json", by_alias=True
        )
        # Blocks from the top of the form, for the shelf (display only).
        structure = with_blocks(
            snapshot.get("reviewStructure"),
            snapshot.get("fieldMappings"),
            version_layout(session, job.template_version_id),
        )
        body = project_values(
            document,
            mappings=snapshot.get("fieldMappings"),
            structure=structure,
            logical_ids=snapshot.get("logicalFieldIds"),
            identity_fields=config.get("documentIdentityFields"),
        )
        mappings = {
            m["mappingId"]: m
            for m in snapshot.get("fieldMappings") or []
            if isinstance(m, dict) and isinstance(m.get("mappingId"), str)
        }
        for reading in body["fields"]:
            reading["box"] = _box(
                document["fields"].get(reading["key"]), mappings.get(reading["fieldId"], {})
            )
            reading["choice"] = mappings.get(reading["fieldId"], {}).get("mappingType") == "mark"
        body["blocks"] = structure.get("blocks") if isinstance(structure, dict) else None
        image = page_image_url(job.id, None)
        version = (
            session.get(TemplateVersion, job.template_version_id)
            if job.template_version_id
            else None
        )
        return {
            "schemaVersion": SCHEMA_VERSION,
            "jobId": job.id,
            "kind": "single",
            "templateId": str(snapshot.get("templateId") or document["templateId"]),
            "templateVersionId": job.template_version_id or snapshot.get("templateVersionId"),
            "templateName": str(config.get("name") or ""),
            "documentName": confirmed.document_name,
            "confirmedAt": confirmed.confirmed_at,
            "confirmedRevision": confirmed.ocr_result_revision,
            "thumbnailUrl": f"{image}/thumbnail",
            "pages": [{"pageId": None, "pageNumber": 1, "imageUrl": image}],
            "headings": heading_cells(
                version.layout_json if version is not None else None,
                snapshot.get("reviewStructure"),
            ),
            **body,
        }

    def _paged(self, job: ProcessingJob) -> dict[str, Any]:
        session = Session.object_session(job)
        assert session is not None
        stored = session.get(PagedReview, job.id)
        if job.status != JobStatus.CONFIRMED.value or stored is None:
            raise self._not_confirmed()
        confirmed = session.get(PagedConfirmation, (job.id, stored.revision))
        if (
            confirmed is None
            or confirmed.snapshot_sha256 != stored.snapshot_sha256
            or confirmed.pages_json != stored.current_pages_json
            or confirmed.document_name != stored.document_name
            or job.confirmed_at is None
        ):
            raise self._not_confirmed()
        snapshot = load_document_snapshot(job)
        pages = job.config_snapshot_json["jobPages"]["pages"]
        page_by_mapping: dict[str, str] = {}
        mapping_by_id: dict[str, dict[str, Any]] = {}
        readings: dict[str, Any] = {}
        labels: dict[str, str] = {}
        for page in pages:
            page_id = page["pageId"]
            payload = confirmed.pages_json.get(page_id)
            if not isinstance(payload, dict):
                raise self._not_confirmed()
            document = OcrDocument.model_validate(payload["result"]).model_dump(
                mode="json", by_alias=True
            )
            for mapping in page["fieldMappings"]:
                key = field_key(mapping["targetPath"])
                reading = document["fields"].get(key) if key is not None else None
                if key is None or reading is None or key in readings:
                    raise reject(
                        "STRUCTURED_SNAPSHOT_INVALID", "ページの項目所有権を確認できません。", 409
                    )
                page_by_mapping[mapping["mappingId"]] = page_id
                mapping_by_id[mapping["mappingId"]] = mapping
                readings[key] = reading
                label = document["fieldLabels"].get(key)
                if isinstance(label, str):
                    labels[key] = label
        config = snapshot["config"]
        structure = with_blocks(
            snapshot.get("reviewStructure"), config["fieldMappings"], snapshot.get("layout")
        )
        body = project_values(
            {"fields": readings, "fieldLabels": labels},
            mappings=[m for m in config["fieldMappings"] if m.get("enabled", True)],
            structure=structure,
            logical_ids=snapshot.get("logicalFieldIds"),
            identity_fields=config.get("documentIdentityFields"),
            page_by_mapping=page_by_mapping,
        )
        for reading in body["fields"]:
            reading["box"] = _box(
                readings.get(reading["key"]), mapping_by_id.get(reading["fieldId"], {})
            )
            reading["choice"] = (
                mapping_by_id.get(reading["fieldId"], {}).get("mappingType") == "mark"
            )
        body["blocks"] = structure.get("blocks") if isinstance(structure, dict) else None
        page_list = [
            {
                "pageId": page["pageId"],
                "pageNumber": index,
                "imageUrl": page_image_url(job.id, page["pageId"]),
            }
            for index, page in enumerate(pages, start=1)
        ]
        return {
            "schemaVersion": SCHEMA_VERSION,
            "jobId": job.id,
            "kind": "paged",
            "templateId": str(snapshot.get("templateId") or ""),
            "templateVersionId": snapshot.get("templateVersionId"),
            "templateName": str(config.get("name") or ""),
            "documentName": confirmed.document_name,
            "confirmedAt": confirmed.confirmed_at,
            "confirmedRevision": confirmed.revision,
            "thumbnailUrl": f"{page_list[0]['imageUrl']}/thumbnail" if page_list else None,
            "pages": page_list,
            **body,
        }


def heading_cells(layout_json: object, structure: object) -> builtins.list[dict[str, Any]]:
    """Where the headings, table titles, column names and item names are printed.

    Positions come from the job's template version (a used version is never edited); what
    each one names comes from the review structure fixed when the job was accepted. Display
    only: a layout that no longer validates just yields no headings.
    """
    if not isinstance(layout_json, dict) or not layout_json or not isinstance(structure, dict):
        return []
    try:
        layout = LayoutDefinition.model_validate(layout_json)
    except ValueError:
        return []
    # Page-local virtual headings belong to paged templates only.
    cells = {cell.id: cell for cell in layout.cells if cell.page_range is None}
    relations = {relation.id: relation for relation in layout.relations}
    sections = {section.id: section for section in layout.record_sections}
    found: dict[str, dict[str, Any]] = {}

    def add(cell_id: object, kind: str, **names: Any) -> dict[str, Any] | None:
        cell = cells.get(cell_id) if isinstance(cell_id, str) else None
        if cell is None:
            return None
        if cell.id not in found:
            found[cell.id] = {
                "cellId": cell.id,
                "kind": kind,
                "box": {"x": cell.x, "y": cell.y, "width": cell.width, "height": cell.height},
                "pageId": None,
                "section": None,
                "tableIds": [],
                "columnId": None,
                "fieldIds": [],
                **names,
            }
        return found[cell.id]

    def records(value: object) -> builtins.list[dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    for table in records(structure.get("recordSections")):
        section = sections.get(str(table.get("id")))
        if section is not None:
            heading = add(section.parent_label_cell_id, "table")
            if heading is not None:
                heading["tableIds"].append(table["id"])  # One heading may head two tables.
        for column in records(table.get("columns")):
            add(column.get("id"), "column", tableIds=[table["id"]], columnId=column.get("id"))
    for parent in records(structure.get("parentSections")):
        title = str(parent.get("title") or "項目")
        heading = add(parent.get("id"), "section", section=title)
        if heading is not None:
            heading["section"] = title  # A table's heading may also head plain items.
    for field in records(structure.get("fields")):
        relation = relations.get(str(field.get("relationId")))
        for label_id in relation.label_cell_ids if relation is not None else []:
            heading = add(label_id, "item")
            if (
                heading is not None
                and heading["kind"] == "item"
                and field["id"] not in heading["fieldIds"]
            ):
                heading["fieldIds"].append(field["id"])
    return builtins.list(found.values())


def joined_fields(
    readings: builtins.list[dict[str, Any]], joins: builtins.list[dict[str, Any]]
) -> builtins.list[dict[str, Any]]:
    """Items shown as one value (「調査場所」), built from the stored readings; display only."""
    by_path = {r["targetPath"]: r for r in readings if r["tableId"] is None}
    result: builtins.list[dict[str, Any]] = []
    for join in joins:
        if join.get("kind", "join") != "join":
            continue
        members = [
            m for m in join.get("members", [])
            if isinstance(m, dict) and m.get("targetPath") in by_path
        ]
        if len(members) < 2:
            continue
        parts = [by_path[m["targetPath"]] for m in members]
        value = joined_text(
            members, {r["targetPath"]: r["value"] for r in parts}, join.get("separator") or ""
        )
        statuses = {r["status"] for r in parts}
        result.append({
            "id": str(join.get("id") or join["name"]),
            "name": join["name"],
            "section": parts[0]["section"],
            "value": value or None,
            "status": "uncertain" if "uncertain" in statuses else "readable" if value else "blank",
            "fieldIds": [r["fieldId"] for r in parts],
        })
    return result
