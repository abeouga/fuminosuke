"""Memory: writing rules learned from the corrections made before confirming (2026-10-22).

When a result is confirmed, each text field whose confirmed value differs from what OCR
read is a correction. One GPT-6 Luna Responses request (shared API budget) turns the
corrections into general writing rules (e.g. 「50.000」→「50,000」: thousands are
separated by a comma) and drops corrections of one document's own values (a misread
name or number). A rule belongs to an item: a field, or every row cell of a table
column. The rules of an item are its reading instruction (``templateFieldHints``), fixed
in the snapshot of each new job; accepted jobs never change.

Learning runs after the confirmation is saved and is never shown: a failure is logged
only, and a confirmation is learned from once (``template_field_memory_runs``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.api_budget import ApiBudget
from app.core.config import Settings
from app.core.errors import AppError
from app.db.models import (
    OcrResult,
    ProcessingJob,
    Template,
    TemplateFieldMemory,
    TemplateFieldMemoryRun,
    TemplateVersion,
)
from app.db.session import Database
from app.domain.template_candidates import (
    MAX_FIELD_INSTRUCTION_LENGTH,
    normalize_candidate_target,
)
from app.domain.template_config import parse_template_config
from app.storage.local_storage import LocalStorageService

logger = logging.getLogger(__name__)

MODEL = "gpt-6-luna"
PURPOSE = "template.memory"
TIMEOUT_SECONDS = 30.0
MAX_RULES_PER_ITEM = 5
MAX_RULE_LENGTH = 120
MAX_CORRECTIONS = 40
INSTRUCTIONS = (
    "帳票のOCR結果を人が確認して直した箇所の一覧です。各修正について、"
    "次の読み取りでも使える『書き方のルール』だけを短い日本語の指示文で返してください"
    "（例：「50.000」→「50,000」なら「千の位の区切りはカンマ（,）で書く」、"
    "「R6.4.1」→「令和6年4月1日」なら「日付は『令和◯年◯月◯日』の形で書く」）。"
    "ルールには修正前後の値そのもの・特定の数字・人名や地名などの固有の内容を含めません。"
    "文字の読み違い（別の数字や文字に読んだ、名前の誤り）、記入の追加・削除など、"
    "その書類だけの修正で一般の書き方のルールにならないものは空文字を返します。"
    "その欄のexistingRulesに同じ意味のルールがあれば、その文をそのまま返します。"
    "ルールは1文、60文字以内。"
)
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rules"],
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "rule"],
                "properties": {"index": {"type": "integer"}, "rule": {"type": "string"}},
            },
        }
    },
}


class _Rule(BaseModel):
    index: int
    rule: str = Field(max_length=400)


class _Answer(BaseModel):
    rules: list[_Rule] = Field(max_length=MAX_CORRECTIONS)


class TemplateFieldMemoryService:
    def __init__(self, database: Database, storage: LocalStorageService) -> None:
        self._database = database
        self._storage = storage

    # --- Viewing -----------------------------------------------------------------

    def view(self, template_id: str, template_version_id: str) -> dict[str, object]:
        """Rules by item, with the item's cells on the template image of the version."""
        with self._database.session() as session:
            _require_table(session)
            if session.get(Template, template_id) is None:
                raise _not_found()
            version = session.get(TemplateVersion, template_version_id)
            if version is None or version.template_id != template_id:
                raise _not_found()
            config = parse_template_config(
                self._storage.read_bytes(version.config_relative_path).decode("utf-8")
            )
            boxes: dict[str, list[dict[str, int]]] = {}
            for mapping in config.field_mappings:
                boxes.setdefault(normalize_candidate_target(mapping.target_path), []).append(
                    {
                        "x": mapping.x,
                        "y": mapping.y,
                        "width": mapping.width,
                        "height": mapping.height,
                    }
                )
            return {
                "templateId": template_id,
                "items": [
                    {
                        "id": row.id,
                        "label": row.field_label,
                        "rule": row.rule,
                        "exampleBefore": row.example_before,
                        "exampleAfter": row.example_after,
                        "hitCount": row.hit_count,
                        "updatedAt": row.updated_at.isoformat(),
                        "targetPatterns": list(row.target_patterns_json),
                        "boxes": [
                            box
                            for target in row.target_patterns_json
                            for box in boxes.get(target, [])
                        ],
                    }
                    for row in _rows(session, template_id)
                ],
            }

    def delete(self, template_id: str, memory_id: str) -> None:
        with self._database.transaction() as session:
            _require_table(session)
            row = session.get(TemplateFieldMemory, memory_id)
            if row is None or row.template_id != template_id:
                raise AppError(
                    status=404,
                    code="TEMPLATE_MEMORY_NOT_FOUND",
                    title="Memory not found",
                    detail="指定されたメモリが見つかりません。",
                )
            session.delete(row)

    # --- Reading instruction ------------------------------------------------------

    @staticmethod
    def instructions(session: Session, template_id: str) -> dict[str, str]:
        """The reading instruction of each target: its item's rules, most used first."""
        if not inspect(session.connection()).has_table(TemplateFieldMemory.__tablename__):
            return {}
        rules: dict[str, list[str]] = {}
        rows = sorted(_rows(session, template_id), key=lambda r: (-r.hit_count, r.created_at))
        for row in rows:
            for target in row.target_patterns_json:
                if isinstance(target, str) and row.rule not in rules.setdefault(target, []):
                    rules[target].append(row.rule)
        result: dict[str, str] = {}
        for target, texts in rules.items():
            text = ""
            for rule in texts:
                sentence = rule.rstrip("。") + "。"
                if len(text) + len(sentence) > MAX_FIELD_INSTRUCTION_LENGTH:
                    break
                text += sentence
            if text:
                result[target] = text
        return result

    # --- Learning -------------------------------------------------------------------

    async def learn(self, job_id: str, settings: Settings, budget: ApiBudget) -> None:
        """Learn from a just-saved confirmation; failures are logged, never raised."""
        try:
            await self._learn(job_id, settings, budget)
        except Exception:
            logger.exception("template memory learning failed", extra={"jobId": job_id})

    async def _learn(self, job_id: str, settings: Settings, budget: ApiBudget) -> None:
        with self._database.session() as session:
            if not inspect(session.connection()).has_table(TemplateFieldMemory.__tablename__):
                return
            job = session.get(ProcessingJob, job_id)
            stored = session.scalar(select(OcrResult).where(OcrResult.job_id == job_id))
            if job is None or stored is None or job.confirmed_at is None:
                return
            template_id = (job.config_snapshot_json or {}).get("templateId")
            if not isinstance(template_id, str) or session.get(Template, template_id) is None:
                return
            revision = stored.revision
            if session.scalar(
                select(TemplateFieldMemoryRun.id).where(
                    TemplateFieldMemoryRun.job_id == job_id,
                    TemplateFieldMemoryRun.revision == revision,
                )
            ):
                return
            found = corrections(
                job.config_snapshot_json or {},
                stored.raw_candidate_json or {},
                stored.current_corrected_json or {},
            )
            existing = _rows(session, template_id)
        answer: _Answer | None = None
        if found:
            key = settings.ocr.openai_api_key
            if not key or budget.status()["remaining"] < 1:
                return  # no paid request possible; try again on a later confirmation
            payload = [
                {
                    "index": index,
                    "field": item["label"],
                    "before": item["before"],
                    "after": item["after"],
                    "existingRules": [
                        row.rule for row in existing if _overlaps(row, item["targets"])
                    ],
                }
                for index, item in enumerate(found)
            ]
            answer = await _ask(key.get_secret_value(), budget, payload)
        with self._database.transaction() as session:
            try:
                session.add(TemplateFieldMemoryRun(job_id=job_id, revision=revision))
                session.flush()
            except IntegrityError:
                session.rollback()
                return  # learned by a concurrent run
            if answer is not None:
                _save(session, template_id, job_id, found, answer)


