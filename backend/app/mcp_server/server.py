"""FastMCP のサーバーと道具。道具は文之助のAPIを呼び、根拠の形に並べ替えるだけ。"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, ContentBlock, TextContent

from app.mcp_server.client import FuminosukeClient
from app.mcp_server.errors import ToolProblem

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
    return server
