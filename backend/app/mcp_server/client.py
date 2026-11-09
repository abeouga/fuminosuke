"""文之助のサーバーへの HTTP の呼び出し（読み取りだけ）。

同じPCからの要求として送る（Origin を付けない）。文之助の業務の規則（確定済みだけ・最新の版
だけ・ごみ箱を除く）は呼び先のAPIが守るので、ここでは絞り込みや計算をしない。
"""

from __future__ import annotations

from typing import Any, cast
from urllib.parse import quote

import httpx

from app.mcp_server.errors import Disabled, ServerFailure, Unavailable

DEFAULT_TIMEOUT = 15.0
# 一覧で一度に読む書類の数（文之助の一覧APIの上限）。
LIST_LIMIT = 1000


class Refused(ServerFailure):
    """呼び先が 4xx を返した（409 の候補・422 の項目の誤りを持つ）。道具が文章に直す。"""

    def __init__(self, status: int, problem: dict[str, Any]) -> None:
        self.status = status
        self.code: str | None = problem.get("code")
        self.message = str(problem.get("message") or problem.get("detail") or f"HTTP {status}")
        self.candidates: list[dict[str, Any]] = list(problem.get("candidateHandles") or [])
        self.field_errors: list[dict[str, Any]] = list(problem.get("fieldErrors") or [])
        super().__init__(self.message)


class NotFoundResponse(Refused):
    """呼び先が 404 を返した。何が見つからなかったかは呼び出し側が文章にする。"""


class FuminosukeClient:
    """1回の道具の呼び出しの間だけ使う。同じ書類を2回読まないよう、その間だけ覚える。"""

    def __init__(
        self, base_url: str, timeout: float = DEFAULT_TIMEOUT, *, web_url: str | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # 文之助の画面の住所。API のサーバーは画面を配らないので、埋め込み用の画面はこちらで作る。
        self.web_url = web_url.rstrip("/") if web_url else None
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)
        self._documents: dict[str, dict[str, Any]] = {}

    def __enter__(self) -> FuminosukeClient:
        return self

    def __exit__(self, *_: object) -> None:
        self._http.close()

    def embed(self, job_id: str, field_id: str | None) -> str | None:
        """書類（と欄）を見るだけの埋め込み用の画面の絶対URL。画面の住所の設定がなければ None。"""
        if not self.web_url:
            return None
        query = f"job={quote(job_id, safe='')}"
        if field_id:
            query += f"&field={quote(field_id, safe='')}"
        return f"{self.web_url}/embed?{query}"

    def templates(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self._get("/api/v1/templates").json())

    def documents(
        self, *, template_id: str | None = None, q: str | None = None, limit: int = LIST_LIMIT
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if template_id:
            params["templateId"] = template_id
        if q:
            params["q"] = q
        body = self._get("/api/v2/structured/documents", params=params).json()
        return cast(list[dict[str, Any]], body["items"])

    def document(self, job_id: str) -> dict[str, Any]:
        if job_id not in self._documents:
            path = f"/api/v2/structured/documents/{quote(job_id, safe='')}"
            self._documents[job_id] = cast(dict[str, Any], self._get(path).json())
        return self._documents[job_id]

    def fields(self, template_id: str) -> list[dict[str, Any]]:
        body = self._get("/api/v2/structured/fields", params={"templateId": template_id}).json()
        return cast(list[dict[str, Any]], body["items"])

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        """欄の値・確定日などで絞った確定済みの書類（POST /documents/search）。"""
        return cast(
            dict[str, Any], self._send("POST", "/api/v2/structured/documents/search", body).json()
        )

    def aggregate(self, body: dict[str, Any]) -> dict[str, Any]:
        """数値の欄・列の集計（POST /aggregations）。計算は文之助のサーバーが行う。"""
        return cast(
            dict[str, Any], self._send("POST", "/api/v2/structured/aggregations", body).json()
        )

    def image(self, path: str) -> bytes:
        """文之助が返した相対のURL（/api/v1/jobs/…/artifacts/…）の画像だけを読む。"""
        if not path.startswith("/api/v1/jobs/"):
            raise ServerFailure("切り抜き画像の住所が文之助の画像の形ではありません。")
        return self._get(path).content

    def absolute(self, path: str | None) -> str | None:
        """相対のURLに文之助のサーバーの住所を付ける（画面でそのまま開けるように）。"""
        if path is None:
            return None
        return path if path.startswith(("http://", "https://")) else self.base_url + path

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._send("GET", path, params=params)

    def _send(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            response = self._http.request(method, path, params=params, json=body)
        except httpx.TransportError as error:
            if isinstance(error, httpx.ConnectError | httpx.ConnectTimeout):
                raise Unavailable(self.base_url) from error
            raise ServerFailure(f"応答がありませんでした（{type(error).__name__}）。") from error
        if response.is_success:
            return response
        problem = _problem(response)
        if response.status_code == 404:
            raise NotFoundResponse(404, problem)
        if response.status_code == 503 and problem.get("code") == "STRUCTURED_DATA_DISABLED":
            raise Disabled()
        if 400 <= response.status_code < 500:
            raise Refused(response.status_code, problem)
        message = problem.get("message") or problem.get("detail") or f"HTTP {response.status_code}"
        raise ServerFailure(str(message))


def _problem(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
