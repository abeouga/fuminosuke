# 修正5-B：行番号対応・R04評価基準の独立再審査

2026-10-13。**審査合格。修正6の実API比較へ進める。** 修正5-Aで追加した5製品ファイルと保存証拠を再審査し、差戻しを必要とする不具合は確認しなかった。R04の固定評価基準を開発用の白紙帳票評価に承認する。実Lunaの品質・応答時間や最終ゴールの達成を認定するものではない。

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。実行記録は `gpt-6-astra` / `xhigh`。修正5-Aの `gpt-6.1-sol` / `high` から担当モデルを切り替えた再審査であり、新しい別エージェントは起動していない。製品コード・設定・通常業務データは変更しない。実APIと通常台帳予約は0回、432/924・残492。

証拠は `D:\llm-ocr-archive\template-ai\20261013-row-key-review`。実装の正本は [修正5-A](template-ai-row-key-repair-2026-10-13.md)、元の指摘は [修正5](template-ai-structure-review-2026-10-13.md)。後続の実験条件は [統合設計](template-ai-structure-repair-design-2026-10-13.md) を維持する。

## 判定と根拠

| 審査対象 | 判定 | 根拠 |
| --- | --- | --- |
| R1/P1：中間行を行番号として扱う欠落検査の迂回 | 解消 | 元ASGIの変更内容と今回の制御応答を照合。実行固有のcatalogDigest以外の意味・矩形宣言は一致。旧結果は2行4mappingで保存、現在は保存前に拒否しrevision 1を保持 |
| 解析から共通保存への到達 | 合格 | 隔離した実ASGIで13シナリオを再実行。正常な左右の行番号は3行6mapping、revision 2で別接続から取得。元のセルID・座標・行順を保持 |
| 手動保存・矩形のみの変更 | 合格 | 不正な旧下書きの直接保存と意味コマンドのない保存を拒否。共通 `TemplateAuthoringService.prepare()` 単独でも拒否し、DB/config書込み前に停止 |
| 所有・行対応・書込範囲 | 合格 | 追加16条件。省略可能な行番号、部分指定、配列順とID対応、上下入替、重複・不明ID、本文セルの行番号流用、2px/3px侵入、複数行を覆う番号、他表による同じ番号IDの所有、role変更、書込範囲外を確認 |
| 旧解析の失効 | 合格 | resolution policy v1の案はGETでstale。承認後の保存もCONFLICT / SNAPSHOT_STALE、revisionを保持。policy v2がruntime contract比較へ接続されることをコードでも確認 |
| R04評価基準 | 承認 | 原PNGの全体・対象一覧と位置を照合。原PDFを別途読み、29印字選択肢の文字・座標を独立確認。145対象のID、親、countFor、許容領域、日付4欄の成分、評価行の対応を検査 |

対象製品ファイルは `layout_analysis/record_geometry.py`、`template_analysis/binding_resolution.py`、`template_analysis/visual_form_answer.py`、`editing/record_commands.py`、`services/template_authoring_service.py`（いずれも `backend/app/` 以下）。共通の行番号検査はHTTP・provider・DBへ依存せず、解析と保存が同じ規則を使用する。`prepare_layout()` の後続処理が行番号の物理対応を付け替えないこと、モデルによるsection・cell参照検査と併用されることも確認した。

50%の縦重なりと2pxの境界許容は、定義済みの物理対応検査である。印字の読取精度、帳票に存在しない意味、すべての撮影条件への適応を保証しない。今回の判定は、以前の欠落検査を迂回する具体的な誤保存を修正したことに基づく。

## R04承認の対象と扱い

承認対象は修正5-Aの `r04-gold/gold.json`、SHA256 `5b3ca352bf6822d87e26d91ad47d045f64272906be50a2c4175a0642da5bee17`。元の候補・manifest・評価表は書き換えない。今回の `gold-approval.json` が、このhashに対する独立審査の承認記録となる。旧ファイル内の `acceptedGold=false` は作成時点の状態である。

- 幾何145位置、機能143項目、手動扱い2領域。選択肢88と自由記入・通数・日付55を保持し、職員欄も含める。欠落・未解決・未対応は分母から除かない。
- 実際の保存・プレビューの読取領域で各必須コアの95%以上を覆い、許容外枠からの突出を2px以内とする。日付は元号を含む印字済みの成分を完全に保持し、一欄または成分分割を許容する。
- 同一セルでまとめた選択肢は独立した保存値が必要。未割当の隣接記入欄・通数を混在させない。名称や生成IDの完全一致を採点基準にしない。
- U01〜U05の判断を維持する。「組」の所属を推測せず、申請日と生年月日を分け、印字選択肢を落とさず、印字にない単一選択規則を追加しない。
- 受付・出納印は別領域として保持し、未対応・手動扱いを別集計する。これらを文字認識成功へ算入しない。

