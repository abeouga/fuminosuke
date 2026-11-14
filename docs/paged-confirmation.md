# 全ページ確定・再編集と文書snapshot — 2026-09-13

本番OCR/有効化の要求に対する継続工程。全ページ確定・再編集の保存契約を実装したが、
後続の[全ページXLSX出力](paged-output.md)工程で結果取得を追加した。
書類一覧統合・本番qualification・ACTIVE化はまだ完了していない。

## 原因と変更境界

1. `JobPageService.accept`の初期bundleはページ別OCR設定だけを持ち、見出し・繰り返し構造、
   承認済み汎用定義、XLSX出力先と原本のjob専用コピーを保持していなかった。
   確定後に現行版や元準備jobへ戻って出力先を再構築すると、受付時の対応を保証できない。
2. `PagedReviewService`は補正保存までで、全ページの確定snapshot、再編集、同時要求の状態管理がなかった。
   フロントエンドだけのconfirmedフラグでは永続化・競合・再取得を保証できない。
3. `LedgerDocumentPage`にはjob_id単位のunique制約があり、既存書類一覧とXLSX出力は単一結果を前提とする。
   この工程ではそこへPage 1を代入しない。後続の明示的な統合が必要。

## 新しい受付形式

新規paged jobだけ`jobPages.schemaVersion=2`。`documentSnapshot`に以下をdeep copyする。

- Template/Version/mode、canonical config、layout全体、reviewStructure、logicalFieldIds。
- layout全体には見出し・仮想見出し・繰り返し・結合・多角形等の保存metadataを保持。
  現行版から過去jobへ補充しない。canonical geometryをpage-local cropへ戻さない。
- 承認状態に結び付くTemplateDefinitionRevisionの正確な定義。承認済みrow欠落は拒否。
- 既存`SpreadsheetTemplateOutputService.snapshot`で検証した元sheet・原本identity・セル・結合。
- `outputBindings`: page ID、mapping ID、targetPath、明示cell ID、sourceRange/sourceParts、anchor cell。
  ラベル・配列index・pixel geometryによる出力先fallbackなし。ページ範囲外・欠落・重複を拒否。
- SHA確認した原本のjob専用copyと`acceptedSourceWorkbook` artifact。全体bundle digestにも含む。

この工程は原本を再保存・加工しない。元ファイルと異なる所有コピーを保存し、受付後はそのpath/SHAを検証する。
実行時は現在のTemplateVersionや元作成jobを読み直さない。schemaVersion 1は元形式の実行を維持するが、
出力snapshotの後付けを禁止し、新確定APIでは`PAGE_OUTPUT_SNAPSHOT_REQUIRED`で拒否する。

## 確定・再編集

- `POST /api/v1/ocr-jobs/{jobId}/page-review/confirm`:
  `baseRevision`、全ページの`reviewedPageIds`、`documentName`を要求。
- `POST .../page-review/reopen`: `baseRevision`を要求。
- `PagedConfirmation`はjob ID＋確定revisionを主キーとする追記snapshot。
  全ページ値、文書名、受付SHA、確定時刻を保存し、過去の確定値を上書きしない。
- 全ページの値/画像品質を既存validatorで検査。errorが残る場合は409。
  認識のraw/normalized/statusを確定によって自動変更しない。全ページ確認は明示操作。
- SQLite BEGIN IMMEDIATE内でbaseRevisionを検査し、確定snapshot・履歴・job CONFIRMEDを一括commit。
  reopenは確認済みsnapshotの一致を要求し、現在値と過去snapshotを維持してREVIEW_REQUIREDへ戻す。
- **新paged経路では状態変更もrevisionに含む。** confirm/reopenごとに増分するため、
  reopen後に古い確定要求が届いても現在の確認として採用されない。legacyのreopenは変更しない。
- provider/model/prompt/schema/association、crop生成、旧単一結果・既存出力処理は変更していない。
  意図的な新paged保存/確定契約の拡張であり、本番gateの解除ではない。

## UIと失敗復旧

