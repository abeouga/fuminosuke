"""FastMCP のサーバーと道具。道具は文之助のAPIを呼び、根拠の形に並べ替えるだけ。"""

from __future__ import annotations

import functools
import io
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, Any, TypeVar

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.utilities.types import Image
from mcp.types import CallToolResult, ContentBlock, TextContent
from PIL import Image as PILImage
from pydantic import BaseModel, Field

from app.mcp_server import evidence, resolve
from app.mcp_server.client import FuminosukeClient, NotFoundResponse
from app.mcp_server.errors import NotFound, ToolProblem
from app.mcp_server.models import (
    DocumentValues,
    TableColumns,
    TemplateFields,
    TemplateList,
    TemplateRef,
    TemplateSummary,
)

DEFAULT_API_URL = "http://127.0.0.1:8010"
# get_field_image で返す画像の長い辺の上限。
MAX_IMAGE_EDGE = 1024

INSTRUCTIONS = """\
このサーバーは、文之助（帳票の読み取りシステム）で人が確認・確定した帳票の値だけを返します。
- 返した値をそのまま使い、推測で埋めたり言い換えたりしないでください。
- 数の計算（合計・平均・件数・最大・最小）は自分でしないでください。
  計算の道具がないときは、その旨を利用者に伝えてください。
- 答えには、書類名と欄名を根拠として添えてください。
- 値が違いそうなときは直そうとせず、文之助の確認画面で確かめるよう利用者に勧めてください。
- 名前が1つに決まらないと返されたら、候補から選び直すか、利用者に聞いてください。
- 最初に list_templates、欄名が分からなければ get_template_fields を呼んでください。
"""

T = TypeVar("T")


class FuminosukeServer(FastMCP):
    """道具の中の ToolProblem を、その文章だけの isError の結果として返す。"""

    def __init__(self, base_url: str) -> None:
        super().__init__("fuminosuke", instructions=INSTRUCTIONS)
        self.base_url = base_url

    async def call_tool(  # type: ignore[override]  # the low-level server accepts CallToolResult
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any] | CallToolResult:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as error:
            if isinstance(error.__cause__, ToolProblem):
                text = str(error.__cause__)
                return CallToolResult(content=[TextContent(type="text", text=text)], isError=True)
            raise

    async def ask(self, work: Callable[[FuminosukeClient], T]) -> T:
        """1回の道具の呼び出しを、その間だけの接続で、別スレッドで行う（通信を待つ間も応答できる）。"""

        def run() -> T:
            with FuminosukeClient(self.base_url) as client:
                return work(client)

        return await anyio.to_thread.run_sync(functools.partial(run))


def build_server(base_url: str = DEFAULT_API_URL) -> FuminosukeServer:
    server = FuminosukeServer(base_url)

    @server.tool()
    async def list_templates() -> Annotated[CallToolResult, TemplateList]:
        """使用中の書類の種類（テンプレート）と、確定済みの書類の件数・最新の確定日を返す。

        最初に呼んで、どんな書類があるかを確かめる。件数0のテンプレートも返す。
        ごみ箱のテンプレートと、確定前の書類は含まない。
        """

        def work(client: FuminosukeClient) -> CallToolResult:
            listed = resolve.catalog(client)
            result = _template_list(listed)
            lines = [result.summary]
            lines.extend(
                f"- {t.name}（ID {t.template_id}）：{t.document_count}件"
                + (f"、最新の確定 {t.latest_confirmed_at[:10]}" if t.latest_confirmed_at else "")
                for t in result.templates
            )
            return _answer(result, "\n".join(lines))

        return await server.ask(work)

    @server.tool()
    async def get_template_fields(
        template: Annotated[str, Field(description="テンプレートの名前又はID")],
    ) -> Annotated[CallToolResult, TemplateFields]:
        """その種類の書類にある欄の名前と、表ごとの列の名前を返す。

        欄名が分からないとき、他の道具で欄を正しく指定するために使う。名前は直近の
        確定済みの書類にあるものから取る（確定済みの書類が1件もなければ出せない）。
        """

        def work(client: FuminosukeClient) -> CallToolResult:
            chosen = resolve.template(client, template)
            result = _template_fields(chosen.id, chosen.name, client.fields(chosen.id))
            lines = [result.summary]
            if result.fields:
                lines.append("欄：" + "、".join(result.fields))
            lines.extend(f"表「{t.title}」の列：" + "、".join(t.columns) for t in result.tables)
            return _answer(result, "\n".join(lines))

        return await server.ask(work)

    @server.tool()
    async def get_document(
        document: Annotated[
            str, Field(description="書類名・識別値（名号・受付番号など）又は書類ID")
        ],
        template: Annotated[
            str | None,
            Field(
                description="同じ名前の書類が複数のテンプレートにあるときの、テンプレートの名前又はID"
            ),
        ] = None,
        fields: Annotated[
            list[str] | None,
            Field(
                description=(
                    "欲しい欄の名前（欄名・見出し・表名・列名、又は「表名 列名」）。省略すると全部"
                )
            ),
        ] = None,
    ) -> Annotated[CallToolResult, DocumentValues]:
        """1件の確定済みの書類の値を、根拠（書類ID・欄・画像上の位置・切り抜きのURL）付きで返す。

        「はじめの毛色は？」のように、特定の書類の値を聞かれたときに使う。値は画面に出す値で、
        保存値が違うときだけ stored_value に入る。表は値のある行だけ（1表100行まで）。
        名前が1つに決まらなければ候補を返すので、選び直すか利用者に聞く。
        """

        def work(client: FuminosukeClient) -> CallToolResult:
            template_id = resolve.template(client, template).id if template else None
            chosen = resolve.document(client, document, template_id=template_id)
            values = evidence.document_values(client, _read(client, chosen.id), fields)
            return _answer(values, evidence.document_text(values))

        return await server.ask(work)

    @server.tool(structured_output=False)
    async def get_field_image(
        document_id: Annotated[str, Field(description="get_document が返した書類ID")],
        field_id: Annotated[str, Field(description="get_document が返した欄ID（field_id）")],
    ) -> list[Image | str]:
        """欄の切り抜き画像（元の手書き）を返す。PNG、長い辺1024pxまで、1回に1枚。

        読み取った値を元の手書きで確かめたいとき、利用者に元の記入を見せたいときに使う。
        画像の文字を読んで値を直さない（値は get_document が返した確定済みの値を使う）。
        """

        def work(client: FuminosukeClient) -> list[Image | str]:
            chosen = resolve.document(client, document_id)
            document = _read(client, chosen.id)
            reading = _field(document, field_id)
            if not reading.get("cropUrl"):
                raise ToolProblem(f"欄 {field_id} には切り抜き画像がありません。")
            item = evidence.reading_evidence(client, document, reading)
            data = _shrink(client.image(reading["cropUrl"]))
            value = item.value if item.value is not None else "空欄"
            return [
                Image(data=data, format="png"),
                f"{item.label}の切り抜き（確定済みの値：{value}）",
            ]

        return await server.ask(work)

    return server


