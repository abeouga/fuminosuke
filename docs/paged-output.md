# 全ページの記入済みXLSX出力

複数ページの読み取り結果を全ページ確定した後に、その確定値を原本へ書き込んだ記入済みXLSXを取得する専用経路の仕様。
テンプレートカードの印刷用XLSX/PDF（[保存済みTemplateVersionの印刷用XLSX / PDF](template-print-files.md)）とは別の成果物であり、物理印刷は行わない。

## 新しい経路を分ける理由

既存の結果出力は単一OcrResultと準備jobの原本を参照し、openpyxlの全体再保存、
sheet名変更・他sheet除去を含む。ここへPage 1だけを渡すことも、全ページを現在の版から
再構成することも受付時の物理対応を維持できない。そのため経路を分け、単一ページの出力処理は変えない。

`paged_document_snapshot.py`は新規受付に`outputAdapter=ooxml-paged-values-v1`を固定する。
既存schemaVersion 2でadapter未指定の受付はレビュー・確定を維持するが、出力は拒否する。
新adapterの後付け・現在設定へのfallbackはない。将来adapterを変更する場合も明示的な互換設計を要する。

## API・状態・所有権

- `GET /api/v1/ocr-jobs/{jobId}/page-review/output.xlsx?revision=N`。既存認証router配下。
- 指定revisionの全ページ確定状態とPagedConfirmationの値・受付SHA一致を要求する。
  全ページのpage ID、mapping ID、targetPath、sourceRegionId、元cell/range/parts/anchorを照合する。
- 原本は受付jobの所有コピーのみ。path所属とSHAを確認し、読取後のbytesも再照合する。
  現在のTemplateVersion、準備job、画面の位置やラベルは出力先決定に使わない。
- 一貫したread transactionでsnapshotを取得し、出力生成前に解放する。
  生成後も確定状態・revision・受付SHAを再確認し、途中reopenでは古い出力を拒否する。
- MIMEはXLSX、Content-DispositionはUTF-8名、Cache-Controlはno-store。
  X-Result-Revisionと出力SHAのETagを返す。生成結果はその要求へ返すだけで、DB値・履歴を変更しない。
- 失敗で確定を解除しない。書類一覧登録を成功したことにしない。

## 原本保持writer

`paged_workbook.py`は既存の原本保持adapterと同じZIP/XML安全検査を先に行う。
数式・外部参照・active content・未対応拡張は拒否する。openpyxlは読取と既存の結合保護検査だけに使い、
保存serializerには使わない。変更するのは対象worksheetの所有セル値と検証済み結合要素のみ。
未編集セル・style・列幅/行高・原sheet名・非表示管理sheet・他ZIP part・印刷設定を保持する。
新しい出力値が元dimensionを超える場合だけ明示座標でdimensionを拡張する。

- 通常セルは受付cell IDから元XLSX座標を照合。元結合はtop-left anchorだけを許可する。
- 手動結合は既存の連続範囲・selected source・内部値・literal一致・重複保護を再利用する。
  元結合内部の隠れた値を破棄する要求は拒否し、別出力項目が結合内部へ入る場合も拒否する。
- 同じ物理anchorへの値は同一の場合だけ許可し、異なる値・空白との競合は拒否する。
- 値は全て文字列。blank確定は当該入力セルの元placeholderを消す。旧単一出力のblank仕様は変更しない。
- inlineStrにより先頭`=`を数式にしない。先頭ゼロ、XML記号、改行、文字列中の`_xHHHH_`を保持する。
  XML不正文字と32767文字超は拒否する。

文字列形式はMicrosoftの[InlineString仕様](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.inlinestring?view=openxml-3.0.1)と
[ST_Xstringのエスケープ仕様](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oe376/bd0aa042-434a-4ca7-b25f-4e1fd25a954d)に照合した。
内部printLayoutのページ設定は記入済み結果へ自動適用しない。

## フロントエンド

`PagedReviewPage`の確定済み結果に「全ページの記入済みXLSXを取得」を表示する。
取得APIのrevision headerを照合してからダウンロードし、古いjobの遅延応答を破棄する。
取得中もページ切替・crop閲覧を維持し、二重取得と同画面のreopenだけを止める。
失敗時は値・確定を保持して再取得による復旧を案内する。

## 実装の場所

- backend: `app/services/paged_workbook.py`、`app/services/paged_output_service.py`、
  `app/services/paged_document_snapshot.py`、`app/services/paged_review_service.py`、`app/api/v1/routes/job_pages.py`
- frontend: `src/api.ts`、`src/pages/PagedReviewPage.tsx`