各ページの保存後に明示確認checkboxを操作し、全ページ確認と書類名入力で確定可能になる。
項目の追加編集はそのページの確認を解除する。自動保存やページ表示だけで確定しない。
補正保存中の追加編集とページ切替は維持。明示確定/再編集の通信中と成否不明時は編集を止め、
crop閲覧・ページ移動を維持する。成否不明時は明示再取得でサーバーのconfirmedへ追従する。
他画面の確定と競合した下書きは保持するが、確定済み値としては表示しない。
明示reopen後に下書きを戻し、再保存・再確認を要求する。

この初期工程では`outputAvailable=false`を維持した。後続の専用出力では対応adapterを持つ
全ページ確定済みの結果だけtrueにする。書類一覧登録の成功を確定応答に偽装しない。
ローカル確定は外部出力と独立している。Google同期はこの新経路では未接続。

## 変更ファイル

- backend新規: `services/paged_document_snapshot.py`、`alembic/versions/0033_paged_confirmation.py`。
- backend更新: `services/job_page_service.py`、`services/paged_review_service.py`、
  `domain/paged_review.py`、`db/models.py`、`api/v1/routes/job_pages.py`。
- frontend: `pages/PagedReviewPage.tsx`/test、`pagedReview.ts`、`api.ts`。
- tests: `unit/test_paged_output_bindings.py`、`integration/test_paged_document_snapshot.py`、
  `integration/test_paged_confirmation.py`、`integration/test_paged_confirmation_migration.py`、
  migration head/tableの既存期待値（0033への明示拡張）。
- AGENTS、OCR Safety Contract、ページレビュー/複数ページ/本番化文書。

## 検証記録

- 変更前: rootから`backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、210 passed（39.84秒）。
- 最初の受付変更後: `tests/integration/test_job_pages.py`、10 passed（178.27秒）。
- 出力binding単体8件、元原本/版変更・所有copy改変・旧snapshot互換の実結合試験1件が通過。
- 確定/reopen・履歴・rollback・同時補正とmigration既存試験: 10 passed（56.02秒）。
- 0032→0033→0032→0033の既存paged値保持: 1 passed（0.49秒）。実DBのdowngradeは行わない。
- frontend全体33 files / 359 passed（13.96秒）、build成功。500 kB超chunk警告は維持。
  全ページ明示確認、確認解除、確定後crop、再編集、応答消失、競合下書き復元を追加した。
- 変更backend 5モジュールの限定mypyと対象Python 11ファイルのRuffが成功。
- backend full: `pytest tests -q -o addopts='' -p no:cacheprovider --basetemp=.pt-paged-confirm-full2-0913`、
  944 passed、既存1 skipped、既存3 warnings（1080.08秒）。
- 変更後: rootから`backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、
  210 passed、既存1 warning（66.59秒）。変更前と同じ対象を実行。
- 最初のfullは同名test moduleによる収集エラー。unitを`test_paged_output_bindings.py`へ分離し、
  製品のassertion/拒否条件を弱めず新しいbasetempで再実行し、上記全体試験が成功。

## 実DB反映と実稼働の限界

処理中job 0件と0032を確認し、8000の既知uvicorn PID 38792を停止した。
開発launcherの通常終了処理で5173も停止し、両portの空きを確認した。
`backend/data/backups/pre-paged-confirmation-20260913.db`へSQLite backupを作成後、
`alembic upgrade 0033_paged_confirmation`相当を実行し、正常終了を確認した。

既存33テーブルについて、変更前に存在した列の全行を比較した。
変更前＝backup＝変更後、集約SHA256は
`e5ce89d6c4a2f34c7258974f27506ca006359345f6152d963814e18447056ef6`。
外部キー違反0、新確定row 0。対象版`6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`はDRAFT・4ページを維持。
旧結果の補正や実paged test jobの挿入は行っていない。

その後の`Start-Process`による開発server再起動要求は実行ポリシーで拒否された。
拒否を迂回する起動は行わず、新APIの実稼働・ブラウザー反映は未検証。
開発serverを通常の起動手順で再起動する必要がある。

最終`git diff --check`成功。

実OpenAI/Google API呼出し0回。
実応答association/recognition error、実書類の確認→出力、ブラウザー操作は未測定・未検証。
本番資格・ACTIVE化は閉じたままであり、OCR安全性を完全には検証できていない。
