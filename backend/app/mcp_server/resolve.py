"""名前→IDの照合（テンプレート・書類）。推測で1つに決めず、決まらなければ候補を返す。

文字のそろえ方は文之助のチャットの照合（structured/qa.py の _fold・_compact）と同じ規則を
書き写したもの（このサーバーは文之助の処理を import しないため）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.mcp_server.client import FuminosukeClient
from app.mcp_server.errors import Ambiguous, Candidate, NotFound

_KANA = {code: code - 0x60 for code in range(0x30A1, 0x30F7)}
JST = timezone(timedelta(hours=9), "JST")


def fold(value: str) -> str:
    """全角半角・大文字小文字・カタカナ/ひらがな（「コヨミ」＝「こよみ」）をそろえる。"""
    return unicodedata.normalize("NFKC", value).casefold().translate(_KANA)


def compact(value: str) -> str:
    """fold した上で空白を除く（「田中 太郎」＝「田中太郎」）。"""
    return re.sub(r"\s+", "", fold(value))


def local_time(value: str | None) -> str | None:
    """文之助の時刻（UTC）を日本時間の ISO 形式に（ライブラリの表示の日付と合わせる）。"""
    if not value:
        return None
    return (
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        .astimezone(JST)
        .isoformat(timespec="seconds")
    )


def local_day(value: str | None) -> str:
    shown = local_time(value)
    return shown[:10] if shown else "確定日不明"


@dataclass(frozen=True, slots=True)
class Catalog:
    """使用中のテンプレート（ごみ箱を除く）と、確定済みの書類の一覧（最新の版だけ）。"""

    templates: list[dict[str, Any]]
    documents: list[dict[str, Any]]


def catalog(client: FuminosukeClient) -> Catalog:
    documents = client.documents()
    with_documents = {d["templateId"] for d in documents}
    # 使用中の版があるもの。書類が入っていれば下書きだけのテンプレートも出す（書類の行き先）。
    templates = [
        t for t in client.templates() if t.get("activeVersionId") or t["id"] in with_documents
    ]
    return Catalog(templates, documents)


def template(client: FuminosukeClient, wanted: str, listed: Catalog | None = None) -> Candidate:
    """ID の一致 → 名前の完全一致 → 名前に含まれる。"""
    listed = listed or catalog(client)
    counts: dict[str, int] = {}
    for document in listed.documents:
        counts[document["templateId"]] = counts.get(document["templateId"], 0) + 1
    candidates = [
        Candidate(t["id"], t["name"], f"確定済みの書類 {counts.get(t['id'], 0)}件")
        for t in listed.templates
    ]
    names = {c.id: compact(c.name) for c in candidates}
    needle = compact(wanted)
    return _pick(
        "テンプレート",
        wanted,
        candidates,
        [
            lambda c: c.id == wanted.strip(),
            lambda c: names[c.id] == needle,
            lambda c: bool(needle) and needle in names[c.id],
        ],
        hint="list_templates で使用中のテンプレートを確かめてください。",
    )


def document(client: FuminosukeClient, wanted: str, *, template_id: str | None = None) -> Candidate:
    """ID の一致 → 書類名の完全一致 → 識別値の完全一致 → 書類名・識別値に含まれる。"""
    documents = client.documents(template_id=template_id)
    candidates = [_document_candidate(d) for d in documents]
    by_id = {d["jobId"]: d for d in documents}
    needle = compact(wanted)

    def name(c: Candidate) -> str:
        return compact(by_id[c.id].get("documentName") or "")

    def identities(c: Candidate) -> list[str]:
        return [compact(i["value"]) for i in by_id[c.id]["identity"] if i.get("value")]

    return _pick(
        "書類",
        wanted,
        candidates,
        [
            lambda c: c.id == wanted.strip(),
            lambda c: name(c) == needle,
            lambda c: needle in identities(c),
            lambda c: bool(needle) and any(needle in text for text in [name(c), *identities(c)]),
        ],
        hint=(
            "確定済みの書類だけが対象です。"
            "list_templates でテンプレートと件数を確かめてから、書類名で指定してください。"
        ),
    )


def _document_candidate(document: dict[str, Any]) -> Candidate:
    identity = "、".join(
        f"{i['label']}={i['value']}" for i in document["identity"] if i.get("value")
    )
    parts = [document["templateName"], identity, f"確定日 {local_day(document['confirmedAt'])}"]
    return Candidate(
        document["jobId"],
        document.get("documentName") or "（書類名なし）",
        "、".join(part for part in parts if part),
    )


def _pick(
    kind: str,
    wanted: str,
    candidates: Sequence[Candidate],
    rules: Sequence[Callable[[Candidate], bool]],
    *,
    hint: str,
) -> Candidate:
    for rule in rules:
        hits = [c for c in candidates if rule(c)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise Ambiguous(kind, wanted, hits)
    raise NotFound(kind, wanted, hint)