白紙帳票の構造・意味評価に限定した承認であり、記入済み筆跡のcrop clearanceやOCR文字精度の認定ではない。実測では145行の評価表へ、保存されたcell/mark set/option ID・targetPath・capture SHA・理由を記録する。新しいモデル出力を見て基準を変更する場合は、新版と原本に基づく理由を必要とする。

Chromeの評価資料は原画像1809×1279と145のSVG群が読み込まれていることを確認した。スクリーンショット取得は応答せず、待機を打ち切った。原PNGは画像表示で直接検査したが、新しいChromeキャプチャの成功や製品E2Eの実施とは報告しない。

## 実行した検証

専用Pythonは `backend/.venv/Scripts/python.exe`、作業ディレクトリはdeployの `backend`。次のスクリプトはいずれも今回の証拠フォルダーに配置し、過去の出力先へ実行しない。

| 実行 | 結果 |
| --- | --- |
| `python -X utf8 -B integration_row_keys.py` | 13シナリオ通過。専用DB予約・制御HTTPは11回、実外部APIと通常予約は0回 |
| `python -X utf8 -B independent_boundaries.py` | 独立に追加した16条件通過 |
| `python -X utf8 -B check_bindings.py` | 既存50条件通過 |
| `python -X utf8 -B check_structure.py` | 既存34条件通過 |
| `python -X utf8 -B compare_evidence.py` | 元ASGIの2反例の変更内容一致、旧保存2行4mappingを確認。旧封印60/61一致、既知の1例外を記録 |
| 同梱PDF用Pythonで `review_gold.py` | 原本hash、145対象、29印字選択肢、4日付、評価表・許容領域の検査通過 |
| `git diff --check` と開始終了比較 | 最終封印時に結果を記録。変更範囲は文書3ファイルのみ |

検証準備の初回は、読取専用のDB監査でMemoryErrorが発生した。並行していた大きな会話ログ読取の終了後、同じ監査が完了した。複製したASGI検証も初回は補助モジュール `wire_fixture.py` の不足で停止し、元と同一内容の補助ファイルを複製して再実行した。失敗ログと条件を保持し、製品の不具合や成功件数に混同しない。既存のassertion・fixtureは変更していない。

製品コードを編集していないためbuild・全体unit/E2E・静的検査の全再実行は行わない。前回の対象ruff/mypy成功と依存先mypy既存61件は封印済み記録を確認したものであり、今回の全体型検査成功とは扱わない。

## 証拠と保存境界

開始時に修正5-Aの477ファイルを全件照合した。修正5で失われた `omitted_row_as_keys.json` の原bytesは復元不能のまま。旧manifestを改変せず、旧ASGIの制御応答・receipt・saved結果・DB・要約でR1の根拠を確認した。この既知例外を今回の封印にも継承する。今回、旧出力への上書きは行っていない。

終了時は通常863ファイル、781artifact、全通常DBテーブル・schema・設定・台帳、旧 `llm-ocr`、前工程の証拠と最終4原本14ファイルを照合する。最終4帳票の内容は閲覧しない。文書の追記前の原本を今回の `before-source` に保持し、以前の文書hashも検証可能にする。staging・commit・pushは行わない。具体的結果は `phase-result.json`、`result-hashes.json` に固定する。

今回の製品コード変更は0件であり、既存OCR contractを維持した。mapping identity、physical identity、crop、補正値、revision、確定snapshot、出力形式を変更していない。

## 次工程

**修正6：GPT-6.1 Sol / High**。APIモデルはGPT-6 Luna。最初のR02 p2・R04 p1のnone/low各1回、計4回を最大27回の内数として実行する。schema受入・重大な誤対応・打切りを確認してから後続を判断する。失敗試行は消去・払戻し・同一trialの再試行を行わない。要求数が変われば送信前に試行表と上限を再計算する。

初回の利用可能な案、全体完了、訂正完了をそれぞれ計測する。途中JSONや進捗表示は利用可能な案に数えない。重大な契約不具合があれば後続送信を止める。方式固定後の修正7はAstra / XHighで未使用4帳票と手作業時間を評価する。

修正5-Bの審査は完了。実Lunaの精度・初回5秒・最終4帳票・人間の作業時間比較は未完了であり、製品ゴールは未達成。今回は修正6の実API送信を開始しない。
