# 汎用の行・列音声指定：フェーズ1

後続の[フェーズ2レビュー](voice-record-cell-phase2-2026-10-08.md)で、表・値・欄名の誤解決を修正しています。
以下はフェーズ1終了時点の記録です。

2026-10-08。計画上の担当は GPT-6.1-Sol / xhigh。実装と短いコピー検証を完了し、
次は Astra / high の独立レビューです。実音声受け入れ、全体回帰、通常切替は未完了です。
作業対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。元の `llm-ocr` は変更していません。

## 原因と変更境界

変更前のコピーAPIで「一行目の体重を500にして」が `CLARIFYING` となり、値が保存されないことを再現しました。
音声検索がアラビア数字の行指定しか認めず、対象catalogにも表・列の識別情報がありませんでした。
また、保存された `row.order` と画面の `rowIndex + 1` は異なる場合があります。

新しいコードは受付時の `reviewStructure` の行・列配列から検索用情報だけを投影します。
特定の表名・列名・field IDを製品コードに固定していません。
OCR provider、prompt、mapping、crop、受付snapshot生成、補正値モデル、出力コードは変更していません。
値の保存は既存 `ocr.set_value`、revision、receipt、undo の経路を使用します。
バックエンドでの投影・解決・保存transaction内の文脈管理が必要で、表示側だけでは実現できません。

## 実装した仕様

- 「一行目の体重」「2行目の価格」「1行目の3列目」のような任意の行＋列指定。
- 行・列番号は1始まりの表示順。見出し行と行番号欄を除き、空行・未割当列を含めます。
  保存済みの疎な `order` で番号を付け直しません。テンプレート編集の `order` は従来どおりです。
- 漢数字、全角数字、一般的なかな・カタカナの番号。負数、0、分数、不対応の大きな番号の部分一致は拒否します。
  変更後の値に現れる「一行目」は対象座標へ流用しません。
- 同名の表は画面と同じ「表名 1」「表名 2」で提示します。共通の元表名だけでは候補を勝手に選びません。
- 表名省略時は同じ連続音声sessionで最後に実際に保存した一表を優先します。
  その表に指定欄がない場合は別表へ移らず、不足情報を質問します。
  引き継ぎがない場合は全表から一意な行・列を探します。
- 聞き返し中は同session・同binding・60秒のメモリに元指示と値を保持します。
  表名だけ、列名だけ、行番号だけ、列番号だけの返答で続行できます。繰り返し質問でも値を保持します。
- 保存後は正式receiptから「表名のN行目の列名を値に変更しました。」を表示します。
  承認ボタンや読み上げは追加していません。既存の連続取得・局所録音・手動操作を維持します。
- モデルが検索条件を省略しても発話の条件を補います。条件に反する検索や可視handleは保存を拒否します。
  複数座標の一部だけを解決した群も保存しません。
- 取り消し、複数表への実保存、手動保存、選択/context更新、外部revision、音声session終了で表の引き継ぎを解除します。
  新sessionは他sessionや資源全体の履歴から表を継承しません。失敗・未保存・`NO_CHANGE` は引き継ぎを更新しません。
- 局所録音は選択範囲を維持し、連続音声用の表文脈を生成しません。

対象は音声編集に接続済みの単一画像OCRレビューです。paged結果の音声接続や、
テンプレートの行構造変更を追加したものではありません。

## APIと変更ファイル

`TargetSearch` に `column`、`tableId`、`tableName` を追加しました。
`TargetInfo.recordCell` は表・行・列の保存identityと表示番号・名称を返し、
`ContextResult.recentTable` はサーバーが保存成功から確定した表を返します。
音声ツールでは明示された表名に `tableName` を使用します。
元の表名による検索が複数表に一致する場合も候補をすべて返します。
HTTP/tool schemaとTypeScriptはPydantic契約から生成しました。
新しいDBテーブル・migrationはありません。既存 `EditingSession.context_json` を使用します。

