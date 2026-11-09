"""search_documents・aggregate の中身。絞り込みも計算もせず、文之助のAPIを呼んで並べ替えるだけ。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.mcp_server import evidence, resolve
from app.mcp_server.client import FuminosukeClient, NotFoundResponse, Refused
from app.mcp_server.errors import Candidate, ToolProblem
from app.mcp_server.models import (
    AggregatedValue,
    AggregationValues,
    DocumentList,
    DocumentSummary,
    Evidence,
    FieldEquals,
    IdentityValue,
    TemplateRef,
)

Operation = Literal["sum", "avg", "count", "max", "min"]
OPERATION_NAMES = {"sum": "合計", "avg": "平均", "count": "件数", "max": "最大", "min": "最小"}
# 道具の結果に根拠の形で並べる値の数（used・excluded それぞれ）。件数は count_used で分かる。
MAX_ITEMS = 100
# AIが読む文章に1行ずつ並べる値の数。
TEXT_ITEMS = 30
_SPACE = re.compile(r"\s+")


def filter_body(
    client: FuminosukeClient,
    *,
    template: str | None,
    document: str | None,
    text: str | None,
    field_equals: list[FieldEquals] | None,
    confirmed_from: str | None,
    confirmed_to: str | None,
) -> tuple[dict[str, Any], Candidate | None, Candidate | None]:
    """名前を ID に照合して、文之助の絞り込み（DocumentFilter）の形にする。"""
    chosen_template = resolve.template(client, template) if template else None
    template_id = chosen_template.id if chosen_template else None
    chosen_document = (
        resolve.document(client, document, template_id=template_id) if document else None
    )
    body: dict[str, Any] = {
        "templateId": template_id,
        "documentIds": [chosen_document.id] if chosen_document else None,
        "query": text or None,
        "fieldEquals": [{"label": c.field, "value": c.value} for c in field_equals or []],
        "confirmedFrom": confirmed_from or None,
        "confirmedTo": confirmed_to or None,
    }
    return body, chosen_template, chosen_document


def search(client: FuminosukeClient, body: dict[str, Any], limit: int) -> DocumentList:
    try:
        found = client.search({**body, "limit": limit})
    except Refused as error:
        raise _refusal(error, "書類") from error
    documents = [
        DocumentSummary(
            document_id=item["jobId"],
            document_name=item.get("documentName"),
            template_id=item["templateId"],
            template_name=item["templateName"],
            version=item.get("seriesVersion") or 1,
            confirmed_at=resolve.local_time(item.get("confirmedAt")),
            identity=[
                IdentityValue(label=i["label"], value=i.get("value")) for i in item["identity"]
            ],
            matched=_matched(client, item),
        )
        for item in found["items"]
    ]
    total = found["total"]
    if total == 0:
        summary = "条件に合う確定済みの書類はありません。"
    else:
        shown = f"（新しい{len(documents)}件を出しています）" if total > len(documents) else ""
        summary = f"条件に合う確定済みの書類は{total}件です{shown}。"
    if found.get("truncated"):
        summary += "書類が多く、読む上限（200件）までで数えています。"
    return DocumentList(
        documents=documents, total=total, truncated=bool(found.get("truncated")), summary=summary
    )


def _matched(client: FuminosukeClient, item: dict[str, Any]) -> list[Evidence]:
    """検索の条件に一致した欄（文之助のサーバーが判定した欄ID）を、根拠の形に。"""
    wanted = item.get("matchedFieldIds") or []
    if not wanted:
        return []
    try:
        document = client.document(item["jobId"])
    except NotFoundResponse:
        return []  # 検索の後に再編集・削除された。
    by_id = {f["fieldId"]: f for f in document["fields"]}
    return [
        evidence.reading_evidence(client, document, by_id[field_id])
        for field_id in wanted
        if field_id in by_id
    ]


def search_text(result: DocumentList) -> str:
    lines = [result.summary]
    for d in result.documents:
        identity = "、".join(f"{i.label}={i.value}" for i in d.identity if i.value)
        matched = "、".join(
            f"{m.location.field_label}={m.value if m.value is not None else '空欄'}"
            f"〔{m.location.field_id}〕"
            for m in d.matched
        )
        lines.append(
            f"- {d.document_name or '書類名なし'}（書類ID {d.document_id}、{d.template_name}"
            + (f"、{identity}" if identity else "")
            + f"、版{d.version}、確定 {(d.confirmed_at or '')[:10]}）"
            + (f" 一致した欄：{matched}" if matched else "")
        )
    return "\n".join(lines)


def selector(
    client: FuminosukeClient, template_id: str, field: str, section: str | None
) -> dict[str, Any]:
    """「表名 列名」「見出し 欄名」を分ける。欄名そのものとして知られている名前は分けない。

    名前の一覧は文之助の GET /structured/fields（直近の確定済みの書類にある名前）。
    """
    wanted = field.strip()
    if section:
        return {"label": wanted, "section": section.strip()}
    names = client.fields(template_id)
    labels = {resolve.compact(n["label"]) for n in names}
    titles = {resolve.compact(n["tableTitle"]) for n in names if n.get("tableTitle")}
    parts = _SPACE.split(wanted, maxsplit=1)
    if resolve.compact(wanted) in labels or len(parts) < 2:
        return {"label": wanted}
    head, rest = parts
    if resolve.compact(head) in titles:
        return {"label": rest, "tableTitle": head}
    return {"label": rest, "section": head}


def aggregate(
    client: FuminosukeClient,
    body: dict[str, Any],
    template: Candidate,
    document: Candidate | None,
    field: dict[str, Any],
    operation: Operation,
) -> AggregationValues:
    try:
        answer = client.aggregate({**body, "field": field, "operation": operation})
    except Refused as error:
        raise _refusal(error, "欄", wanted=_field_name(field)) from error

    def values(items: list[dict[str, Any]]) -> list[AggregatedValue]:
        return [_value(client, item) for item in items[:MAX_ITEMS]]

    used, excluded, at = values(answer["used"]), values(answer["excluded"]), values(answer["at"])
    name = _field_name(answer["field"])
    cut = (
        bool(answer["truncated"])
        or len(answer["used"]) > MAX_ITEMS
        or len(answer["excluded"]) > MAX_ITEMS
    )
    result = AggregationValues(
        operation=operation,
        field=name,
        template=TemplateRef(template_id=template.id, name=template.name),
        result=answer["result"],
        count_used=answer["countUsed"],
        blank=answer["blank"],
        documents=answer["documents"],
        used=used,
        excluded=excluded,
        at=at,
        truncated=cut,
        summary="",
    )
    result.summary = _aggregate_summary(result, template, document)
    return result


def aggregate_text(result: AggregationValues) -> str:
    lines = [result.summary]

    def line(item: AggregatedValue) -> str:
        read = f" → {item.number}" if item.note and item.number is not None else ""
        note = f"（{item.note}）" if item.note else ""
        return (
            f"- {item.label}：{item.value if item.value is not None else '空欄'}{read}{note}"
            f"　書類ID {item.source_id}、field_id={item.location.field_id}"
        )

    if result.used:
        lines.append("使った値：")
        lines.extend(line(item) for item in result.used[:TEXT_ITEMS])
        if result.count_used > TEXT_ITEMS:
            lines.append(
                f"…ほか{result.count_used - TEXT_ITEMS}件（構造化データの used にあります）"
            )
    if result.excluded:
        lines.append("数として読めず、除いた値：")
        lines.extend(line(item) for item in result.excluded[:TEXT_ITEMS])
    return "\n".join(lines)


def _value(client: FuminosukeClient, item: dict[str, Any]) -> AggregatedValue:
    """集計APIの項目に、書類の欄の根拠（位置・切り抜き・画面の値）を足す。"""
    reading: dict[str, Any] | None = None
    try:
        document = client.document(item["jobId"])
    except NotFoundResponse:
        document = None  # 集計の後に再編集・削除された。
    if document is not None and item.get("fieldId"):
        reading = next((f for f in document["fields"] if f["fieldId"] == item["fieldId"]), None)
    if document is None or reading is None:
        document = document or {
            "jobId": item["jobId"],
            "documentName": item.get("documentName"),
            "templateId": "",
            "templateName": "",
            "pages": [],
        }
        reading = {
            "fieldId": item.get("fieldId"),
            "label": item["label"],
            "section": item.get("section"),
            "rowNumber": item.get("rowNumber"),
            "pageId": item.get("pageId"),
            "value": item.get("value"),
            "status": "blank" if item.get("value") is None else "readable",
        }
    shown = evidence.reading_evidence(client, document, reading, table_title=item.get("tableTitle"))
    return AggregatedValue(**shown.model_dump(), number=item.get("number"), note=item.get("note"))


def _field_name(field: dict[str, Any]) -> str:
    head = field.get("tableTitle") or field.get("section")
    return f"{head} {field['label']}" if head else str(field["label"])


def _number(value: str | None) -> str:
    if value is None:
        return "計算できません"
    try:
        return f"{Decimal(value):,}"
    except InvalidOperation:
        return value


def _aggregate_summary(
    result: AggregationValues, template: Candidate, document: Candidate | None
) -> str:
    scope = (
        f"{template.name}「{document.name}」"
        if document
        else f"{template.name}の確定済みの書類{result.documents}件"
    )
    operation = OPERATION_NAMES[result.operation]
    if result.operation == "count":
        head = f"{scope}の{result.field}で、値の書かれている欄は{result.count_used}件です"
    elif result.result is None:
        head = f"{scope}の{result.field}は、数として読める値がなく{operation}を出せません"
    else:
        head = f"{scope}の{result.field}の{operation}は {_number(result.result)} です"
    counts = [f"{result.count_used}件" if result.operation != "count" else None]
    if result.blank:
        counts.append(f"空欄{result.blank}件")
    if result.excluded:
        counts.append(f"数として読めず除いた値{len(result.excluded)}件")
    detail = "、".join(c for c in counts if c)
    lines = [head + (f"（{detail}）" if detail else "") + "。"]
    if result.at:
        lines.append(f"{operation}の値だった欄：" + "、".join(a.label for a in result.at))
    noted = [v for v in result.used if v.note]
    if noted:
        lines.append(
            "読み方に注意した値：" + "、".join(f"{v.label}「{v.value}」{v.note}" for v in noted[:5])
        )
    if result.truncated:
        lines.append("書類や値が多く、上限までで切っています。")
    return "\n".join(lines)


def _refusal(error: Refused, kind: str, *, wanted: str | None = None) -> ToolProblem:
    """文之助の 4xx を、AIが次の手を決められる文章に。"""
    if error.code == "STRUCTURED_FIELD_AMBIGUOUS":
        lines = [
            "・"
            + " ".join(
                p
                for p in (
                    c.get("templateName"),
                    c.get("tableTitle") or c.get("section"),
                    c.get("label"),
                )
                if p
            )
            for c in error.candidates
        ]
        return ToolProblem(
            f"「{wanted}」に当てはまる欄が複数あります：\n"
            + "\n".join(lines)
            + "\n表の列は「表名 列名」、見出しの下の欄は「見出し 欄名」"
            + "（又は section）で指定してください。"
        )
    if error.code == "STRUCTURED_FIELD_NOT_FOUND":
        return ToolProblem(f"{error.message}get_template_fields で欄名を確かめてください。")
    if error.code == "STRUCTURED_NO_DOCUMENTS":
        return ToolProblem(f"{error.message}条件（書類・欄の値・確定日）を確かめてください。")
    if error.field_errors:
        details = "、".join(f"{e.get('field')}：{e.get('message')}" for e in error.field_errors[:5])
        return ToolProblem(
            f"指定の形が正しくありません（{details}）。日付は YYYY-MM-DD で指定してください。"
        )
    return error
