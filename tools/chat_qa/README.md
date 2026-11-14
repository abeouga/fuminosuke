# チャットの回帰試験

`backend/app/structured/qa.py`（質問応答）を変えたら、この台本を実サーバーに流して、
前回の記録と答えが変わった会話を読む。実AIを使うため回数を消費し、答えは揺れる。
合否は人が読んで決める（unit テストではない）。

```bash
python tools/chat_qa/harness.py tools/chat_qa/suites/regression.json out.txt --compare tools/chat_qa/last/regression.txt
```

- `suites/regression.json`：2026-10-25 に欠陥を洗い出したときの台本（154会話）。通常DBの
  6書類（繁殖台帳1021「こよみ」「はじめ」、給水装置台帳1018「給水装置台帳１」「12345」、
  チェックリスト検証用2件）を前提にしている。書類が変わったら答えも変わる。
- `suites/fresh.json`：調整に使っていない言い方の台本。穴を探すときに使う。
- `last/`：最後に通した記録。手元にだけ置き、Git では管理しない（`.gitignore`）。変えたら差し替える。
- 送った質問はサーバーの `backend/data/logs/chat-questions.jsonl` に残るので、終わったら
  試験の行を消す。
