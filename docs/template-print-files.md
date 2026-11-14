# 保存済みTemplateVersionの印刷用XLSX / PDF

テンプレートの版から、未記入の印刷用XLSXとPDFを取得する機能の仕様。OCR結果・補正値は含めない。
印刷ページの境界は[XLSX印刷ページ設計](xlsx-print-layout.md)で版に保存したものを使う。
複数ページOCRの受付やrenderer認定とは別の、印刷用成果物の生成機能である。

## 取得経路

- `printLayout`がない版（旧単一ページのXLSX由来テンプレート）は
  `GET /api/v1/template-versions/{version_id}/blank-files/{xlsx|pdf}`を使い、
  版の取込jobに属する原本`sourceWorkbook`と取込時の1ページ`printablePdf`を返す。
  原本・PDFの保存SHAと版に保存したパスの内容を照合し、失敗時は別版や再変換へ切り替えない。
- `printLayout`がある版は、印刷ページ編集の「印刷用ファイルを確定」（`POST .../print-layout/prepare`）で
  変換モデル・整数倍率・指定fontの版を保存し、その時点のXLSX/PDF/ページPNGを保持する。
  確定版の取得は保存artifactを返し、再変換しない。印刷条件を変えた場合は再確定が必要。
  固定したWSL環境を使う場合は[固定WSL環境を使う印刷確定](pinned-print-preparation.md)を参照する。
- 確定前の版は、下記の`print-layout/files`で保存済みprintLayoutから毎回変換する（キャッシュなし）。
- PDFの変換経路は設定`PRINT_PDF_RENDERER`（`appcontainer` / `wsl` / `linux`）で選ぶ。
  選んだ経路だけを使い、失敗時に非隔離経路や旧1ページPDFへfallbackしない。

読み取り画面のACTIVEなXLSXカードにも取得ボタンを置く。管理カードでは新しいDRAFTがあっても
印刷用の取得対象はACTIVE版を優先し、版番号を表示する。編集の再開先はDRAFTとする。
対象は横1ページ×縦Nページ。

## 正本と既存部品

- ページ境界: `TemplateVersion.quality_json.printLayout.pages` の `startRow` / `endRow` /
  `startCol` / `endCol` / `range`。保存済みrangeをそのまま使い、page_planで再配置しない。
- 印刷条件: 同じprintLayoutの用紙・方向・mm余白・中央配置・commonScale。
- 保存: `PrintLayoutService.save` がDRAFTとbaseRevisionを照合して版に保存する。
- 原本: 版のsource_job_idに属する`sourceWorkbook` artifact。artifact SHAと
  printLayout.sourceSha256をともに照合し、生成終了時にも原本と版revisionを再照合する。
- 既存部品: `read_source`はopenpyxlによる読取、`page_copy`はzipfile/XMLの限定編集。
  原本をopenpyxlで再保存しない。既存のOCR結果用Spreadsheet出力は使わない。
- PDF: `office_reference_renderer`のprofile・Office command・process管理を共通関数へ
  抽出して再利用。旧診断経路の資源制限・失敗時metrics・PDFium helper境界を維持する。

## 確定前の生成とAPI

`GET /api/v1/template-versions/{version_id}/print-layout/files/{xlsx|pdf}?base_revision=...`

既存の認証付きrouter内。base_revisionは必須。保存設定なし・stale layout・原本不一致・
不正range・版競合は拒否する。DB・原本・保存plan・過去jobへの書込みはない。
レスポンスはattachment、Cache-Control=no-store、X-Print-Layout-Revision付き。
キャッシュを作らないため版更新後に古い生成物を再利用しない。
X-Renderer-Qualified=falseはこの生成を本番認定として扱わないことを表す。

XLSXは保存範囲の先頭から末尾までを絶対参照のPrint_Areaとし、最終ページ以外の
endRowをmanual rowBreaksへ設定する。元のrowBreaks/colBreaksは成果物上で置き換える。
用紙・余白・方向・中央配置・整数％の保存倍率は既存adapterが設定する。
Fit-to-pageでmanual breaksを無効にせず、元セル・結合・行高・列幅・書式と
未変更ZIPパートを保持する。原本のbytesは不変。