def corrections(
    snapshot: Mapping[str, Any],
    raw_document: Mapping[str, Any],
    confirmed_document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Text fields whose confirmed value differs from what OCR read (both non-empty).

    Values a keyword setting replaced (``KEYWORD_ABSORBED``) are not corrections of the
    reading. ``targets`` is the item of the field: the field, or its table column.
    """
    config = (snapshot.get("templateSnapshot") or {}).get("config") or {}
    mappings = {
        raw["mappingId"]: raw
        for raw in config.get("fieldMappings") or []
        if isinstance(raw, dict) and isinstance(raw.get("mappingId"), str)
    }
    columns = _columns(snapshot)
    raw_fields = raw_document.get("fields") or {}
    labels = confirmed_document.get("fieldLabels") or {}
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for key, reading in (confirmed_document.get("fields") or {}).items():
        before_reading = raw_fields.get(key)
        if not isinstance(reading, dict) or not isinstance(before_reading, dict):
            continue
        mapping = mappings.get(reading.get("sourceRegionId"))
        if (
            mapping is None
            or mapping.get("mappingType", "text") != "text"
            or mapping.get("readingMode") == "number"
            or before_reading.get("reviewReason") == "KEYWORD_ABSORBED"
        ):
            continue
        before, after = _value(before_reading), _value(reading)
        if not before or not after or before == after:
            continue
        mapping_ids = columns.get(mapping["mappingId"], [mapping["mappingId"]])
        targets = sorted(
            {
                normalize_candidate_target(mappings[mid]["targetPath"])
                for mid in mapping_ids
                if mid in mappings and isinstance(mappings[mid].get("targetPath"), str)
            }
        )
        if not targets or (targets[0], before, after) in seen:
            continue
        seen.add((targets[0], before, after))
        label = labels.get(key) if isinstance(labels.get(key), str) else None
        result.append(
            {
                "label": re.sub(r"\s*\d+行目$", "", label or mapping.get("label") or key)[:100],
                "before": before[:200],
                "after": after[:200],
                "targets": targets,
            }
        )
        if len(result) >= MAX_CORRECTIONS:
            break
    return result


def _value(reading: Mapping[str, Any]) -> str:
    value = reading.get("normalized") or reading.get("raw") or ""
    return value.strip() if isinstance(value, str) else ""


def _columns(snapshot: Mapping[str, Any]) -> dict[str, list[str]]:
    """Mapping ID → every mapping ID of its table column (all rows)."""
    structure = snapshot.get("reviewStructure")
    result: dict[str, list[str]] = {}
    if not isinstance(structure, dict):
        return result
    for section in structure.get("recordSections") or []:
        if not isinstance(section, dict):
            continue
        by_column: dict[str, list[str]] = {}
        for row in section.get("rows") or []:
            for cell in (row or {}).get("cells") or []:
                if isinstance(cell, dict) and isinstance(cell.get("fieldId"), str):
                    by_column.setdefault(str(cell.get("columnId")), []).append(cell["fieldId"])
        for ids in by_column.values():
            for mapping_id in ids:
                result[mapping_id] = ids
    return result


async def _ask(key: str, budget: ApiBudget, payload: list[dict[str, Any]]) -> _Answer | None:
    body = {
        "model": MODEL,
        "instructions": INSTRUCTIONS,
        "input": json.dumps({"corrections": payload}, ensure_ascii=False),
        "reasoning": {"effort": "low"},
        "store": False,
        "max_output_tokens": 2048,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "memory_rules",
                "strict": True,
                "schema": ANSWER_SCHEMA,
            }
        },
    }
    try:
        # ApiBudget reserves before the actual send and never refunds failures.
        async with asyncio.timeout(TIMEOUT_SECONDS):
            async with budget.client(PURPOSE) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": "Bearer " + key},
                    json=body,
                    timeout=TIMEOUT_SECONDS,
                )
        if not response.is_success:
            logger.warning("template memory request rejected: %s", response.status_code)
            return None
        value = response.json()
    except (TimeoutError, httpx.HTTPError, ValueError) as error:
        logger.warning("template memory request failed: %s", error)
        return None
    if not isinstance(value, dict) or value.get("status") != "completed":
        return None
    texts = [
        content.get("text")
        for item in value.get("output", [])
        if item.get("type") == "message"
        for content in item.get("content", [])
        if content.get("type") == "output_text"
    ]
    if len(texts) != 1:
        return None
    try:
        return _Answer.model_validate(json.loads(texts[0]))
    except (ValueError, ValidationError):
        return None


def _save(
    session: Session,
    template_id: str,
    job_id: str,
    found: list[dict[str, Any]],
    answer: _Answer,
) -> None:
    rows = _rows(session, template_id)
    counted: set[int] = set()
    for item in answer.rules:
        if not 0 <= item.index < len(found):
            continue
        rule = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", item.rule)).strip()
        rule = rule.rstrip("。")
        if not rule or len(rule) > MAX_RULE_LENGTH:
            continue
        correction = found[item.index]
        targets: list[str] = correction["targets"]
        row = next(
            (r for r in rows if _same(r.rule, rule) and _overlaps(r, targets)), None
        )
        if row is None:
            row = TemplateFieldMemory(
                template_id=template_id,
                target_patterns_json=targets,
                field_label=correction["label"],
                rule=rule,
                hit_count=0,
            )
            session.add(row)
            rows.append(row)
        elif sorted(set(row.target_patterns_json) | set(targets)) != sorted(
            row.target_patterns_json
        ):
            row.target_patterns_json = sorted(set(row.target_patterns_json) | set(targets))
        if id(row) not in counted:  # once per confirmation, however many rows match
            counted.add(id(row))
            row.hit_count = (row.hit_count or 0) + 1
        row.example_before = correction["before"]
        row.example_after = correction["after"]
        row.last_job_id = job_id
        session.flush()
    _trim(session, rows)


def _trim(session: Session, rows: list[TemplateFieldMemory]) -> None:
    """At most MAX_RULES_PER_ITEM rules per item; the least used and oldest go first."""
    by_item: dict[tuple[str, ...], list[TemplateFieldMemory]] = {}
    for row in rows:
        by_item.setdefault(tuple(sorted(row.target_patterns_json)), []).append(row)
    for group in by_item.values():
        group.sort(key=lambda r: (r.hit_count, r.updated_at or r.created_at), reverse=True)
        for extra in group[MAX_RULES_PER_ITEM:]:
            session.execute(delete(TemplateFieldMemory).where(TemplateFieldMemory.id == extra.id))


def _same(a: str, b: str) -> bool:
    def key(text: str) -> str:
        return re.sub(r"[\s。、.,]", "", unicodedata.normalize("NFKC", text)).casefold()

    return key(a) == key(b)


def _overlaps(row: TemplateFieldMemory, targets: list[str]) -> bool:
    return bool(set(row.target_patterns_json) & set(targets))


def _rows(session: Session, template_id: str) -> list[TemplateFieldMemory]:
    return list(
        session.scalars(
            select(TemplateFieldMemory)
            .where(TemplateFieldMemory.template_id == template_id)
            .order_by(TemplateFieldMemory.created_at, TemplateFieldMemory.id)
        )
    )


def _require_table(session: Session) -> None:
    if not inspect(session.connection()).has_table(TemplateFieldMemory.__tablename__):
        raise AppError(
            status=503,
            code="TEMPLATE_MEMORY_NOT_MIGRATED",
            title="Database migration required",
            detail="メモリを使うにはデータベースの移行が必要です。",
        )


def _not_found() -> AppError:
    return AppError(
        status=404,
        code="TEMPLATE_NOT_FOUND",
        title="Template not found",
        detail="指定されたテンプレートが見つかりません。",
    )
