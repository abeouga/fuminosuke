"""文之助の構造化の書類を、根拠の形に並べ替える。応答にある項目だけから作り、作り出さない。"""

from __future__ import annotations

import re
from typing import Any

from app.mcp_server.client import FuminosukeClient
from app.mcp_server.models import (
    Box,
    DocumentInfo,
    DocumentLocation,
    DocumentValues,
    Evidence,
    TableRowValues,
    TableValues,
)
from app.mcp_server.resolve import compact, local_time

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


# 1つの表で返す行の上限。
MAX_TABLE_ROWS = 100
# 要約に値を並べる欄の数。
SUMMARY_FIELDS = 8


def document_values(
    client: FuminosukeClient, document: dict[str, Any], wanted: list[str] | None
) -> DocumentValues:
    """書類の欄（表の外）と表を、帳票の上から順に根拠の形で並べる。

    wanted があれば、その名前（欄名・見出し・表名・列名・「表名 列名」、文字のそろえ方は
    compact）に当たるものだけ。見つからない名前は not_found_fields に入れる。
    """
    selection = _Selection(document, wanted)
    fields = [
        reading_evidence(client, document, reading)
        for reading in _outside_fields(document)
        if selection.field(reading)
    ]
    fields.extend(
        _extra_evidence(client, document, extra)
        for extra in document.get("extraFields") or []
        if selection.extra(extra)
    )
    tables = []
    for table in _ordered_tables(document):
        columns = selection.columns(table)
        if columns:
            tables.append(_table_values(client, document, table, columns))
    info = DocumentInfo(
        document_id=document["jobId"],
        document_name=document.get("documentName"),
        template_id=document["templateId"],
        template_name=document["templateName"],
        version=document.get("seriesVersion") or 1,
        confirmed_at=local_time(document.get("confirmedAt")),
    )
    return DocumentValues(
        document=info,
        fields=fields,
        tables=tables,
        not_found_fields=selection.missing(),
        summary=_summary(info, fields, tables),
    )


def _outside_fields(document: dict[str, Any]) -> list[dict[str, Any]]:
    """表の外の欄を、表示の順（blocks）に。blocks に無い欄は後ろへ元の順で。"""
    outside = [f for f in document["fields"] if f.get("tableId") is None]
    by_id = {f["fieldId"]: f for f in outside}
    order: list[str] = []
    for block in document.get("blocks") or []:
        order.extend(block.get("fieldIds") or [])
        order.extend(block.get("attachedFieldIds") or [])
    ordered = list(dict.fromkeys(fid for fid in order if fid in by_id))
    shown = set(ordered)
    return [by_id[fid] for fid in ordered] + [f for f in outside if f["fieldId"] not in shown]


def _ordered_tables(document: dict[str, Any]) -> list[dict[str, Any]]:
    tables = document.get("tables") or []
    rank = {
        block.get("id"): index
        for index, block in enumerate(document.get("blocks") or [])
        if block.get("kind") == "record"
    }
    return sorted(tables, key=lambda t: rank.get(t["id"], len(rank)))


def _column_names(table: dict[str, Any]) -> dict[str, str]:
    """列ID → 返す名前。同じ名前の列が2つあれば「備考（2）」のように分ける。"""
    names: dict[str, str] = {}
    used: dict[str, int] = {}
    for column in table["columns"]:
        label = column["label"]
        used[label] = used.get(label, 0) + 1
        names[column["id"]] = label if used[label] == 1 else f"{label}（{used[label]}）"
    return names


