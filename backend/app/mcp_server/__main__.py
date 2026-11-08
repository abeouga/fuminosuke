"""起動の入口：`python -m app.mcp_server`（標準入出力）。

標準出力は MCP の通信に使うので、ログは標準エラーへ出す。文之助の設定（.env）は読まず、
AIのキーも持たない。呼び先は環境変数 FUMINOSUKE_API_URL（既定 http://127.0.0.1:8010）。
"""

from __future__ import annotations

import logging
import os
import sys

from app.mcp_server.server import DEFAULT_API_URL, build_server


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_server(os.environ.get("FUMINOSUKE_API_URL", DEFAULT_API_URL)).run("stdio")


if __name__ == "__main__":
    main()
