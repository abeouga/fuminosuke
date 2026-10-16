# 音声・Decisions再設計 フェーズ3：判断APIの接続

実施日：2026-10-08〜09。対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。
[フェーズ1設計](voice-editing-redesign-phase1-2026-10-08.md) §3〜5を、
[フェーズ2の共通command](voice-editing-redesign-phase2-2026-10-08.md)へ接続した。
この工程の実装と限定検証は完了。通常有効化・音声取得・画面は未接続。

## 実装した境界

- `editing/domain/fast_path.py` が、全文を扱える明示的な最大4節の値変更だけを抽出する。
  数値・引用文字列・明示空欄が対象。自由文・条件・言い直し・意味付けはResponsesへ渡す。
- `planning_catalog.py` は所有catalogを投影する。表・列・保存順の行・疎な割当をまとめ、
  現在の全セル値・layout・保存pathをモデルへ渡さない。通常の値変更では列名を帳票別にハードコードしない。
  表選択、明示表、直近表、全表の一意性を順に適用する。モデルの表refだけでは全表の曖昧さを解消しない。
- `candidates.py` は最大252変更候補＋3分岐を一質問にする。最大4質問を一要求で送り、
  候補ID・質問順・回答件数・確率集合・世代を照合する。候補を上位だけに切り詰めない。
  一節の保留・拒否で他節だけ保存しない。低確信のresponses選択でも質問し、自動で二次要求しない。
- `instruction_planner.py` はDecisions最大1回、必要時のResponses最大1回を制御する。
  障害・拒否・不正応答を別モデルへの再試行にしない。catalog cacheは世代付き・最大32件。
- `plan_binder.py` は閉じたSchema、原文Evidence、有限selector、明示行列、値の節、共有操作型を検査する。
  生成値・別節の値・単位の一部だけの引用・不明ref・重複する同一属性の書込みを拒否する。
  名前「受付番号」と短い構成ラベル「番」「号」は、最長の正式名称一致を優先し、同じ長さの重複候補は残す。
- 数字の共通純粋関数を `domain/numbers.py` へ分離した。旧adapterは同じ関数を再公開する。
  00789／六万→60000、引用された6万／6万kgの保持を扱う。名称・手動値・既存OCR値を一括変換しない。
- `infrastructure/planner_provider.py` が固定のOpenAI送信先へ接続する。
  既存OCR SDKを変更せず、既存HTTP予算予約を使用する。Decisions 3秒／Responses 15秒、8192出力token、
  store=false、指定した単一tool、retryなし。モデルは両経路とも `gpt-6-luna`。
- runtime／v2 APIから接続し、言語によるundoは既存inverseへ渡す。
  予算不足は新規指示の202前に拒否する。既存receiptの再取得は予算枯渇後もできる。
  実送信前の原子的予約も維持し、取消・timeoutで払い戻さない。

共通22操作の定義は `operation_contracts.py` を正本とし、モデル用Schemaをそこから生成する。
`jsonschema==4.23.0` をbackend依存へ追加した。任意JSON patch、SQL、physical geometryのモデル操作は追加しない。
API・DB・OCR読取側の大きな仕様変更は追加していない。

