# 複数ページOCRの文書全体API予算

2026-09-13。本番化の実行制御であり、認識方式や本番資格の解除ではない。

## 原因と契約変更

従来の`OpenAIOcrProvider.extract`は呼出しごとに計数を0へ戻す。
`PagedOcrExecution`がページごとに呼ぶため、上限3回・2ページ・各61項目のfixtureで
3回+3回=6回まで進むことを外部通信なしで再現した。
ページ別に成功応答の回数を足すだけでは、失敗・並行batch・再起動時の過剰送信を防げない。

新規OpenAI paged受付は`jobPages.ocrCallBudget`へ
`adapter=paged-ocr-job-budget-v1 / scope=job / maxCalls`を固定し、同じ受付transactionで
`PagedOcrBudget`を作る。上限は受付時の`executionConfig.ocr.max_calls_per_job`と一致させる。
mock/disabled、旧単一ページの予算挙動は変更しない。
古いpaged受付へ予算を自動追加せず、必要な記録がないOpenAI実行は送信前に拒否する。

## 予約・失敗・再試行

- 文書単位の上限は全page・全attemptで共有する。再試行や再起動でリセットしない。
- `PagedCallBudget.reserve`はSQLite `BEGIN IMMEDIATE`内でjobのPROCESSING状態、
  cancel、attempt、page所有権、受付SHA、予算と呼出し台帳の整合を検証する。
  `used_calls`増分と`PagedOcrCall`追加をcommitしてからprovider送信へ進む。
- 予約番号は文書全体で一意。台帳にはpage ID・attempt・予約時刻を保持し、
  provider diagnosticsのcall番号へ渡す。成功responseの`apiCallCount`は当該ページの計数を維持する。
- 失敗・応答消失・予約後cancelを理由に予約を払い戻さない。
  予約済みは「実際に送信した」と同義ではなく、送信の可能性を含めて保守的に消費した枠である。
  commit応答が不明なときは送信せず、commit済みなら使用回数を残す。
- 読み取りも明示的なread transactionを用い、並行予約でcounterと台帳の異なる時点を比較しない。
  閲覧時の残数が古くても、実予約はwrite transaction内で再検証する。
- 上限到達は明示エラーで停止する。現行設定の増額を受付済みjobへ黙って適用しない。
  新しい上限で実行する場合は別の明示的な受付が必要である。

backend serviceが保存を所有し、providerへは`OcrCallBudget` protocolを渡す。
request/response計算やproviderにSQLAlchemy・repositoryの直接依存を追加しない。

## 全ページの最低枠

既存batch方式（通常項目は最大30件、識別項目は各1件）と同じ式で、未完了全ページの
設定済み全項目を送る場合の必要枠を事前計算する。成功ページは保存SHAとページsnapshotの一致を検証して再利用し、
再送の最低枠に含めない。不足時は画像抽出・最初のprovider呼出しより前に拒否する。

これは入力画像に依存するローカル解決を行う前の予定枠であり、実送信数の下限ではない。
従来の空白行等のローカル解決で送信対象が減る場合も、予定全項目分の予算を要求する。
ローカル解決を停止したり、空白項目を追加送信したりして計算値へ合わせない。

各ページの実行中は、後続未完了ページの最低枠を保護する。
先頭ページのoptional high-detail再読取やfallbackが後続の最低枠を使い切らない。
ネットワーク失敗を含め全ページの成功を保証するものではなく、有限予算内で安全に停止する方式である。
認識値・判定、画像/crop、mapping/sourceRegionId/targetPath、association token、prompt/model/schema、
既存の単一ページretry方式は変更しない。意図的な新paged実行予算契約の拡張である。

## 変更ファイル

- `backend/app/services/paged_ocr_budget.py`、`db/models.py`、`alembic/versions/0034_paged_ocr_budget.py`
- `services/job_page_service.py`、`services/paged_ocr_execution.py`、`services/ocr_workflow_service.py`
- `ocr/base.py`、`ocr/openai_provider.py`
- `tests/integration/test_paged_call_budget.py`、`test_paged_budget_migration.py`、既存migrationのhead/table期待値
- AGENTS、OCR Safety Contract、複数ページ本番化の記録