PDFは上記の**ダウンロード用XLSXそのもの**を1回のOffice変換へ渡す。
設定した変換経路だけを使い、失敗後の非隔離変換・旧1ページPDF・参考PNGへのfallbackはない。
別Job processの固定protocol helperがpypdfでページ数と各ページ用紙寸法を検査する。
backend内でnative PDF parse/rasterを行わない。
ページ数・用紙寸法一致だけをセル範囲・OCR座標の実測一致と扱わない。

## UI

テンプレート管理のXLSX由来の表示対象版、および印刷ページ編集内の保存済み設定へ、
「印刷用XLSX」「印刷用PDF」の操作を置く。
カードにprintLayoutがない場合は両ボタンを無効化し、未保存の理由を表示する。
未保存の印刷設定がある間は編集内の取得操作を無効化する。
取得中の重複操作を止め、変換失敗・版競合を表示して再試行できる。
生成中の形式と、取得後にブラウザーへダウンロードを渡した状態を表示する。
ブラウザーがディスクへの保存を完了したことまでは判定しない。
元ファイルダウンロードやOCR結果出力と名称・APIを分離している。
カードの `primaryVersion.id` を直接使用し、template IDから最新versionを探し直さない。

## 明示的な制約

- 保存倍率が小数％の場合、既存adapterの拒否を維持する。GUIが保存可能なすべてのplanを
  XLSXへ表現できるわけではない。保存倍率を黙って丸めず、既存版・OCR座標を変えない。
  OOXMLのscaleはunsignedIntであり、Fit Toはmanual breaksを無効にし得る。
  [Microsoft PageSetup](https://learn.microsoft.com/ja-jp/dotnet/api/documentformat.openxml.spreadsheet.pagesetup?view=openxml-3.0.1)、
  [Microsoft manual page breaks](https://support.microsoft.com/en-us/excel/insert-move-or-delete-page-breaks-in-a-worksheet)。
- 既存adapterのunsupported判定を維持する。横複数ページ、複数sheet、高度OOXML、図形、
  controls、外部参照、数式、hidden、未対応defined names等は対応しない。
- 環境認定条件・reference資格発行・ACTIVE gate・paged OCR受付gateはこの機能で変えない。
- PDF変換が失敗する場合、APIは失敗を返す。実環境でPDFを取得できると
  とは扱わない。helper自体のnetwork isolation等も従来の未認定条件として残る。
- XLSX/PDFを物理印刷した際のプリンタ固有差やscan registrationの保証はない。

## テスト

unitでは1/2/3ページのmanual breaks、元breakの置換、セル構造・ZIPパート保持、
不正range・横分割・結合横断・原本SHA・小数倍率拒否を確認する。
integrationでは実GUI保存API→export、原本・版・過去snapshot保持、V1/V2独立性、
PDF stub変換→実processのPDF検査、ページ数/用紙/不正PDF/変換失敗、生成中の版競合を確認する。
Office stubの成功を実Office・本番資格の成功としない。

人工帳票で再現するscript: backendで`python scripts/check_template_print.py --output <新規directory>`。
人工XLSXの1/2/3ページ・V1/V2相当を出力し、元SHA・manual breaks・保存range・変換結果をreport.jsonへ出す。
実DB・実帳票・外部APIを使わない。人工fixtureの記録は[template-print-evidence.json](template-print-evidence.json)にある。

## 実装の場所

- `backend/app/api/v1/routes/print_layout.py`、`backend/app/api/v1/routes/templates.py`（blank-files）
- `backend/app/services/office_reference_renderer.py`、`print_workbook.py`、`print_pdf.py`、`print_pdf_worker.py`、`template_print_service.py`（`backend/app/services/`）
- `backend/scripts/check_template_print.py`
- `frontend/src/components/TemplatePrintFiles.tsx`、`frontend/src/components/PrintLayoutEditor.tsx`、`frontend/src/pages/TemplatePage.tsx`