| ファイル | 変更 |
| --- | --- |
| `backend/app/editing/record_cells.py` | 受付構造の投影、一意性検査、共通の位置フィルター・通知名 |
| `backend/app/editing/contracts.py` | 型付き検索条件と応答metadata |
| `backend/app/editing/catalog.py` | 表・表示行・列の投影とcursor binding |
| `backend/app/editing/service.py` | transaction内の直前表、解除、正式保存通知 |
| `backend/app/editing/schemas.py` | 共通schemaからのツール生成 |
| `backend/app/assistant/record_selection.py` | 汎用の番号・座標解釈、聞き返しの補完 |
| `backend/app/assistant/target_resolution.py` | 検索・近似・直接handleへの座標拘束 |
| `backend/app/assistant/conversation.py` | 対象質問と元値保持の接続 |
| `backend/app/assistant/conversation_provider.py` | 行・列指定のツール使用指示 |
| `frontend/src/editing/generated.ts` | 自動生成型の更新 |

## 検証と保存成果物

成果物は `D:\llm-ocr-archive\voice-assistant\20261008-record-cell-phase1`。
開始時の1008ソース/文書、通常DB、855業務ファイル、status/diffを退避しました。
検証スクリプトはすべてその外部フォルダーにあり、モデル応答だけを制御し、実FastAPIと隔離コピーSQLiteを使用しています。
これは実OpenAIモデルの解釈性能や実マイク試験の証明ではありません。

| 検証 | 結果 |
| --- | --- |
| `baseline_record.py` | 変更前の保存不成立を再現、保存なし |
| `verify_records.py` | 20項目通過（コピーDB移行照合1項目を含む） |
| `verify_record_boundaries.py` | 19項目通過（コピーDB移行照合1項目を含む） |
| `verify_conversation.py` | 既存22項目通過（コピーDB移行照合1項目を含む） |
| `verify_clarification.py` | 既存4項目通過（コピーDB移行照合1項目を含む） |
| `verify_ocr_replay.py` | 保存済み72欄、290画像SHA、providerの25ケース比較通過、外部API 0回 |
| 対象9 Pythonファイルの `ruff check` | 通過 |
| 同9ファイルの `mypy --follow-imports=silent` | 通過。無関係な既存領域の完全型検証ではありません |
| `python -m app.editing.schemas --output <成果物>/schemas` | 生成TSとソース内TSの一致 |
| `npm run build -- --outDir <成果物>/frontend-dist` | 通過 |
| `audit.py` / `git diff --check` | 既存差分・通常31テーブル・855業務ファイル・保護OCR8ソースを保持 |

各 `runs/<時刻>/results.json`、ログ、`baseline.json`、`before.diff`、`phase1-only.diff`、
`pipeline-replay.json`、`report.json` に証拠を保存します。
OCR replayの過去の日付正規化差1件は既存の汎用変換仕様として分離され、今回の回帰ではありません。
通常DBは移行せず、新編集APIの通常無効設定を維持しました。通常データへの補正はありません。
既存OCR contractを維持したことは上記の所有・画像・応答・保存照合の範囲で確認しました。

## 後続フェーズとAPI枠

フェーズ2は Astra / high で、表の一意性、番号解釈、曖昧性、古い文脈、同名列の通知、
複数座標と値の対応、実画面との整合を独立レビューします。
フェーズ3は GPT-6.1-Sol / high で、必要な全体回帰、実ブラウザー、XLSX出力対応、
利用者参加の実マイクを確認します。以前の全体検証にある既知失敗を解決済みとは扱いません。
このフェーズではサーバーの起動・停止・再起動をしていません。実行中の試験サーバーへの反映は未確認です。

利用者の追加許可は「APIはあと100回」。台帳96回を起点に累計196回を上限とし、
今回の追加消費は0回、残り100回です。旧記録の追加20回の許可待ちはこの追加許可で置き換えます。
重い全体unit/E2Eと実マイクは本フェーズでは実行していません。
