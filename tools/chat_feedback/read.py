"""Collect the deployed beta's chat log (feedback/chat/ in the objects bucket) into one file.

    backend/.venv/Scripts/python.exe tools/chat_feedback/read.py out.md [--since 2026-10-27]

Uses the AWS profile fuminosuke-deploy. Cognito usernames (admin, tester01, ...) are looked
up from the subject recorded in each line. Read only: nothing in S3 is changed.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import boto3

JST = ZoneInfo("Asia/Tokyo")


def usernames(session: Any) -> dict[str, str]:
    cognito = session.client("cognito-idp")
    names: dict[str, str] = {}
    for pool in cognito.list_user_pools(MaxResults=60)["UserPools"]:
        if "fuminosuke" not in pool["Name"]:
            continue
        for page in cognito.get_paginator("list_users").paginate(UserPoolId=pool["Id"]):
            for user in page["Users"]:
                sub = next((a["Value"] for a in user["Attributes"] if a["Name"] == "sub"), "")
                names[sub] = user["Username"]
    return names


def lines(session: Any, bucket: str, since: str | None) -> list[dict[str, Any]]:
    s3 = session.client("s3")
    found = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket,
                                                              Prefix="feedback/chat/"):
        for item in page.get("Contents", []):
            day = item["Key"].split("/")[3]
            if since and day < since:
                continue
            body = s3.get_object(Bucket=bucket, Key=item["Key"])["Body"].read()
            found.append(json.loads(body))
    return sorted(found, key=lambda line: line["at"])


REASONS = {
    "wrong_value": "数値が違う",
    "not_wanted": "探していたものと違う",
    "unclear": "分かりにくい",
    "other": "その他",
}


def describe(line: dict[str, Any]) -> list[str]:
    if line.get("type") == "remove":
        texts = [turn.get("text") or "" for turn in line.get("turns") or []]
        return [f"- ×で削除: {' / '.join(texts)}"]
    if line.get("type") == "rating":
        turn = (line.get("turns") or [{}])[0]
        mark = {"good": "👍", "bad": "👎"}.get(line.get("rating") or "", "評価を取り消し")
        reason = REASONS.get(line.get("reason") or "", "")
        out = [f"- {mark}{'（' + reason + '）' if reason else ''}: {turn.get('text') or ''}"]
        if line.get("comment"):
            by = "音声" if line.get("commentBy") == "voice" else "文字"
            out.append(f"  - ひとこと（{by}）: {line['comment']}")
        return out
    reply = line.get("replyTo") or {}
    text = line.get("text") or reply.get("text") or reply.get("value") or ""
    head = "- 聞き返しへの回答" if line.get("type") == "reply" else "- 質問"
    out = [f"{head}: {text}"]
    if outcome := line.get("outcome"):
        if outcome.get("answer"):
            out.append("  - 回答: " + str(outcome["answer"]).replace("\n", " ")[:400])
        if question := outcome.get("question"):
            labels = [choice.get("label") or "" for choice in question.get("choices") or []]
            out.append(f"  - 聞き返し: {question.get('message')}")
            if labels:
                out.append("    - 選択肢: " + "、".join(labels))
    if error := line.get("error"):
        out.append(f"  - エラー: {error.get('code')} {error.get('message')}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--since", help="UTC date YYYY-MM-DD")
    parser.add_argument("--profile", default="fuminosuke-deploy")
    parser.add_argument("--raw", type=Path, help="also write every line as JSONL")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name="ap-northeast-1")
    account = session.client("sts").get_caller_identity()["Account"]
    found = lines(session, f"fuminosuke-objects-{account}", args.since)
    names = usernames(session)
    if args.raw:
        args.raw.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in found),
                            encoding="utf-8")
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in found:
        by_user[names.get(line.get("subject", ""), line.get("subject") or "?")].append(line)
    out = [f"# チャットのログ（{len(found)}件）", ""]
    for user, entries in sorted(by_user.items()):
        out += [f"## {user}（{len(entries)}件）", ""]
        for line in entries:
            at = line["at"]
            local = datetime.fromisoformat(at).astimezone(JST)
            out.append(f"### {local:%m/%d %H:%M:%S}")
            out += describe(line)
            out.append("")
    args.out.write_text("\n".join(out), encoding="utf-8")
    print(f"{len(found)} lines -> {args.out}")


if __name__ == "__main__":
    main()
