# 互換廃止・汎用変換記録 — 2026-10-04

## 対象と結論

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。元の `llm-ocr` は編集していません。
明示された破壊的変更の許可に基づき、旧業種schema・旧API・旧DB構造の実行時互換を廃止しました。
既存データは停止中に一度だけ変換しました。現在は汎用形式で起動しています。

| 項目 | 現在の仕様 |
| --- | --- |
| OCR結果 | `schemaVersion: "2.0"`、`fields`、`fieldLabels` |
| 保存先 | `fields.<key>`。旧階層pathは拒否 |
| 識別情報 | テンプレートの `documentIdentityFields`。番号・名称・日付・組合せを確認 |
| 補正 | `baseRevision`、`fields`、`reviewItems`。旧payloadと余剰属性は422 |
| 確定 | ローカルtransaction。確定前にXLSX生成と識別条件を確認 |
| 出力 | 確定したXLSXのみ。受付時の原本・セルbindingまたは凍結reviewStructureを使用 |
| DB初期導入 | `generic_schema_v2`。旧migration列は製品外へ退避 |
| テンプレートパッケージ | format/schemaVersion 2。旧package v1は422で拒否 |
| UI / API | `http://127.0.0.1:5180/` / `http://127.0.0.1:8010/` |

## 廃止したもの

- 牛用のDocumentIdentity / Pedigree / Calving / Saleモデル、固定targetPath、種雄牛辞書。
- 業種名や欄名からの10桁必須・年号・単位・前行値の推測。
- 旧OCR schema、単一documentIdentity alias、空の固定項目カタログ、旧CSV出力設定。
- Google認証・Drive / Sheets同期、検索、書類ライブラリ、持ち出しバックアップ、開発情報画面。
- 旧文書統合・RESCAN_PAGE・業種別の行番号によるmerge。
- 旧同期facade、古いJSON Schema、未使用のカメラ・生mapping編集・資格確認UI。
- 廃止した仕様のテストと旧 `verify_ocr_contract.py`。退役理由と原本は製品外に保存。

単一画像とXLSX複数ページは現在も別の入力経路です。geometry・renderer隔離・所有原本・
ページbindingの検証は必要な現行機能として保持し、互換処理の廃止をgate解除に使用していません。

## 保持したデータと対応関係

旧pathから新pathへの対応は145件。候補・補正・revision履歴・確定snapshotの12件を照合しました。
2テンプレート、3版、5job、2結果、5件のrevision履歴、1件の確定結果を保持しています。
未設定で空の業種モデル初期値だけを除き、保存済みの手入力・不確実な候補は保持しました。

確認用job `65d54edf-71a5-4d00-bfa1-434782e7f6ee` は以下を保持しています。

- revision 3、confirmed=true、75項目、72欄のcrop binding。
- mappingId、sourceRegionId、boundingBox、crop画像、列・行ID、警告根拠。
- 候補・補正・確定値、status、alternatives、reviewReason、手入力項目。
- 設定した7列10行の明細表と独立項目。帳票のラベル・名称は利用者データとして保持。

設定・mappingのJSON変更に必要なSHAを更新しました。画像内容は変更していません。
既存の保存値を新しいOCRで再構築していません。

### 意図した非互換の挙動

新規OCRでは `26 9 27` を生年月日と見なして `2014年9月27日` へ推測変換しません。
保存済みの `2014年9月27日` は保持しました。同じ保存responseの再計算では、72欄のうち
71欄が完全一致し、1欄だけこの正規化仕様変更が発生しました。raw、status、所有欄、cropは一致しています。

旧ブラウザー下書きを新形式へ混ぜないため、編集下書きの保存keyをv2用へ分離しました。
旧cacheの実行時変換はありません。サーバーへ保存済みの版・補正値は移行対象として保持しています。

## 修正した欠落

1. 旧補正形式の余剰属性が無視されて受理されていました。入力schemaを厳格化し422で拒否します。
2. 設定済みcropが欠落しても送信対象から黙って除かれていました。provider呼出し前に409で停止します。
3. 列の編成で既存の保存先を再生成する処理が残っていました。行・列の編成時もrelation.targetPathと欄IDを保持します。
4. 配布用テンプレート書き出しで、除去した版のfield identityが残り外部キー違反になりました。版に属するメタデータを一緒に整理し、再検証しました。
5. 削除済み画面のprops・API型・Google起動設定が残っていました。呼出し元、型、起動設定を整理しました。

## 表示と配信の変更

- 帳票全体を大きく表示し、セル選択時のcropをその上に表示します。表・行・列の文脈を併記します。
- 確定後も値とcropを閲覧でき、無効な編集・行移動ボタンと作業用の警告表示は終了します。
- 読み取り前の識別確認と、保存・revision競合・確定後の再取得による復旧を維持します。
- 内部targetPathを操作ラベルへ露出せず、欄名・値・操作内容を読み上げます。
- 旧ライブラリの検索・自動統合を示す説明を、書類を見分ける情報の確認へ変更しました。
- 画面を遅延読込にし、初回JavaScriptを532.45 kBから230.44 kBへ削減しました（約57%）。
  全画面分のコードは必要時に読み込みます。総コード量が同じ割合で減ったという意味ではありません。

## 実行した最小検証

使い捨てscriptと全成果物は `D:\llm-ocr-archive\generic-cleanup\20261004` に保存しました。
この互換廃止作業で追加した外部OpenAI / Google API呼出しは **0回** です。

