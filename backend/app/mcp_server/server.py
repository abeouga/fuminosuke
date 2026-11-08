"""FastMCP のサーバーと道具。道具は文之助のAPIを呼び、根拠の形に並べ替えるだけ。"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence
from typing import Annotated, Any, TypeVar

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, ContentBlock, TextContent
from pydantic import BaseModel, Field

from app.mcp_server import evidence, resolve
from app.mcp_server.client import FuminosukeClient, NotFoundResponse
from app.mcp_server.errors import NotFound, ToolProblem
from app.mcp_server.models import DocumentValues

DEFAULT_API_URL = "http://127.0.0.1:8010"

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

    return server


def _answer(model: BaseModel, text: str) -> CallToolResult:
    """構造化データ（画面用、根拠の全項目）と、AIが読む短い文章（同じ値・欄名・ID）の両方を返す。"""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=model.model_dump(mode="json"),
    )


def _read(client: FuminosukeClient, job_id: str) -> dict[str, Any]:
    try:
        return client.document(job_id)
    except NotFoundResponse as error:
        # 一覧の後に再編集・削除された（確定済みでなくなった）。
        raise NotFound("確定済みの書類", job_id) from error
