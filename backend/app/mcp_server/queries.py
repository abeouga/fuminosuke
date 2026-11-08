"""search_documents の中身。絞り込みも計算もせず、文之助のAPIを呼んで並べ替えるだけ。"""

from __future__ import annotations

from typing import Any

from app.mcp_server import resolve
from app.mcp_server.client import FuminosukeClient, Refused
from app.mcp_server.errors import Candidate, ToolProblem
from app.mcp_server.models import (
    DocumentList,
    DocumentSummary,
    FieldEquals,
    IdentityValue,
)


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


def search_text(result: DocumentList) -> str:
    lines = [result.summary]
    for d in result.documents:
        identity = "、".join(f"{i.label}={i.value}" for i in d.identity if i.value)
        lines.append(
            f"- {d.document_name or '書類名なし'}（書類ID {d.document_id}、{d.template_name}"
            + (f"、{identity}" if identity else "")
            + f"、版{d.version}、確定 {(d.confirmed_at or '')[:10]}）"
        )
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