| 検証 | 結果・証跡 |
| --- | --- |
| 停止DBの変換照合 | 145 path、12 snapshot。保存値・状態・revisionを保持。`migration-report.json` |
| 保存実response再生 | 72欄、290画像SHA。重複ID拒否、欠落ID警告、不明IDによるずれ防止、欠落cropの送信前拒否。`pipeline-replay.json` |
| 隔離API / SQLite / XLSX | 116確認、75項目・72crop、71個の非空XLSXセル。補正・競合・再編集・確定・未確定出力拒否。`acceptance-report.json` |
| 稼働中APIのXLSX | 最終再起動後に取得。93セル（非空70セル）が表示用データと一致。revision 3・確定状態・75項目・72cropを保持。`live-download-report.json`、`live-result.xlsx` |
| パッケージ・初期導入 | 13確認。現行templateのexport/import、v1拒否、空DBのAlembic導入、実portable exportとhash/FK検証。`package-install-report.json` |
| 配布bootstrap | repository rootで `backend/.venv/Scripts/python.exe scripts/export_portable_templates.py --validate-only` — 成功、1テンプレート。`portable-bootstrap-final.log` |
| frontend | `npm run build` — 成功。最大chunk 230.44 kB、500 kB警告なし。`frontend-delivery-build.log` |
| backend静的検査 | `ruff check app --select F821,F841` と変更対象のF/I検査 — 成功 |
| JSON Schema | `python -m app.template_packages.schema_export --check` — 成功 |
| 起動・停止・再起動 | 実PowerShell入口。停止後の5180/8010待受0・旧PID0、再起動HTTP200、保存結果完全一致。`runtime-lifecycle-report.json` |
| 実画面 | ホーム、読み取り、テンプレート、識別確認、確定結果・crop閲覧、広い表示と狭い表示を確認。`template-identity-final.png`、`review-wide-final.png`、`review-normal-final.png` |
| 差分 | `git diff --check` — 成功。staging / commit / pushなし |

初回検証で見つかった誤り・失敗のログも保存しました。廃止した仕様のテストは
`retirement-manifest.json` に理由を記録しています。有効なテストのfixtureは汎用targetと
現行の種類へ更新し、assertionは弱めていません。そのunitを全件実行したとは扱いません。
最終ビルドで検出した旧資格UIのテスト参照は退役し、現行の印刷ファイル取得テストはAPI・propの名称だけを更新しました。
失敗ログは `frontend-final-reference-failure.log` に残し、修正後のビルド成功を確認しています。

## 検証限界

- 実OpenAIへ新しい帳票を追加送信した精度測定ではありません。保存済み実responseとの比較です。
- 全件unit / E2E、実XLSX複数ページOCR、native rendererの再資格確認、クラウド本番配備は今回未実施です。
- SysOverRayはpublishし直し、実起動scriptでlifecycleを確認しました。今回、オーバーレイのボタンをUI自動操作で再検証していません。
- 画面のXLSXボタンからAPIはHTTP200に到達しました。アプリ内ブラウザーの保存イベントは取得できず、端末への保存完了は未確認です。
  HTTPで保存した実XLSXとその内容は検証済みです。API取得成功とブラウザー保存完了を区別します。
- 任意の帳票形式への無条件対応や文字認識の完全一致を保証する変更ではありません。

## 現行文書と退避先

現行方針は `AGENTS.md`、技術契約は `docs/ocr-safety-contract.md`、製品入口は `README.md`。
旧汎用化記録は、その後の互換廃止が分かる注記を付けました。

退避先：`D:\llm-ocr-archive\generic-cleanup\20261004`

- `source-before.zip`、`app-before.db`、`bootstrap-before.db`
- 変換器、各段階のDB退避、metadata原本、path対応・SHA・照合レポート
- `retired/` と `retirement-manifest.json`
- 保存response再生、隔離API、パッケージ・初期導入、実lifecycleのscriptとログ
- 実XLSX、画面画像、ブラウザー保存イベントの観測記録

変換器・旧DB・使い捨て検証・退役コードを製品の実行経路へ残していません。

## 追補：XLSXテンプレート取得の500修正（2026-10-04）

互換処理の削除時に `LayoutService._with_saved_text_placement` の `@staticmethod` が欠落しました。
XLSXテンプレートでインスタンスから呼ぶと引数が1つ多く渡され、`layout` と `field-mappings` が500になりました。
画像テンプレートを中心とした先の画面確認では、この分岐の欠落を検出していませんでした。

修正はデコレーターの復元1行です。文字配置の照合条件・計算、layout・mapping・crop・受付snapshot・
provider・prompt・model・schema・補正値・revision・確定状態は変更せず、既存OCR contractを維持しました。
破壊的な仕様変更はありません。

- 修正前：対象版 `07d01fe1-6ca1-4d79-8dff-2d844d422d6a` の2 endpointを直接8010と5180 proxyで呼び、4件すべて500を再現。
- 修正後：既存6版×2 endpoint×2接続先の24リクエストすべて200。物理セルID・矩形・役割・文字・relationと保存内容を照合。
- 全テンプレート版のDB内容と確定結果は修正前と一致。実起動入口で再起動し、確定結果も完全一致。
- `ruff check app/services/layout_service.py --select F,I`、クラスメソッドの引数binding静的確認、`npm run build`、`git diff --check` は成功。
- 実画面で「1 給水装置台帳等複写申込書 (2)」の確認を再開し、元Excelの74セルを表示。保存・承認・再OCRは実行していません。
- unit全件・E2E全件・Office再描画・実OCR精度評価は実行せず、外部API呼出しは0回。

修正前ソース、例外ログ、短いAPI検証、各結果、画面画像は
`D:\llm-ocr-archive\fixes\20261004-layout-get-500` に保存しました。
使い捨て検証を製品内へ追加していません。
