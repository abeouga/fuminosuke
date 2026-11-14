# 保存済みTemplateVersionの印刷用XLSX / PDF

2026-09-25追記: 読み取り画面のACTIVEな旧単一ページXLSX由来テンプレートでは、
既存の「印刷用XLSX」「印刷用PDF」ボタンから未記入帳票を取得する。
`printLayout`がない版は`GET /api/v1/template-versions/{version_id}/blank-files/{xlsx|pdf}`を使い、
版の取込jobに属する原本`sourceWorkbook`と取込時の1ページ`printablePdf`を返す。
原本・PDFの保存SHAと版に保存したパスの内容を照合し、失敗時は別版や再変換へ切り替えない。
OCR結果・補正値は含めない。`printLayout`がある版の既存取得経路は以下のままとする。

2026-09-12追記: [複数ページ本番化](https://github.com/abeouga/agorack-document/blob/main/fuminosuke/history/docs/production-paged-ocr.md)で、利用者が導入を許可した
WSL/bubblewrap/cgroup経路を印刷用PDFの選択肢に追加した。`PRINT_PDF_RENDERER=wsl`のときは
この経路だけを使い、失敗時に非隔離/旧1ページへfallbackしない。人工1/2/3ページの実生成を確認した。
2026-09-13追記: 同作業記録の新adapterは明示されたplanだけに適用する。
既存版の自動移行は未実装。実帳票4ページの生成は指定font・69%の診断条件で成立したが、
保存済み小数倍率をダウンロード時に丸める処理は追加していない。
以下のAppContainer失敗記録とstrict workbook adapterの拒否範囲は維持する。
同日後続実装: GUIの「印刷用ファイルを確定」と`POST print-layout/prepare`を追加した。
明示操作で新WSLモデル/整数倍率/指定fontの版を保存し、その時点のXLSX/PDF/ページPNGを保持する。
確定版の取得は保存artifactを返し、再変換しない。印刷条件を変えた場合は再確定が必要。
以下の「毎回変換・キャッシュなし」は未確定の従来planに対する説明であり、新確定版には適用しない。
読み取り画面のACTIVE XLSXカードにも取得ボタンを追加した。管理カードでは新しいDRAFTがあっても
印刷用の取得対象はACTIVE版を優先し、版番号を表示する。編集の再開先は従来どおりDRAFTとする。

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

テンプレート管理のXLSX由来の表示対象版、および印刷ページ編集内の保存済み設定へ、
「印刷用XLSX」「印刷用PDF」を追加した。新規画面なし。
カードにprintLayoutがない場合は両ボタンを無効化し、未保存の理由を表示する。
未保存の印刷設定がある間は編集内の取得操作を無効化する。
取得中の重複操作を止め、変換失敗・版競合を表示して再試行できる。
生成中の形式と、取得後にブラウザーへダウンロードを渡した状態を表示する。
ブラウザーがディスクへの保存を完了したことまでは判定しない。
元ファイルダウンロードやOCR結果出力と名称・APIを分離している。

## 2026-09-12 カード操作の再確認・補完

既存の上記API・生成service・命名・隔離条件を正本として再利用した。別endpointや
並行rendererを追加せず、frontendの不足した表示とテストだけを補完した。
カードの `primaryVersion.id` を直接使用し、template IDから最新versionを探し直さない。
元セル・結合を再構成せず、保存済みprintLayoutを変換する既存処理を維持する。
数式保持を理由に、現adapterが拒否する数式入り帳票を新たに通すこともしない。

### 今回の変更とGit

開始/終了HEAD: `b613c11a72b20d4c7cafc7ec3dd7ddf9adf52b55`。staged差分は開始/終了ともなし。
開始時はtracked変更30ファイルと既存untracked（過去テストtemp、手動編集・位置合わせ・
自動保存のコード/テスト/文書）が存在した。それらを保持し、今回の変更として数えない。
今回の変更は次の5ファイル。終了時tracked変更は34ファイル（4ファイル増加）。

- `frontend/src/components/TemplatePrintFiles.tsx`
- `frontend/src/components/TemplatePrintFiles.test.tsx`
- `frontend/src/pages/TemplatePage.tsx`（既存差分にカード表示条件だけを追加）
- `frontend/src/pages/TemplatePage.test.tsx`
- `docs/template-print-files.md`

検証が追加したtemp/evidenceは保持した。原本・実DB・公開版・過去jobへの書込みなし。

### 検証結果

| 検証 | 結果 |
| --- | --- |
| root `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py` 変更前/後 | 194 passed / 194 passed（41.51秒 / 53.92秒） |
| backend `pytest tests/unit/test_print_workbook.py tests/unit/test_reference_qualification.py tests/integration/test_template_print_files.py -q -o addopts='' -p no:cacheprovider --basetemp=.pt-print-card-20260912` | 46 passed |
| frontend `npm.cmd test -- TemplatePrintFiles.test.tsx TemplatePage.test.tsx` | 34 passed |
| frontend `npm.cmd test` | 29ファイル、300 passed |
| frontend `npm.cmd run build` | 成功。既存の500kB超bundle警告あり |
| `git diff --check` | 成功 |
| backend full / Ruff / mypy | backend変更なしのため今回は再実行なし。上記対象回帰とOCR契約で検証 |

追加テストは未保存理由、stale失敗/再試行、XLSXのみのカード表示、2カードと旧版のID非混線。
既存の二重操作拒否・XLSX/PDF download・失敗後の再試行を維持した。
API integrationはsource SHA、保存range、版分離、stale拒否、元ZIP内容、PDFへ渡すXLSX、
用紙寸法/ページ数の検査を含む。PDF成功ケースはOffice stubであり実Office成功ではない。
実Chromeのカードから保存完了までのE2E、物理印刷は未実施。

### 人工帳票の実生成

既存scriptをbackendから実行:
`.venv/Scripts/python.exe scripts/check_template_print.py --output <出力先>`

同directoryの `report.json` とXLSX/planを保持した。1/2/3ページとV1/V2相当のXLSXを生成し、
元break=9に対し、保存breakのなし/16/10,20/20が成果物へ反映された。原本SHAは不変。
用紙A4縦、余白各10mm、中央配置なし、保存倍率100%。構造保持は上記unitでZIPパートと
sheetData・行高列幅・結合等を比較した。実際のプリンターのページ分割は未検証。

LibreOffice `26.2.3.2 70e089b17412e4cb7773e41413306b17a2328c34` の既存AppContainer変換を
再試行したが `RENDER_FAILED`。PDFファイルは生成されず、実PDFページ数は未検証。
非隔離fallback・偽PDF・qualification flag変更は行っていない。
XLSXはメモリ上の別bytes、PDF用一時ファイルは既存 `render_directory` のrequest別領域で
生成し、既存cleanupとprocess境界を維持する。

### 指定のキャリア・プラン帳票

実DBをread-only接続し、版 `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` のsourceWorkbookと保存planを
照合した。保存範囲は `A1:AK28`, `A29:AK60`, `A61:AK89`, `A90:AK102`、breakは28/60/89。
A4縦、左右18mm・上下19mm、横中央、縦中央なし、commonScale=`0.6957339287812906`。
artifact SHAとplan.sourceSha256が原本bytesに一致し、検査後も原本SHAは不変。

既存 `workbook_copy` は `UNSUPPORTED_XLSX_FEATURE`（`AlternateContent`）で拒否した。
この実帳票のXLSX/PDF取得はBLOCKED。高度XMLを削除して対応させていない。
加えて保存倍率が小数％であり、現adapterでは黙った丸めも許可しない。

### 判定

全体は **PARTIAL**。対応fixtureのXLSX取得・原本保持・版境界は検証済みだが、
実隔離PDF変換は未成立。指定実帳票は既存adapterの対応範囲外。
OpenAI 0回、Google 0回。既存OCR contractと本番paged OCRの拒否gateを維持した。
本結果をrenderer認定・複数ページOCR完成・実OCR精度検証済みとは扱わない。

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


### 実renderer試行

[実測report](template-print-evidence.json)に人工fixtureのrange・manual breaks・SHAを保存した。

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