def _template_list(listed: resolve.Catalog) -> TemplateList:
    counts: dict[str, int] = {}
    latest: dict[str, str] = {}
    for document in listed.documents:
        template_id = document["templateId"]
        counts[template_id] = counts.get(template_id, 0) + 1
        latest[template_id] = max(latest.get(template_id, ""), document["confirmedAt"])
    templates = [
        TemplateSummary(
            template_id=t["id"],
            name=t["name"],
            document_count=counts.get(t["id"], 0),
            latest_confirmed_at=resolve.local_time(latest.get(t["id"])),
        )
        for t in listed.templates
    ]
    # 書類の多い順、同じなら最近確定した順（ライブラリで目に付く順に近い）。
    templates.sort(key=lambda t: (-t.document_count, -_stamp(t.latest_confirmed_at)))
    return TemplateList(
        templates=templates,
        summary=(
            f"使用中のテンプレートは{len(templates)}種類、"
            f"確定済みの書類は{len(listed.documents)}件です。"
        ),
    )


def _stamp(value: str | None) -> float:
    return datetime.fromisoformat(value).timestamp() if value else 0.0


def _template_fields(template_id: str, name: str, names: list[dict[str, Any]]) -> TemplateFields:
    fields = [n["label"] for n in names if n["kind"] == "field"]
    tables: dict[str, list[str]] = {}
    for n in names:
        if n["kind"] == "column":
            tables.setdefault(n.get("tableTitle") or "（表名なし）", []).append(n["label"])
    if names:
        summary = f"{name}：表の外の欄 {len(fields)}個、表 {len(tables)}つ。"
    else:
        summary = f"{name}：確定済みの書類がまだないため、欄の名前を出せません。"
    return TemplateFields(
        template=TemplateRef(template_id=template_id, name=name),
        fields=fields,
        tables=[TableColumns(title=title, columns=columns) for title, columns in tables.items()],
        summary=summary,
    )


def _answer(model: BaseModel, text: str) -> CallToolResult:
    """構造化データ（画面用、根拠の全項目）と、AIが読む短い文章（同じ値・欄名・ID）の両方を返す。"""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=model.model_dump(mode="json"),
    )


def _field(document: dict[str, Any], wanted: str) -> dict[str, Any]:
    """欄ID の一致。無ければ欄名（文字のそろえ方は compact）が1つだけ当たるもの。"""
    readings: list[dict[str, Any]] = document["fields"]
    hit = next((r for r in readings if r["fieldId"] == wanted.strip()), None)
    if hit is not None:
        return hit
    named = [r for r in readings if resolve.compact(r["label"]) == resolve.compact(wanted)]
    if len(named) == 1:
        return named[0]
    raise ToolProblem(f"この書類に欄 {wanted} はありません。get_document で欄を確かめてください。")


def _shrink(data: bytes) -> bytes:
    """長い辺を MAX_IMAGE_EDGE までに縮めて PNG に。"""
    with PILImage.open(io.BytesIO(data)) as image:
        image.load()
        if max(image.size) > MAX_IMAGE_EDGE:
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
        if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
            image = image.convert("RGB")
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        return out.getvalue()


def _read(client: FuminosukeClient, job_id: str) -> dict[str, Any]:
    try:
        return client.document(job_id)
    except NotFoundResponse as error:
        # 一覧の後に再編集・削除された（確定済みでなくなった）。
        raise NotFound("確定済みの書類", job_id) from error