参照した公式仕様：
[Decisions](https://developers.openai.com/api/reference/resources/decisions/methods/create)、
[Function calling](https://developers.openai.com/api/docs/guides/function-calling)、
[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)。

## 判断設定の限定校正

policyは `decisions-first-v2`。フェーズ1の0.98／0.90では、正解だった明示的な行列指定も質問になった。
そのため、正式名称の完全一致が構造的に一つ、又は明示した行・列番号で一つの場合だけ、
確率0.85・confidence 0.80を開始値にする。次点との差0.20は維持する。
近似名称には従来の0.98・confidence 0.90を維持し、モデルscoreだけで同名の表・列を選ばない。

正解付き3要求・12節の小規模サンプルを保存した。明示的な7節は新policyの閾値を通過する。
「名古」の候補選択は名号だったが確信が低いため質問になる。「目以後う」「5」「英語」は保存を許可しない。
このサンプルで実音声の精度や一般的な誤保存率を証明したとは扱わない。STTヒント・実マイクの校正は後続。

ResponsesのOCR値編集はreasoning=noneを維持する。
テンプレート意味付けはnoneで条件付き解除の対象種類を誤ったため、同じ指示でlowを比較した。
lowで意味作成＋正しい接続が成功し、今回の遅延は増えなかった。意味付けだけlowを既定とする。
これはこの工程で予定した判断設定の調整であり、モデルの自動再試行や毎回の承認は追加しない。

## 実際に確認した結果

証拠：`D:/llm-ocr-archive/voice-assistant/20261008-redesign-decisions-phase3`。
実行ごとに保存済み試験DBの新しいコピーをmigrationし、実ASGI HTTP・SQLite・既存storageで確認した。
ブラウザーの描画や録音を含む計測ではない。

| 入力・検証 | 結果 |
| --- | --- |
| 4節の体重500、価格六万、数字列00789、引用6万kg | Decisions 1回、API受付から保存状態取得まで892ms。4欄を一revisionで保存 |
| 名号をゼロゼロ七八九へ | Decisions 1回、2327ms。00789を保存 |
| 500→やっぱり501、別行の価格を六万へ | Responses 1回、4784ms。501／60000を一群で保存 |
| 受付番号に受理コードの意味を作成し接続、既存接続があれば解除 | Responses low 1回、4881ms。既存接続なしを扱い、2操作で保存 |
| 最初のDecisions接続試験 | 3秒上限でtimeout。予約1回を消費し、再試行・保存なし |
| `verify_planner.py` | 65条件成功。故障注入、値対応、候補・Evidence・scope・予算、実応答再生、意味付けと物理セル保持 |
| `phase2_regression.py` | 共通command APIの54条件成功。取消・競合・再送・原子的保存・undo・SSE等 |
| `value_boundaries.py` | 14条件成功。引用6万、単位付き値、先頭ゼロ、部分数値の拒否、名前と数値の分離 |
| `validate_contracts.py` | 7 Schema／20設計例／22操作と層の依存方向を確認。旧操作Schema同一 |
| Ruff | 対象ファイル成功 |
| Mypy | `--follow-imports silent`、対象25 source成功 |

65条件の解析応答は制御用差し替え又は保存実応答の再生であり、65回の実AI試験ではない。
実APIの初期過剰質問、不正な意味付け案の拒否、校正前後の結果を別JSONに保存した。
補助検証で既に同じ値だったためAPPLIEDを期待して失敗した記録も保持し、
変更前状態を明示的に設定して再実行した。製品側のNO_ACTION判定を変更していない。

主な検証コマンドはbackendで実行した次のもの。

```text
.venv/Scripts/python.exe D:/llm-ocr-archive/voice-assistant/20261008-redesign-decisions-phase3/verify_planner.py
.venv/Scripts/python.exe D:/llm-ocr-archive/voice-assistant/20261008-redesign-decisions-phase3/phase2_regression.py
.venv/Scripts/python.exe D:/llm-ocr-archive/voice-assistant/20261008-redesign-decisions-phase3/validate_contracts.py
.venv/Scripts/python.exe -m ruff check app/editing/domain app/editing/application app/editing/infrastructure app/api/v2/editing.py app/assistant/spoken_numbers.py scripts/generate_editing_v2_contracts.py
.venv/Scripts/python.exe -m mypy --follow-imports silent app/editing/domain app/editing/application app/editing/infrastructure app/api/v2/editing.py app/assistant/spoken_numbers.py
git diff --check
```

## 保持範囲と残作業

既存OCR contractを維持した。OCR provider／認識prompt／mapping／association／crop／受付snapshot／出力bindingを変更していない。
通常DB31表の差分は承認済みAPI予約の `api_call_ledger` だけ。通常の業務表、855業務ファイル、
従来試験DB36表、Git HEADを保持した。新migration・feature flag・起動中serverの切替は行っていない。
この工程の変更一覧と開始時との差分は `phase3-only.diff`／`phase3-report.json` に保存する。

実APIは13回使用（Decisions 8、Responses 5。timeout 1を含む）。開始137、終了150/196、残46。
TTSは無効のまま。一般的なp95、音声終了から画面反映までの速度、実マイク精度は未確認。
全22操作の実モデル・実保存網羅、backend/frontend全件、paged・XLSX・ブラウザーはフェーズ7の残作業。
frontendの実装は変更せず、今回build・画面確認は行っていない。
過去の全件回帰にある23失敗／34収集エラーを今回解消したとは扱わない。
短いコピー検証だけではOCR安全性を完全には検証できていない。

次はフェーズ4、GPT-6.1-Sol / high。
180秒の指示全体を組み立てる音声取得、質問と元の値の保持、回答の再束縛、連続訂正、局所録音を接続する。
resolverは質問・private partial plan・対象差替えの解釈結果を返せるが、保留storeと対象差替え実行は未接続。
現在の `retarget_previous` は保存前に明示拒否する。これを黙って通常の値変更へ縮退させない。
実マイクは利用者参加のフェーズ7。ここでモデル切替を待つ。
