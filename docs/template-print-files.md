# 保存済みTemplateVersionの印刷用XLSX / PDF

対象は既存GUI契約の横1ページ×縦Nページ。印刷用成果物の生成機能であり、
複数ページOCRの本番開放・renderer認定ではない。

## 調査した正本と変更境界

- ページ境界: `TemplateVersion.quality_json.printLayout.pages` の `startRow` / `endRow` /
  `startCol` / `endCol` / `range`。保存済みrangeをそのまま使い、page_planで再配置しない。
- 印刷条件: 同じprintLayoutの用紙・方向・mm余白・中央配置・commonScale。
- 保存: `PrintLayoutService.save` がDRAFTとbaseRevisionを照合して版に保存する。
  今回この保存処理・GUI境界編集・geometry計算は変更していない。
- 原本: 版のsource_job_idに属する`sourceWorkbook` artifact。artifact SHAと
  printLayout.sourceSha256をともに照合し、生成終了時にも原本と版revisionを再照合する。
- 既存部品: `read_source`はopenpyxlによる読取、`page_copy`はzipfile/XMLの限定編集。
  原本をopenpyxlで再保存しない。既存のOCR結果用Spreadsheet出力は使わない。
- PDF: `office_reference_renderer`のprofile・Office command・process管理を共通関数へ
  抽出して再利用。旧診断経路の資源制限・失敗時metrics・PDFium helper境界を維持する。

## 生成とAPI

`GET /api/v1/template-versions/{version_id}/print-layout/files/{xlsx|pdf}?base_revision=...`

既存の認証付きrouter内。base_revisionは必須。保存設定なし・stale layout・原本不一致・
不正range・版競合は拒否する。DB・原本・保存plan・過去jobへの書込みはない。
レスポンスはattachment、Cache-Control=no-store、X-Print-Layout-Revision付き。
キャッシュを作らないため版更新後に古い生成物を再利用しない。
X-Renderer-Qualified=falseは今回の生成を本番認定として扱わないことを表す。

XLSXは保存範囲の先頭から末尾までを絶対参照のPrint_Areaとし、最終ページ以外の
endRowをmanual rowBreaksへ設定する。元のrowBreaks/colBreaksは成果物上で置き換える。
用紙・余白・方向・中央配置・整数％の保存倍率は既存adapterが設定する。
Fit-to-pageでmanual breaksを無効にせず、元セル・結合・行高・列幅・書式と
未変更ZIPパートを保持する。原本のbytesは不変。

PDFは上記の**ダウンロード用XLSXそのもの**を1回のOffice変換へ渡す。
AppContainerを必須にし、失敗後の非隔離変換・旧1ページPDF・参考PNGへのfallbackはない。
別Job processの固定protocol helperがpypdfでページ数と各ページ用紙寸法を検査する。
backend内にnative PDF parse/rasterを追加していない。
ページ数・用紙寸法一致だけをセル範囲・OCR座標の実測一致と扱わない。

## UI

テンプレート管理の保存済みprintLayoutを持つ版、および印刷ページ編集内の保存済み設定へ、
「印刷用XLSX」「印刷用PDF」を追加した。新規画面なし。
未保存の印刷設定がある間は編集内の取得操作を無効化する。
取得中の重複操作を止め、変換失敗・版競合を表示して再試行できる。
元ファイルダウンロードやOCR結果出力と名称・APIを分離している。

## 明示的な制約

