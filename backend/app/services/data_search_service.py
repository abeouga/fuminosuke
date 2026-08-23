from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ConfirmedResult,
    LedgerDocument,
    LedgerDocumentPage,
    ProcessingJob,
    Template,
    TemplateCandidateBinding,
    TemplateCandidateSet,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.enums import JobStatus
from app.domain.template_candidates import normalize_candidate_target
from app.domain.template_config import parse_template_config
from app.storage.local_storage import LocalStorageService

_READING_KEYS = {"raw", "normalized", "status"}
_DATE_PATTERN = re.compile(r"^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?$")


@dataclass(frozen=True, slots=True)
class SearchValue:
    field_key: str
    field_label: str
    section_key: str
    section_title: str
    record_order: int | None
    record_key: str | None
    value: str
    normalized_value: str
    numeric_value: Decimal | None
    date_value: date | None
    value_type: str
    repeated: bool
    identity: bool


@dataclass(frozen=True, slots=True)
class SearchDocument:
    document_id: str
    template_id: str
    template_name: str
    source_job_id: str
    confirmed_at: Any
    page_count: int
    spreadsheet_url: str | None
    document: dict[str, Any]
    job: ProcessingJob
    template_config: dict[str, Any]


class DataSearchService:
    """Search confirmed document values without assuming a cattle schema."""

    def __init__(
        self,
        database: Database,
        storage: LocalStorageService | None = None,
    ) -> None:
        self._database = database
        self._storage = storage

    def catalog(self) -> dict[str, Any]:
        with self._database.session() as session:
            documents = self._documents(session)
            suggestions = self._candidate_suggestions(session)
            by_template: dict[str, dict[str, Any]] = {}
            for item in documents:
                template = by_template.setdefault(
                    item.template_id,
                    {
                        "templateId": item.template_id,
                        "templateName": item.template_name,
                        "documentCount": 0,
                        "fields": {},
                    },
                )
                template["documentCount"] += 1
                fields = template["fields"]
                for value in self._search_values(item):
                    existing = fields.get(value.field_key)
                    if existing is None:
                        fields[value.field_key] = {
                            "fieldKey": value.field_key,
                            "label": value.field_label,
                            "sectionTitle": value.section_title,
                            "valueType": value.value_type,
                            "repeated": value.repeated,
                            "identity": value.identity,
                            "suggestions": suggestions.get(
                                (item.template_id, value.field_key),
                                [],
                            ),
                        }
                    elif value.identity:
                        existing["identity"] = True
            templates = []
            for template in by_template.values():
                fields = list(template.pop("fields").values())
                fields.sort(
                    key=lambda field: (
                        not field["identity"],
                        field["sectionTitle"],
                        field["label"],
                    )
                )
                template["fields"] = fields
                templates.append(template)
            templates.sort(key=lambda item: item["templateName"])
            return {
                "count": len(documents),
                "templates": templates,
            }

    def search(
        self,
        *,
        query: str,
        template_id: str | None,
        filters: list[dict[str, str | None]],
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        normalized_query = self._normalize_text(query) if query.strip() else ""
        with self._database.session() as session:
            documents = self._documents(session)
            results: list[dict[str, Any]] = []
            for item in documents:
                if template_id and item.template_id != template_id:
                    continue
                values = self._search_values(item)
                quick_matches = self._quick_matches(
                    item,
                    values,
                    normalized_query,
                )
                if normalized_query and not quick_matches:
                    continue
                filter_matches = self._filter_matches(values, filters)
                if filter_matches is None:
                    continue
                matched = self._deduplicate_values([*quick_matches, *filter_matches])
                title, title_label, subtitle, subtitle_label = self._display(item, values)
                results.append(
                    {
                        "documentId": item.document_id,
                        "templateId": item.template_id,
                        "templateName": item.template_name,
                        "sourceJobId": item.source_job_id,
                        "confirmedAt": item.confirmed_at,
                        "pageCount": item.page_count,
                        "spreadsheetUrl": item.spreadsheet_url,
                        "displayTitle": title,
                        "displayTitleLabel": title_label,
                        "displaySubtitle": subtitle,
                        "displaySubtitleLabel": subtitle_label,
                        "matches": [self._match_payload(value) for value in matched[:12]],
                    }
                )
            results.sort(key=lambda item: item["confirmedAt"], reverse=True)
            total = len(results)
            start = (page - 1) * page_size
            return {
                "query": query,
                "count": total,
                "page": page,
                "pageSize": page_size,
                "items": results[start : start + page_size],
            }

    @staticmethod
    def _match_payload(value: SearchValue) -> dict[str, Any]:
        return {
            "fieldKey": value.field_key,
            "label": value.field_label,
            "sectionTitle": value.section_title,
            "recordOrder": value.record_order,
            "recordKey": value.record_key,
            "value": value.value,
        }

    @classmethod
    def _quick_matches(
        cls,
        document: SearchDocument,
        values: list[SearchValue],
        query: str,
    ) -> list[SearchValue]:
        if not query:
            return []
        matches = [
            value
            for value in values
            if value.identity and query in value.normalized_value
        ]
        if query in cls._normalize_text(document.template_name):
            return matches or values[:1]
        return matches

    @classmethod
    def _filter_matches(
        cls,
        values: list[SearchValue],
        filters: list[dict[str, str | None]],
    ) -> list[SearchValue] | None:
        if not filters:
            return []
        matched: list[SearchValue] = []
        repeated_filters: dict[str, list[dict[str, str | None]]] = {}
        scalar_filters: list[dict[str, str | None]] = []
        repeated_sections = {
            value.field_key: value.section_key for value in values if value.repeated
        }
        for search_filter in filters:
            section_key = repeated_sections.get(search_filter.get("fieldKey") or "")
            if section_key:
                repeated_filters.setdefault(section_key, []).append(search_filter)
            else:
                scalar_filters.append(search_filter)
        for search_filter in scalar_filters:
            candidates = [
                value
                for value in values
                if value.field_key == search_filter.get("fieldKey")
                and cls._matches_operator(value, search_filter)
            ]
            if not candidates:
                return None
            matched.extend(candidates)
        for section_key, section_filters in repeated_filters.items():
            orders = {
                value.record_order
                for value in values
                if value.section_key == section_key and value.record_order is not None
            }
            matching_order: int | None = None
            row_values: list[SearchValue] = []
            for order in sorted(item for item in orders if item is not None):
                current_matches: list[SearchValue] = []
                for search_filter in section_filters:
                    candidates = [
                        value
                        for value in values
                        if value.section_key == section_key
                        and value.record_order == order
                        and value.field_key == search_filter.get("fieldKey")
                        and cls._matches_operator(value, search_filter)
                    ]
                    if not candidates:
                        break
                    current_matches.extend(candidates)
                else:
                    matching_order = order
                    row_values = current_matches
                    break
            if matching_order is None:
                return None
            matched.extend(row_values)
        return matched

    @classmethod
    def _matches_operator(
        cls,
        value: SearchValue,
        search_filter: dict[str, str | None],
    ) -> bool:
        operator = search_filter.get("operator") or "contains"
        raw = search_filter.get("value") or ""
        raw_to = search_filter.get("valueTo") or ""
        normalized = cls._normalize_text(raw)
        if operator == "exact":
            return value.normalized_value == normalized
        if operator == "contains":
            return normalized in value.normalized_value
        if operator == "startsWith":
            return value.normalized_value.startswith(normalized)
        if operator == "between" and value.date_value is not None:
            lower_date = cls._parse_date(raw)
            upper_date = cls._parse_date(raw_to)
            return (
                lower_date is not None
                and upper_date is not None
                and lower_date <= value.date_value <= upper_date
            )
        if operator in {"gte", "lte", "between"}:
            if value.numeric_value is None:
                return False
            try:
                lower = Decimal(raw.replace(",", "").strip())
                upper = Decimal(raw_to.replace(",", "").strip()) if raw_to else None
            except InvalidOperation:
                return False
            if operator == "gte":
                return value.numeric_value >= lower
            if operator == "lte":
                return value.numeric_value <= lower
            return upper is not None and lower <= value.numeric_value <= upper
        if operator in {"on", "before", "after"}:
            expected = cls._parse_date(raw)
            if value.date_value is None or expected is None:
                return False
            if operator == "on":
                return value.date_value == expected
            if operator == "before":
                return value.date_value <= expected
            return value.date_value >= expected
        return False

    def _display(
        self,
        document: SearchDocument,
        values: list[SearchValue],
    ) -> tuple[str, str, str | None, str | None]:
        identity = [value for value in values if value.identity and not value.repeated]
        config = document.template_config
        raw_fields = config.get("documentIdentityFields")
        if not isinstance(raw_fields, list):
            legacy = config.get("documentIdentity")
            raw_fields = [legacy] if isinstance(legacy, dict) else []
        by_key = {value.field_key: value for value in identity}
        title: SearchValue | None = None
        subtitle: SearchValue | None = None
        for raw in raw_fields:
            if not isinstance(raw, dict) or not isinstance(raw.get("targetPath"), str):
                continue
            selected = by_key.get(normalize_candidate_target(raw["targetPath"]))
            if selected is None:
                continue
            if raw.get("displayRole") == "title":
                title = selected
            elif raw.get("displayRole") == "subtitle":
                subtitle = selected
        if title is None:
            title = next(
                (value for value in identity if "name" in value.field_key.casefold()),
                identity[0] if identity else None,
            )
        if subtitle is None:
            subtitle = next(
                (
                    value
                    for value in identity
                    if value.field_key != getattr(title, "field_key", None)
                ),
                None,
            )
        return (
            title.value if title else document.template_name,
            title.field_label if title else "書類",
            subtitle.value if subtitle else None,
            subtitle.field_label if subtitle else None,
        )

    def _search_values(self, item: SearchDocument) -> list[SearchValue]:
        snapshot_mappings = item.job.config_snapshot_json.get("fieldMappings")
        current_mappings = item.template_config.get("fieldMappings")
        mapping_by_target: dict[str, dict[str, Any]] = {}
        for raw_mappings in (snapshot_mappings, current_mappings):
            if not isinstance(raw_mappings, list):
                continue
            for raw in raw_mappings:
                if isinstance(raw, dict) and isinstance(raw.get("targetPath"), str):
                    mapping_by_target[raw["targetPath"]] = raw
        mappings = list(mapping_by_target.values())
        identity_paths = self._identity_paths(item)
        generic_metadata = self._generic_metadata(item.document)
        values: list[SearchValue] = []
        if mappings:
            for raw in mappings:
                if not isinstance(raw, dict):
                    continue
                target_path = raw.get("targetPath")
                if not isinstance(target_path, str):
                    continue
                reading = self._path(item.document, target_path)
                selected = self._reading_value(reading)
                if selected is None:
                    continue
                source_region = reading.get("sourceRegionId") if isinstance(reading, dict) else None
                metadata = (
                    generic_metadata.get(source_region)
                    if isinstance(source_region, str)
                    else None
                )
                values.append(
                    self._value(
                        target_path=target_path,
                        label=(
                            metadata["label"]
                            if metadata is not None
                            else str(raw.get("label") or target_path.split(".")[-1])
                        ),
                        value=selected,
                        document=item.document,
                        value_type=metadata["valueType"] if metadata is not None else None,
                        section_title=metadata["sectionTitle"] if metadata is not None else None,
                        identity_paths=identity_paths,
                    )
                )
        else:
            self._walk_legacy(
                item.document,
                identity_paths=identity_paths,
                values=values,
            )
        return self._deduplicate_values(values)

    @classmethod
    def _value(
        cls,
        *,
        target_path: str,
        label: str,
        value: str,
        document: dict[str, Any],
        value_type: str | None,
        section_title: str | None,
        identity_paths: set[str],
    ) -> SearchValue:
        parts = target_path.split(".")
        record_order = next((int(part) + 1 for part in parts if part.isdigit()), None)
        repeated = record_order is not None
        section_key = parts[0] if parts else "document"
        titles = {
            "document": "基本情報",
            "pedigree": "血統",
            "saleCalves": "売却子牛一覧",
            "calvings": "繁殖履歴",
            "customFields": "追加項目",
        }
        record_key = None
        if repeated and len(parts) >= 2:
            row = cls._path(document, ".".join(parts[:2]))
            if isinstance(row, dict):
                record_key = cls._reading_value(row.get("parity"))
        pattern = normalize_candidate_target(target_path)
        is_identity = pattern in identity_paths or pattern in {
            "document.unifiedEarTag",
            "document.cattleName",
        }
        numeric = cls._parse_decimal(value)
        parsed_date = cls._parse_date(value)
        resolved_type = "text" if is_identity else value_type or (
            "date" if parsed_date is not None else "number" if numeric is not None else "text"
        )
        return SearchValue(
            field_key=pattern,
            field_label=label,
            section_key=section_key,
            section_title=section_title or titles.get(section_key, section_key),
            record_order=record_order,
            record_key=record_key,
            value=value,
            normalized_value=cls._normalize_text(value),
            numeric_value=numeric,
            date_value=parsed_date,
            value_type=resolved_type,
            repeated=repeated,
            identity=is_identity,
        )

    @classmethod
    def _walk_legacy(
        cls,
        document: dict[str, Any],
        *,
        identity_paths: set[str],
        values: list[SearchValue],
    ) -> None:
        labels = {
            "unifiedEarTag": "個体識別番号",
            "cattleName": "牛名",
            "sireName": "種雄牛名",
            "priceYen": "価格",
            "weightKg": "体重",
            "saleDate": "売却日",
            "birthDate": "生年月日",
        }

        def visit(node: object, path: str) -> None:
            if isinstance(node, list):
                for index, child in enumerate(node):
                    visit(child, f"{path}.{index}")
                return
            if not isinstance(node, dict):
                return
            if set(node) >= _READING_KEYS:
                selected = cls._reading_value(node)
                if selected is not None:
                    values.append(
                        cls._value(
                            target_path=path,
                            label=labels.get(path.split(".")[-1], path.split(".")[-1]),
                            value=selected,
                            document=document,
                            value_type=None,
                            section_title=None,
                            identity_paths=identity_paths,
                        )
                    )
                return
            for key, child in node.items():
                visit(child, f"{path}.{key}" if path else key)

        for root in ("document", "pedigree", "saleCalves", "calvings", "customFields"):
            visit(document.get(root), root)

    @classmethod
    def _generic_metadata(cls, document: dict[str, Any]) -> dict[str, dict[str, str]]:
        data = document.get("genericData")
        if not isinstance(data, dict):
            return {}
        metadata: dict[str, dict[str, str]] = {}

        def add(field: object, section_title: str) -> None:
            if not isinstance(field, dict):
                return
            reading = field.get("reading")
            source = reading.get("sourceRegionId") if isinstance(reading, dict) else None
            if isinstance(source, str):
                metadata[source] = {
                    "label": str(field.get("name") or field.get("key") or "項目"),
                    "valueType": str(field.get("valueType") or "text"),
                    "sectionTitle": section_title,
                }

        standalone_fields = data.get("standaloneFields")
        for field in standalone_fields if isinstance(standalone_fields, list) else []:
            add(field, "基本情報")
        for section in data.get("sections", []) if isinstance(data.get("sections"), list) else []:
            if not isinstance(section, dict):
                continue
            section_title = str(section.get("name") or section.get("key") or "明細")
            records = section.get("records")
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict) or not isinstance(record.get("fields"), list):
                    continue
                for field in record["fields"]:
                    add(field, section_title)
        return metadata

    @staticmethod
    def _identity_paths(document: SearchDocument) -> set[str]:
        config = document.template_config
        fields = config.get("documentIdentityFields")
        if not isinstance(fields, list):
            legacy = config.get("documentIdentity")
            fields = [legacy] if isinstance(legacy, dict) else []
        return {
            normalize_candidate_target(path)
            for item in fields
            if isinstance(item, dict)
            and isinstance((path := item.get("targetPath")), str)
        }

    @staticmethod
    def _snapshot_template_config(job: ProcessingJob) -> dict[str, Any]:
        snapshot = job.config_snapshot_json.get("templateSnapshot")
        config = snapshot.get("config") if isinstance(snapshot, dict) else None
        return config if isinstance(config, dict) else {}

    @staticmethod
    def _path(document: object, path: str) -> object:
        current = document
        for segment in path.split(".") if path else []:
            if isinstance(current, dict):
                current = current.get(segment)
            elif isinstance(current, list) and segment.isdigit():
                index = int(segment)
                current = current[index] if 0 <= index < len(current) else None
            else:
                return None
        return current

    @staticmethod
    def _reading_value(value: object) -> str | None:
        if not isinstance(value, dict):
            return None
        candidate = value.get("normalized") or value.get("raw")
        if candidate is None:
            return None
        selected = str(candidate).strip()
        return selected or None

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return re.sub(
            r"[\s\-\u2010\u2011\u2012\u2013\u2014\u2015\u30fc\uff0d]+",
            "",
            normalized,
        )

    @staticmethod
    def _parse_decimal(value: str) -> Decimal | None:
        try:
            return Decimal(value.replace(",", "").strip())
        except InvalidOperation:
            return None

    @staticmethod
    def _parse_date(value: str) -> date | None:
        normalized = unicodedata.normalize("NFKC", value).strip()
        match = _DATE_PATTERN.fullmatch(normalized)
        if match is None:
            return None
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            return None

    @staticmethod
    def _deduplicate_values(values: list[SearchValue]) -> list[SearchValue]:
        unique: dict[tuple[str, int | None, str], SearchValue] = {}
        for value in values:
            unique[(value.field_key, value.record_order, value.value)] = value
        return list(unique.values())

    @staticmethod
    def _candidate_suggestions(session: Session) -> dict[tuple[str, str], list[str]]:
        rows = session.execute(
            select(TemplateCandidateBinding, TemplateCandidateSet).join(
                TemplateCandidateSet,
                TemplateCandidateSet.id == TemplateCandidateBinding.candidate_set_id,
            )
        ).all()
        return {
            (binding.template_id, binding.target_pattern): list(candidate_set.values_json)
            for binding, candidate_set in rows
        }

    def _documents(self, session: Session) -> list[SearchDocument]:
        page_counts: dict[str, int] = {
            document_id: int(count)
            for document_id, count in session.execute(
                select(
                    LedgerDocumentPage.ledger_document_id,
                    func.count(LedgerDocumentPage.id),
                )
                .where(LedgerDocumentPage.active.is_(True))
                .group_by(LedgerDocumentPage.ledger_document_id)
            ).tuples()
        }
        documents: list[SearchDocument] = []
        rows = session.execute(
            select(LedgerDocument, ProcessingJob, Template, ConfirmedResult)
            .join(ProcessingJob, ProcessingJob.id == LedgerDocument.current_job_id)
            .join(Template, Template.id == LedgerDocument.template_id)
            .join(ConfirmedResult, ConfirmedResult.job_id == ProcessingJob.id)
            .where(ProcessingJob.status == JobStatus.CONFIRMED.value)
        ).all()
        for ledger, job, template, confirmed in rows:
            documents.append(
                SearchDocument(
                    document_id=ledger.id,
                    template_id=template.id,
                    template_name=template.name,
                    source_job_id=job.id,
                    confirmed_at=confirmed.confirmed_at,
                    page_count=page_counts.get(ledger.id, 1),
                    spreadsheet_url=ledger.spreadsheet_url,
                    document=ledger.current_document_json,
                    job=job,
                    template_config=self._current_template_config(session, job),
                )
            )
        linked_jobs = set(session.scalars(select(LedgerDocumentPage.job_id)))
        legacy_rows = session.execute(
            select(ConfirmedResult, ProcessingJob, TemplateVersion, Template)
            .join(ProcessingJob, ProcessingJob.id == ConfirmedResult.job_id)
            .join(TemplateVersion, TemplateVersion.id == ProcessingJob.template_version_id)
            .join(Template, Template.id == TemplateVersion.template_id)
            .where(ProcessingJob.status == JobStatus.CONFIRMED.value)
            .order_by(ConfirmedResult.confirmed_at.desc())
        ).all()
        for confirmed, job, _version, template in legacy_rows:
            if job.id in linked_jobs:
                continue
            documents.append(
                SearchDocument(
                    document_id=job.id,
                    template_id=template.id,
                    template_name=template.name,
                    source_job_id=job.id,
                    confirmed_at=confirmed.confirmed_at,
                    page_count=1,
                    spreadsheet_url=None,
                    document=confirmed.confirmed_json,
                    job=job,
                    template_config=self._current_template_config(session, job),
                )
            )
        return documents

    def _current_template_config(
        self,
        session: Session,
        job: ProcessingJob,
    ) -> dict[str, Any]:
        if self._storage is not None and job.template_version_id:
            version = session.get(TemplateVersion, job.template_version_id)
            if version is not None:
                try:
                    return parse_template_config(
                        self._storage.read_bytes(version.config_relative_path).decode("utf-8")
                    ).model_dump(mode="json", by_alias=True)
                except (OSError, UnicodeError, ValueError):
                    pass
        return self._snapshot_template_config(job)
