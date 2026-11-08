"""文之助の構造化の書類を、根拠の形に並べ替える。応答にある項目だけから作り、作り出さない。"""

from __future__ import annotations

import re
from typing import Any

from app.mcp_server.client import FuminosukeClient
from app.mcp_server.models import Box, DocumentLocation, Evidence

_NUMBERED = re.compile(r"^(.*\D)(\d+)$")
# 選択欄の名前の末尾に印字された選択肢：「区分（高・特・繁殖）」の「（高・特・繁殖）」。
_PRINTED_CHOICES = re.compile(r"\s*[（(][^（）()]*[・、,，/／][^（）()]*[）)]\s*$")


def choice_name(label: str, labels: list[str] | None) -> str:
    """同じ名前の選択肢は保存上「黒2」のように番号付き。画面は印字の名前（黒）で出す。"""
    match = _NUMBERED.match(label) if labels else None
    if match and labels and label in labels and match.group(1) in labels:
        return match.group(1)
    return label


def shown_value(reading: dict[str, Any]) -> str | None:
    """ライブラリの表示と同じ規則（frontend の choiceText）。"""
    value = reading.get("value")
    labels = reading.get("choiceLabels")
    if value is None or not labels:
        return value
    return "・".join(choice_name(part.strip(), labels) for part in value.split("・"))


def shown_label(reading: dict[str, Any]) -> str:
    """選択欄は名前から印字の選択肢を外して出す（ライブラリの readingLabel と同じ）。"""
    label = str(reading["label"])
    if not reading.get("choice"):
        return label
    return _PRINTED_CHOICES.sub("", label).strip() or label


def page_image(document: dict[str, Any], page_id: str | None) -> str | None:
    pages = document.get("pages") or []
    page = next((p for p in pages if p.get("pageId") == page_id), pages[0] if pages else None)
    return page["imageUrl"] if page else None


def reading_evidence(
    client: FuminosukeClient,
    document: dict[str, Any],
    reading: dict[str, Any],
    *,
    table_title: str | None = None,
) -> Evidence:
    shown = shown_value(reading)
    stored = reading.get("value")
    name = shown_label(reading)
    place = f"{table_title} {reading['rowNumber']}行目の{name}" if table_title else name
    title = document.get("documentName") or "書類名なし"
    box = reading.get("box")
    return Evidence(
        source_id=document["jobId"],
        label=f"{document['templateName']}「{title}」の{place}",
        location=DocumentLocation(
            template_id=document["templateId"],
            template_name=document["templateName"],
            document_name=document.get("documentName"),
            field_id=reading.get("fieldId"),
            field_label=reading["label"],
            section=reading.get("section"),
            table_id=reading.get("tableId"),
            table_title=table_title,
            row_number=reading.get("rowNumber"),
            column_id=reading.get("columnId"),
            page_id=reading.get("pageId"),
            box=Box(**box) if box else None,
            crop_url=client.absolute(reading.get("cropUrl")),
            image_url=client.absolute(page_image(document, reading.get("pageId"))),
        ),
        value=shown,
        stored_value=stored if stored != shown else None,
        status=reading["status"],
    )
