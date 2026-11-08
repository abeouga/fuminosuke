"""AIに返すエラーの文章。道具の結果は isError として、この文章だけを返す。"""

from __future__ import annotations

from dataclasses import dataclass

# 候補を並べる数の上限（多すぎるとAIが選べず、返す量も増える）。
MAX_CANDIDATES = 10


class ToolProblem(Exception):
    """AIが次の手を決められるよう、文章で伝える失敗。"""


class Unavailable(ToolProblem):
    def __init__(self, url: str) -> None:
        super().__init__(
            f"文之助のサーバー（{url}）に接続できません。起動しているか確かめてください。"
        )


class Disabled(ToolProblem):
    def __init__(self) -> None:
        super().__init__("文之助の確定済みデータの機能が無効です。")


class ServerFailure(ToolProblem):
    def __init__(self, message: str) -> None:
        super().__init__(f"文之助のサーバーがエラーを返しました：{message}")


@dataclass(frozen=True, slots=True)
class Candidate:
    id: str
    name: str
    # テンプレート名・識別値・確定日など、AIが見分けるための説明。
    detail: str


class Ambiguous(ToolProblem):
    def __init__(self, kind: str, wanted: str, candidates: list[Candidate]) -> None:
        self.candidates = candidates
        shown = candidates[:MAX_CANDIDATES]
        lines = [f"・{c.name}（ID: {c.id}、{c.detail}）" for c in shown]
        if len(candidates) > len(shown):
            rest = len(candidates) - len(shown)
            lines.append(f"ほか{rest}件。もっと詳しい名前で指定してください。")
        super().__init__(
            f"「{wanted}」に当てはまる{kind}が{len(candidates)}件あります。"
            "次のどれかを、名前又はIDで指定してください：\n" + "\n".join(lines)
        )


class NotFound(ToolProblem):
    def __init__(self, kind: str, wanted: str, hint: str | None = None) -> None:
        text = f"「{wanted}」という{kind}は見つかりませんでした。"
        super().__init__(text + hint if hint else text)