def _filled_rows(table: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """値のある行。どの行にも値のある列（行番号など）だけの行は、空の行とみなす。

    文之助のチャット（structured/qa.py の _row_readings）と同じ規則。
    """

    def filled(row: dict[str, Any]) -> set[str]:
        return {
            cell["columnId"]
            for cell in row["cells"]
            if cell.get("fieldId") in by_id and by_id[cell["fieldId"]].get("value") is not None
        }

    rows = table["rows"]
    every = set.intersection(*(filled(r) for r in rows)) if len(rows) > 1 else set()
    return [row for row in rows if filled(row) - every]


def _table_values(
    client: FuminosukeClient,
    document: dict[str, Any],
    table: dict[str, Any],
    columns: set[str],
) -> TableValues:
    by_id = {f["fieldId"]: f for f in document["fields"]}
    names = _column_names(table)
    rows = _filled_rows(table, by_id)
    values = []
    for row in rows[:MAX_TABLE_ROWS]:
        cells = {}
        for cell in row["cells"]:
            reading = by_id.get(cell.get("fieldId") or "")
            if reading is not None and cell["columnId"] in columns:
                cells[names[cell["columnId"]]] = reading_evidence(
                    client, document, reading, table_title=table["title"]
                )
        values.append(TableRowValues(row_number=row["rowNumber"], cells=cells))
    return TableValues(
        table_id=table["id"],
        title=table["title"],
        columns=[names[c["id"]] for c in table["columns"] if c["id"] in columns],
        rows=values,
        truncated=len(rows) > MAX_TABLE_ROWS,
    )


def _extra_evidence(
    client: FuminosukeClient, document: dict[str, Any], extra: dict[str, Any]
) -> Evidence:
    """テンプレートの欄に結び付かない読み取り値（ライブラリの「その他」）。位置は持たない。"""
    return reading_evidence(
        client, document, {**extra, "fieldId": None, "cropUrl": None, "box": None}
    )


class _Selection:
    """get_document の fields の指定。表示の量を絞るだけで、業務の判断はしない。"""

    def __init__(self, document: dict[str, Any], wanted: list[str] | None) -> None:
        self._all = not wanted
        self._wanted = {compact(name): name for name in wanted or [] if compact(name)}
        self._hit: set[str] = set()
        self._tables = {t["id"]: t for t in document.get("tables") or []}

    def _take(self, *names: str | None) -> bool:
        hits = [compact(n) for n in names if n and compact(n) in self._wanted]
        self._hit.update(hits)
        return bool(hits)

    def field(self, reading: dict[str, Any]) -> bool:
        if self._all:
            return True
        return self._take(reading["label"], shown_label(reading), reading.get("section"))

    def extra(self, extra: dict[str, Any]) -> bool:
        return self._all or self._take(extra["label"])

    def columns(self, table: dict[str, Any]) -> set[str]:
        every = {c["id"] for c in table["columns"]}
        if self._all or self._take(table["title"]):
            return every
        title = compact(table["title"])
        chosen = set()
        for column in table["columns"]:
            label = compact(column["label"])
            for key in self._wanted:
                if key == label or key == title + label:
                    self._hit.add(key)
                    chosen.add(column["id"])
        return chosen

    def missing(self) -> list[str]:
        return [name for key, name in self._wanted.items() if key not in self._hit]


def _summary(info: DocumentInfo, fields: list[Evidence], tables: list[TableValues]) -> str:
    head = (
        f"{info.template_name}「{info.document_name or '書類名なし'}」"
        f"（版{info.version}、確定 {(info.confirmed_at or '')[:10]}）"
    )
    lines = [head]
    values = [
        f"{e.label.split('」の', 1)[-1]}：{e.value if e.value is not None else '空欄'}"
        for e in fields[:SUMMARY_FIELDS]
    ]
    if values:
        more = f" ほか{len(fields) - SUMMARY_FIELDS}欄" if len(fields) > SUMMARY_FIELDS else ""
        lines.append("、".join(values) + more)
    if tables:
        lines.append("表：" + "、".join(f"{t.title}（値のある行 {len(t.rows)}）" for t in tables))
    return "\n".join(lines)


def _shown(item: Evidence) -> str:
    if item.value is None:
        return "空欄"
    return f"{item.value}（保存値 {item.stored_value}）" if item.stored_value else item.value


def document_text(values: DocumentValues) -> str:
    """AIがそのまま読む短い文章。値・欄名・ID は structuredContent と同じ（位置とURLは省く）。"""
    info = values.document
    lines = [
        f"{info.template_name}「{info.document_name or '書類名なし'}」"
        f"（書類ID {info.document_id}、版{info.version}、確定 {info.confirmed_at}）"
    ]
    if values.fields:
        lines.append("欄：")
        for item in values.fields:
            place = item.location.field_label
            if item.location.section:
                place += f"［{item.location.section}］"
            lines.append(f"- {place}：{_shown(item)}　field_id={item.location.field_id}")
    for table in values.tables:
        cut = "（上限で切った）" if table.truncated else ""
        lines.append(f"表「{table.title}」 値のある行 {len(table.rows)}{cut}：")
        for row in table.rows:
            cells = "、".join(
                f"{name}={_shown(cell)}〔{cell.location.field_id}〕"
                for name, cell in row.cells.items()
            )
            lines.append(f"- {row.row_number}行目：{cells}")
    if values.not_found_fields:
        lines.append("見つからなかった名前：" + "、".join(values.not_found_fields))
    lines.append("切り抜き画像は get_field_image(document_id, field_id) で見られます。")
    return "\n".join(lines)