上記backend内の省略パスは`backend/app`または`backend/tests`基準。frontendの変更はない。

## 検証

- 変更前OCR契約: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、210 passed（39.29秒）。
- 変更後の同コマンド: 210 passed / 1 warning（52.46秒）。既存contractを維持した。
- 新規予算・migrationと既存provider予算: 42 passed（45.73秒）。
  `pytest tests/integration/test_paged_call_budget.py tests/integration/test_paged_budget_migration.py
  tests/unit/test_openai_call_budget.py -q -o addopts='' -p no:cacheprovider
  --basetemp=.pt-paged-budget-api3-0913`（backend venv）。
- 上限超過、同時12予約、別connection/新attempt、取消・状態・所属・SHA・台帳不整合、
  commit前/後の応答消失、予約後cancel、読み取り途中の別connection予約、後続最低枠保護を固定。
- 実DB fixtureの新規受付→ページ別workflow→全ページレビュー公開、
  requestのmapping ID/ページ所有権、受付snapshot不変を検証。実OpenAI clientは生成しない。
- 追加offline wire比較では、同じ画像・mappingを予算なし/ありで実request組立てと
  固定parse responseへ通した。送信引数（画像/prompt/schemaを含む）とmapped documentは完全一致。
  responseの当該ページ使用回数は両方1、diagnostics call番号は1/5で文書全体の番号だけ異なる。
  実API呼出し0。実帳票の認識精度比較や全Golden replayではない。
- migrationは0033→0034→0033→0034で既存job値の不変と予算非後付けを確認した。
- 初期fixtureのSettingsのコピー方法を修正した。また、統合試験で状態名の誤りを検出し、
  新予算側のRUNNINGを既存正本のPROCESSINGへ訂正した。既存状態制約を緩めていない。
- 対象7 productモジュールの限定mypyは成功。Ruff/全体回帰の最終結果は追記する。
- 予算工程の対象11 PythonファイルのRuffも成功。
- 実DRAFT版の4ページ151項目をread-onlyで再投影し、予定呼出し数3/2/2/1、合計8を確認。
  現在の設定上限も8であり全項目の予定枠は足りるが、その場合の再試行余力は0。
  上限設定・原本・版configを変更せず、新規受付/有効化も行っていない。

最初の全体試験は1006 passed / 1 failed / 1 skipped（1271.60秒）。
失敗は既存のfont SHA cacheによる失効検査だったため、
[依存SHAの再検証](renderer-identity-freshness.md)として原因と修正を分離した。
旧試験を弱めず、追加した同一metadata再現試験と全体回帰を再実行する。
再試験`.pt-paged-budget-full2-0913`は、後続resource対象試験との並行実行を解消するため中断した。
完走結果として数えず、[後続工程](paged-transcription-resources.md)で直列の全体回帰を行う。

## 実DB適用

実アプリ8000/5173のlistener 0件、処理中job 0件、DB0033を確認した。
`backend/data/backups/pre-paged-budget-20260913.db`へSQLite backupを作成してから0034へupgradeした。
既存34テーブルの全列・全行を比較し、変更前=backup=変更後。
集約SHAは`5fe04621f34cf51cdd7f21f70b72ceeb9542274cfca950995d748bc7b3dd416d`。
外部キー違反0、新予算row 0・新予約row 0。原本・版・過去job snapshotを更新していない。
実DBのdowngrade・バックアップ上書きは行っていない。

## 残る本番条件

本番資格・ACTIVE/受付gateは維持する。prompt/schema実体・実行resourceの固定、
WSL font/runtime/geometry/実手書きcropの認定、実OCR・稼働UIによる有効化は未完了。
実OpenAI/Google呼出しは0回。実認識誤り・実association誤対応は未測定であり0件と主張しない。
frontendは変更しておらず、この工程では再ビルド対象外。実アプリは停止中で、Chrome実操作は未検証。
後続でprompt本文と応答model schemaの保存を追加した。実行依存全体の固定は未完了。