- 保存倍率が小数％の場合、既存adapterの拒否を維持する。GUIが保存可能なすべてのplanを
  XLSXへ表現できるわけではない。保存倍率を黙って丸めず、既存版・OCR座標を変えない。
  OOXMLのscaleはunsignedIntであり、Fit Toはmanual breaksを無効にし得る。
  [Microsoft PageSetup](https://learn.microsoft.com/ja-jp/dotnet/api/documentformat.openxml.spreadsheet.pagesetup?view=openxml-3.0.1)、
  [Microsoft manual page breaks](https://support.microsoft.com/en-us/excel/insert-move-or-delete-page-breaks-in-a-worksheet)。
- 既存adapterのunsupported判定を維持する。横複数ページ、複数sheet、高度OOXML、図形、
  controls、外部参照、数式、hidden、未対応defined names等の対応範囲は拡張していない。
- 環境認定条件・reference資格発行・ACTIVE gate・paged OCR受付gateは変更していない。
  `environmentQualified=false` / `productionQualified=false`のままである。
- PDF変換が現AppContainer内で失敗する場合、APIは失敗を返す。実環境でPDFを取得できると
  は報告しない。helper自体のnetwork isolation等も従来の未認定条件として残る。
- XLSX/PDFを物理印刷した際のプリンタ固有差やscan registrationの保証は追加していない。

## 検証

追加unitでは1/2/3ページのmanual breaks、元breakの置換、セル構造・ZIPパート保持、
不正range・横分割・結合横断・原本SHA・小数倍率拒否を確認する。
追加integrationでは実GUI保存API→export、原本・版・過去snapshot保持、V1/V2独立性、
PDF stub変換→実processのPDF検査、ページ数/用紙/不正PDF/変換失敗、生成中の版競合を確認する。
Office stubの成功を実Office・本番資格の成功としない。

再現script: backendで`.venv/Scripts/python.exe scripts/check_template_print.py --output <新規directory>`。
人工XLSXの1/2/3ページ・V1/V2相当を出力し、2ページケースについて実AppContainer変換を試す。
元SHA・manual breaks・保存range・変換結果をreport.jsonへ出す。実DB・実帳票・外部APIを使わない。

## 2026-09-09 検証結果

開始HEAD / 終了HEAD: `b880d76484e3c687b9efc3032fcc5d6cae1c0ff9`。
commit / staging / pushなし。開始時からの`frontend/src/styles.css`差分は保持し、今回編集していない。

| 検証 | 結果 |
| --- | --- |
| OCR contract script 変更前 / 変更後 | 194 passed / 194 passed |
| backend full | 753 passed, 1 skipped, 3 warnings（617.80秒）。skipは既存の実OpenAI opt-in |
| 最終対象: print workbook + reference qualification unit + print files API | 46 passed（18.17秒） |
| 今回追加backendテスト | unit 14件、integration 19件。上記最終対象に含む |
| frontend full | 250 passed / 24 files |
| frontend最終対象3ファイル | 34 passed |
| frontend build | 成功。既存500KB超bundle警告あり |
| Ruff（変更Python 9ファイル） | 成功 |
| 新規サービス4ファイルmypy、follow-imports=silent | 成功 |
| mypy app全体 | 35 errors / 6 files。対象6ファイルはHEADから変更なし。全体成功ではない |
| git diff --check | 成功 |

full収集後に、未参照worksheetパートへの不要な編集を防ぐ限定修正と1件のunitを追加し、
共通変換関数の失敗時metrics保持を確認した。これらは最終対象46件で検証している。
最終版全体を754 passedと数え直してはいない。
途中で検出したfrontend型エラー・新規Python型エラーは修正し、該当検査を再実行した。
実API呼出しはOpenAI 0回、Google 0回。

実行コマンド（backend内では`.venv/Scripts/python.exe`、frontend内ではnpm）:

- root: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`（変更前後）
- backend: `python -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=.pt-print-files-full`
- backend最終対象: `python -m pytest tests/unit/test_print_workbook.py tests/unit/test_reference_qualification.py tests/integration/test_template_print_files.py -q -o addopts='' -p no:cacheprovider --basetemp=.pt-print-files-final`
- frontend: `npm test`、`npm run build`
- backend: `python -m ruff check`に変更Python9ファイルを指定、`python -m mypy app --no-pretty`
- 新規サービス限定: `python -m mypy app/services/print_workbook.py app/services/print_pdf.py app/services/print_pdf_worker.py app/services/template_print_service.py --follow-imports=silent --no-pretty`

完了した今回のtest temp 5個は`C:/Users/hanam/.codex/tmp/template-print-tests-20260909-2102/`へ移動し、
検証証拠を保持した。既存tempやユーザーデータの削除はしていない。

### 実renderer試行

[実測report](template-print-evidence.json)に人工fixtureのrange・manual breaks・SHAを保存した。
生成ファイルは`C:/Users/hanam/.codex/tmp/template-print-20260909-2101/`に保持している。

| ケース | 保存range | 成果物のmanual breaks |
| --- | --- | --- |
| 1ページ | B2:D33 | なし |
| 2ページ / V1相当 | B2:D16, B17:D33 | 16 |
| 3ページ | B2:D10, B11:D20, B21:D33 | 10, 20 |
| V2相当 | B2:D20, B21:D33 | 20 |

元のmanual breakは9。全ケースで保存rangeを優先し、原本SHAは前後一致。
2ページ成果物を実AppContainer経路で変換した結果は`RENDER_FAILED`、PDF生成なし。
非隔離fallbackは使用していない。ページ数・セル範囲・境界の実描画一致、実Excel・物理印刷は
未検証であり、**実装済み・renderer実測未検証**とする。既存文書のIPC原因を今回再診断したとはしない。
初回の人工fixtureは既存source readerが要求する四辺border定義を欠き、Office到達前に失敗した。
人工fixtureを四辺罫線に修正して再試行した。source reader自体は変更していない。

renderer qualificationの状態は引き続き未認定。新規コードは資格を発行・更新しない。
OCR recognition/provider/model/prompt/schema、rectangle detection、association/mapping/crop、
補正・review・confirmed・Sheets出力・過去snapshotは変更していない。
既存OCR contractを維持した。ただし実API精度比較や実帳票OCRの保証を追加したものではない。

未解決事項は、実隔離Office変換・実レンダリング一致、および既存adapterでは表現できない
小数％保存倍率等の明示拒否。したがって「GUIで保存できる全版から印刷用ファイルを取得できる」
という無条件の完了宣言はしない。横複数ページ、縦横分割、OCR全体の完成、資格緩和は対象外。

## 変更ファイル

- `backend/app/api/v1/routes/print_layout.py`
- `backend/app/services/office_reference_renderer.py`
- `backend/app/services/print_workbook.py`
- `backend/app/services/print_pdf.py`
- `backend/app/services/print_pdf_worker.py`
- `backend/app/services/template_print_service.py`
- `backend/scripts/check_template_print.py`
- `backend/tests/unit/test_print_workbook.py`
- `backend/tests/integration/test_template_print_files.py`
- `frontend/src/api.ts`
- `frontend/src/components/TemplatePrintFiles.tsx`
- `frontend/src/components/TemplatePrintFiles.test.tsx`
- `frontend/src/components/PrintLayoutEditor.tsx`
- `frontend/src/components/PrintLayoutEditor.test.tsx`
- `frontend/src/pages/TemplatePage.tsx`
- `frontend/src/pages/TemplatePage.test.tsx`
- `docs/ocr-safety-contract.md`
- `docs/template-print-files.md`
- `docs/template-print-evidence.json`
