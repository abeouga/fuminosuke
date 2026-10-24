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
- `last/`：最後に通した記録。変えたら差し替える。
- 送った質問はサーバーの `backend/data/logs/chat-questions.jsonl` に残るので、終わったら
  試験の行を消す。

## 書類が多いときの速さ

`D:\llm-ocr-archive\chat-scale-20261025` に、通常DBの複製に書類を60件足した環境（66件）がある
（`serve.py`、launch.json の `chat-scale-backend`、8120、Originは `http://localhost:5220`）。
書類を増やすスクリプトは同じフォルダの `scale.py`（複製DBにだけ使う）。

```bash
python tools/chat_qa/harness.py tools/chat_qa/suites/speed.json out.txt --base http://127.0.0.1:8120 --origin http://localhost:5220
```

2026-10-25 の計測（66件、2回目＝キャッシュあり）：AIを使わない質問 0.1〜0.3秒、AI1回 0.6〜0.9秒、
一般回答 2〜5秒。キャッシュは確定した書類の一覧（確定の件数・最新の確定日時・ごみ箱・移動・系列の
件数が変わるまで、最長120秒）と、書類ごとの投影（確定版ごと、最長120秒）。
