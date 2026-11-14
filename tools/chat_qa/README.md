# チャットの回帰試験

`backend/app/structured/qa.py`（質問応答）を変えたら、この台本を実サーバーに流して、
前回の記録と答えが変わった会話を読む。実AIを使うため回数を消費し、答えは揺れる。
合否は人が読んで決める（unit テストではない）。

```bash
python tools/chat_qa/harness.py tools/chat_qa/suites/regression.json out.txt --compare tools/chat_qa/last/regression.txt
```

- `suites/regression.json`：見つかった欠陥を確かめるための台本（154会話）。繁殖台帳の「こよみ」「はじめ」、
  給水装置台帳の「給水装置台帳１」「12345」、チェックリストの書類2件が確定済みであることを前提にしている。
  書類が変わったら答えも変わる。
- `suites/fresh.json`：調整に使っていない言い方の台本。穴を探すときに使う。
- `suites/extra.json`：集計・確認待ちの書類など、regression を補う台本。
- `suites/speed.json`：書類が多いときの答えの速さを見る台本。記録の各行に、かかった時間（ms）が出る。
- `last/`：最後に通した記録。手元にだけ置き、Git では管理しない（`.gitignore`）。変えたら差し替える。
- 送った質問はサーバーの `backend/data/logs/chat-questions.jsonl` に残るので、終わったら
  試験の行を消す。
