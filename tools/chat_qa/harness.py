"""チャット（質問応答）の回帰試験：会話の台本を実サーバーへ送り、結果を記録する。

    python tools/chat_qa/harness.py tools/chat_qa/suites/regression.json out.txt
        [--base URL] [--origin http://localhost:PORT] [--compare prev.txt]

台本は [[名前, [発言, ...]], ...]。発言は文字列（新しい質問）か、["choose", 選択肢の一部]（直前の
聞き返しの選択肢を押す）、["reply", 文]（開いている聞き返しに言葉で答える）。文脈（直前の回答の
書類・質問・回答文）はチャット画面と同じ作り方で付ける。

実AI（Decisions／Responses）を使うので回数を消費する。回答は揺れることがあるため、--compare は
「前回の記録と答えが変わった会話」を並べるだけで、合否は人が読んで決める。unit テストではない。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "http://127.0.0.1:8010"


ORIGIN = "http://localhost:5180"


def post(base: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{base}/api/v2/structured/questions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Origin": ORIGIN},
    )
    try:
        return json.load(urllib.request.urlopen(request, timeout=120))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        return {
            "state": "ERROR",
            "answer": detail,
            "notes": [],
            "caveats": [],
            "documents": [],
            "modelCalls": 0,
            "table": None,
            "question": None,
        }


def run(base: str, conversation: list, out) -> None:
    turns: list[tuple[str, dict]] = []
    for step in conversation:
        kind, arg = (step if isinstance(step, tuple) else ("ask", step))
        last = next((t for t in reversed(turns) if t[1].get("documents")), None)
        context = (
            {
                "documentIds": [d["jobId"] for d in last[1]["documents"]][:20],
                "previous": last[0],
                "previousAnswer": (last[1].get("answer") or "")[:2000],
            }
            if last
            else None
        )
        open_question = next(
            (
                t[1]["question"]
                for t in reversed(turns)
                if t[1].get("question") and not t[1]["question"].get("optional")
            ),
            None,
        )
        if kind == "choose":
            question = (turns[-1][1].get("question") if turns else None) or {"choices": []}
            choice = next((c for c in question["choices"] if arg in c["label"]), None)
            if choice is None:
                print(f"Q[choose]: {arg} -> 選択肢なし", file=out, flush=True)
                continue
            body = {
                "text": "",
                "replyTo": {"questionId": question["questionId"], "value": choice["value"]},
            }
            asked = turns[-1][0]
        elif kind == "reply" and open_question:
            body = {"text": "", "replyTo": {"questionId": open_question["questionId"], "text": arg}}
            asked = f"{turns[-1][0]}（{arg}）"
        else:
            body = {"text": arg}
            asked = arg
        if context:
            body["context"] = context
        started = time.time()
        result = post(base, body)
        elapsed = int((time.time() - started) * 1000)
        turns.append((asked, result))
        question = result.get("question") or {}
        lines = [f"Q[{kind}]: {arg}", f"  A[{result['state']}] {result['answer']}".replace("\n", " / ")]
        if result.get("notes"):
            lines.append(f"  注記: {result['notes']}")
        if result.get("caveats"):
            lines.append(f"  注意: {result['caveats'][:3]}")
        if result.get("table"):
            table = result["table"]
            more = " …" if len(table["rows"]) > 4 else ""
            lines.append(f"  表: {table['columns']} {table['rows'][:4]}{more} ({len(table['rows'])}行)")
        if question:
            labels = [c["label"] for c in question.get("choices", [])][:8]
            optional = ",任意" if question.get("optional") else ""
            lines.append(f"  聞き返し({question.get('kind')}{optional}): {question.get('message')} {labels}")
        if result.get("open"):
            lines.append(f"  開く: {result['open'][:8]}")
        names = [d["documentName"] for d in result.get("documents", [])]
        lines.append(f"  書類={names} AI={result.get('modelCalls')} 一般={result.get('general')} {elapsed}ms")
        print("\n".join(lines), file=out, flush=True)
    print("", file=out, flush=True)


def parse(path: Path) -> dict[str, list[str]]:
    """会話ごとの要点（質問・答え・必須の聞き返し・開く）。注記や時間は比べない。"""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("=== "):
            current = line[4:].strip()
            out[current] = []
        elif current and (
            line.startswith("Q[")
            or line.startswith("  A[")
            or (line.startswith("  聞き返し(") and "任意" not in line)
            or line.startswith("  開く")
        ):
            out[current].append(line.rstrip()[:260])
    return out


def main() -> None:
    global ORIGIN
    parser = argparse.ArgumentParser()
    parser.add_argument("suite")
    parser.add_argument("output")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--compare", help="前回の記録。答えが変わった会話だけを標準出力に出す")
    parser.add_argument("--origin", default=ORIGIN, help="サーバーが許す画面のOrigin")
    args = parser.parse_args()
    ORIGIN = args.origin
    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    with open(args.output, "w", encoding="utf-8") as out:
        for name, conversation in suite:
            print(f"=== {name}", file=out, flush=True)
            run(args.base, [tuple(s) if isinstance(s, list) else s for s in conversation], out)
    if args.compare:
        before, after = parse(Path(args.compare)), parse(Path(args.output))
        changed = [name for name, lines in after.items() if before.get(name) != lines]
        print(f"変わった会話: {len(changed)}/{len(after)}")
        for name in changed:
            print(f"=== {name}")
            print("\n".join(after[name]))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    main()
