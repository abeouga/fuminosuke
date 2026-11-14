# デプロイ版のチャットのログ（意見箱）

デプロイ版（`BETA_CHAT_LOG=true`）は、チャットの質問・回答・聞き返し・エラー・×での削除を
1件ずつ objects バケットの `feedback/chat/<installation>/<UTCの日付>/…json` に残す
（`backend/app/cloud/chat_feedback.py`）。利用者の保存場所（tenants/）の外なので、容量・
バックアップに数えず、利用者を消しても残る。書き込みに失敗してもチャットは止めない（ログだけ）。

集めて読むには（S3 は読むだけ）。AWS のプロファイルとリージョンは `--profile`・`--region` か、
環境変数 `AWS_PROFILE`・`AWS_REGION`（無ければプロファイルの region）で渡す：

```bash
AWS_PROFILE=<プロファイル名> backend/.venv/Scripts/python.exe tools/chat_feedback/read.py out.md --since 2026-10-27
```

利用者ごと（Cognito のユーザー名）に時刻順で並べる。`--raw all.jsonl` で全行もそのまま書き出す。
聞き返しで書類を選んだ回答は、選んだ値（書類のID）で出る。
