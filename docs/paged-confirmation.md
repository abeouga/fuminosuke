# 全ページ確定・再編集と文書snapshot

複数ページの読み取り結果を、全ページまとめて確定・再編集する保存契約と、そのために受付時に固定する文書snapshotの仕様。
補正は[複数ページ結果のレビュー・補正](paged-review.md)、確定後の取得は[全ページXLSX出力](paged-output.md)を参照する。

## 必要な理由

1. ページ別OCR設定だけの受付bundleには、見出し・繰り返し構造、承認済み汎用定義、
   XLSX出力先と原本のjob専用コピーが入らない。確定後に現行版や元準備jobへ戻って出力先を再構築すると、
   受付時の対応を保証できない。そのため受付時にこれらを文書snapshotへ固定する。
2. 全ページの確定snapshot、再編集、同時要求の状態管理はサーバーに持つ。
   フロントエンドだけのconfirmedフラグでは永続化・競合・再取得を保証できない。
3. `LedgerDocumentPage`にはjob_id単位のunique制約があり、既存書類一覧とXLSX出力は単一結果を前提とする。
   複数ページの結果をそこへPage 1として代入しない。

## 受付形式

新規paged jobは`jobPages.schemaVersion=2`。`documentSnapshot`に以下をdeep copyする。

- Template/Version/mode、canonical config、layout全体、reviewStructure、logicalFieldIds。
- layout全体には見出し・仮想見出し・繰り返し・結合・多角形等の保存metadataを保持。
  現行版から過去jobへ補充しない。canonical geometryをpage-local cropへ戻さない。
- 承認状態に結び付くTemplateDefinitionRevisionの正確な定義。承認済みrow欠落は拒否。
- 既存`SpreadsheetTemplateOutputService.snapshot`で検証した元sheet・原本identity・セル・結合。
- `outputBindings`: page ID、mapping ID、targetPath、明示cell ID、sourceRange/sourceParts、anchor cell。
  ラベル・配列index・pixel geometryによる出力先fallbackなし。ページ範囲外・欠落・重複を拒否。
- SHA確認した原本のjob専用copyと`acceptedSourceWorkbook` artifact。全体bundle digestにも含む。

受付は原本を再保存・加工しない。元ファイルと異なる所有コピーを保存し、受付後はそのpath/SHAを検証する。
実行時は現在のTemplateVersionや元作成jobを読み直さない。schemaVersion 1は元形式の実行を維持するが、
出力snapshotの後付けを禁止し、確定APIでは`PAGE_OUTPUT_SNAPSHOT_REQUIRED`で拒否する。

## 確定・再編集

- `POST /api/v1/ocr-jobs/{jobId}/page-review/confirm`:
  `baseRevision`、全ページの`reviewedPageIds`、`documentName`を要求。
- `POST .../page-review/reopen`: `baseRevision`を要求。
- `PagedConfirmation`（表`paged_confirmations`）はjob ID＋確定revisionを主キーとする追記snapshot。
  全ページ値、文書名、受付SHA、確定時刻を保存し、過去の確定値を上書きしない。
- 全ページの値/画像品質を既存validatorで検査。errorが残る場合は409。
  認識のraw/normalized/statusを確定によって自動変更しない。全ページ確認は明示操作。
- 1つのDB transaction内でbaseRevisionを検査し、確定snapshot・履歴・job CONFIRMEDを一括commit。
  reopenは確認済みsnapshotの一致を要求し、現在値と過去snapshotを維持してREVIEW_REQUIREDへ戻す。
- **paged経路では状態変更もrevisionに含む。** confirm/reopenごとに増分するため、
  reopen後に古い確定要求が届いても現在の確認として採用されない。legacyのreopenは変えない。
- provider/model/prompt/schema/association、crop生成、旧単一結果・既存出力処理は変えない。
  pagedの保存/確定契約の拡張であり、本番gateの解除ではない。

## UIと失敗復旧

各ページの保存後に明示確認checkboxを操作し、全ページ確認と書類名入力で確定可能になる。
項目の追加編集はそのページの確認を解除する。自動保存やページ表示だけで確定しない。
補正保存中の追加編集とページ切替は維持。明示確定/再編集の通信中と成否不明時は編集を止め、
crop閲覧・ページ移動を維持する。成否不明時は明示再取得でサーバーのconfirmedへ追従する。
他画面の確定と競合した下書きは保持するが、確定済み値としては表示しない。
明示reopen後に下書きを戻し、再保存・再確認を要求する。

出力は、対応adapterを持つ全ページ確定済みの結果だけ`outputAvailable=true`にする。
書類一覧登録の成功を確定応答に偽装しない。確定はローカルで完結し、外部出力とは独立している。

## 実装の場所

- backend: `app/services/paged_document_snapshot.py`、`app/services/job_page_service.py`、
  `app/services/paged_review_service.py`、`app/domain/paged_review.py`、`app/db/models.py`、
  `app/api/v1/routes/job_pages.py`
- frontend: `src/pages/PagedReviewPage.tsx`、`src/pagedReview.ts`、`src/api.ts`
