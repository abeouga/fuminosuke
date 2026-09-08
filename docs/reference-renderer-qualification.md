# XLSX Reference Renderer Qualification — 2026-09-08

実行環境の現状は[Environment Qualification](renderer-environment-qualification.md)を参照。

後続[Geometry Calibration](geometry-calibration.md)で新規source寸法を改善した。
以下の数値は変更前の歴史的evidenceとして保持する。本番gateは引き続き閉鎖。

本Phaseは**診断基盤まで実装、本番qualificationは未達**です。LibreOffice実画像で列幅方向の累積誤差を検出しました。座標許容値の実測校正、ネットワーク遮断、OSによるプロセス資源隔離を認定できていないため、qualifiedを発行せず、ACTIVE化と複数ページOCR受付の拒否を維持します。API許可フラグを追加して解除する方式にはしていません。

## 1–3. 開始状態とbaseline

- 開始HEAD／終了HEAD: `d33e192cb4483fe3a2a857a2b5f51f046a8f8fbe`、main、worktreeは1個。
- 開始時点でtracked変更18ファイル、untracked21項目。前Phaseの未コミット変更を保持しています。staging、commit、push、reset、revertは行っていません。
- 実ファイルのAGENTS.md、OCR Safety Contract、実コード・テスト・migrationを確認。migration headと現在DBはいずれも`0031_template_field_identity`。既存`JobInput`列を確認し、schema変更・migration適用はしていません。
- 変更前: OCR契約194 passed、print layout/job page関連34 passed、frontend関連32 passed。変更前のbackend全体テストは今回未実行です。過去の全体成功を今回のbaselineとは扱いません。

## 4–10. Renderer比較と保存範囲の適用

| 経路 | 確認結果 | 今回の扱い |
| --- | --- | --- |
| 現Pillow | sourceモデルの近似フォント・罫線描画。同じモデルから画像と矩形を作るため、相互一致だけではExcel互換精度の証明にならない | 既存の下書き参考画像として保持 |
| 既存LibreOffice preview | openpyxlで一時再保存、fitToPage、幅1×高さ1、PageRange=1、最後に画像X/Yリサイズ | legacy経路を変更せず維持。物理ページ検証へ転用しない |
| 新LibreOffice診断経路 | LibreOffice 26.2.3.2、XLSX→PDF→150dpi PNG。保存した各rangeを1冊ずつ一時コピーへ設定。出力PDFが正確に1ページであることを検査 | 診断専用。OCR基準画像へ自動昇格しない |

一時XLSXはZIP内のworkbook.xmlのPrint_Areaと、選択worksheetのprintOptions/pageMargins/pageSetup/pageSetUpPr/rowBreaks/colBreaks/headerFooterだけを限定変更します。印刷範囲には`$B$2:$D$15`のような絶対参照を使います。相対参照ではLibreOfficeが期待位置から描画しないことをprobeで確認し、修正しました。

元XLSXをopenpyxlで再保存していません。未編集ZIPパートの展開内容はbyte単位で維持し、sheetData等の同一性をテストしています。元ファイル自体は不変です。未知のXML prefix、複数sheet、高度な要素、binary partsは限定adapterで安全に扱えないため拒否します。画像を含むXLSXをいったん再保存してから「維持した」とする処理はありません。

保存rangeを正本とし、元manual breaksは一時コピーから除去します。用紙・余白・方向・centering・commonScaleは保存値を使用します。LibreOfficeの印刷倍率は整数percentのため、小数percentの保存倍率は丸めず`UNSUPPORTED_XLSX_FEATURE`です。全ページを個別Fitする処理はありません。

`SinglePageSheets`は用紙サイズ・印刷範囲を無視するため使用しません。[LibreOffice公式PDF export parameters](https://help.libreoffice.org/latest/en-US/text/shared/guide/pdf_params.html)

## 11–15. 座標測定と実測

既存のlogical→page uniform transformを予測座標として使用します。Office PNGの黒画素に水平・垂直morphologyを適用し、各四辺の連続被覆率を測定します。予測位置±8px内に一意な罫線帯がある場合だけその中心を採用し、複数候補・細すぎるセル・罫線なしは未検証です。罫線探索でscale/offsetを推定・修正しません。legacyのX/Y画像合わせも使用しません。

印刷範囲内の罫線セルから左上・右上・中央・左下・右下に分散した5個の異なるanchorを要求します。OCR fieldはsourceRangeで対応させ、装飾セルとは別にfield rectangleの四辺も検査します。20pxずらしたfieldを失敗させるテストを追加しました。

誤差はleft/right/top/bottomの符号付きpixel差、絶対最大、平均、用紙長辺による正規化値を保存します。未照合fieldを誤差ゼロの合格とは扱いません。default-widthの最大5pxは照合できたセルだけの値であり、未照合fieldを含むページ全体の最大誤差の保証ではありません。

| 実Office fixture | 物理ページ | 結果 | 最大誤差 | 再描画PNG一致 |
| --- | --- | --- | --- | --- |
| default-width | B2:D12 | FAIL、anchor不足 | 照合できたセルで5px | 一致 |
| explicit-width（8.43） | B2:D12 | FAIL | 7.5px | 一致 |
| saved-two-pages | B2:D15 / B16:D30 | 両ページFAIL | 各7.5px | 一致 |

元manual breaksで3区画に分かれるfixtureを、保存範囲2ページへ描画できました。元break配置をそのまま使った結果ではありません。PDF metadataのhashは比較せず、各PNGを2回生成して完全一致を比較しています。

`RASTER_EDGE_BUDGET_PX=2.0`は「両端丸め0.5px×2＋1pxのアンチエイリアス幅」という**暫定診断予算**です。実測に基づく本番合格閾値は決定できていません。`thresholdQualified=false`を明記し、7.5pxへ閾値を緩めて合格させていません。列幅モデル、Normal fontの文字幅、Officeの幅換算を一次情報から解決し、異なる行高・列幅・倍率・ページ位置のfixtureで校正する必要があります。

実測JSON・人工XLSX・Office PNGは[reference-renderer-evidence](reference-renderer-evidence/audit.json)に保存しています。実帳票のセル値は報告していません。

## 16–19. Evidence、失効、gate

`TemplateVersion.quality_json.referenceQualification`はfingerprint・report相対パス・report SHAを保持します。report schemaVersion 1には版ID、作成時刻、元SHA、plan SHA、range一覧、field数、renderer情報、PNG SHA、反復一致、ページ別metric、失敗code、本番許可を妨げる条件を保存します。

fingerprintは版ID、PrintLayoutService.revision、設定ファイルSHAから生成します。printLayout全体、field geometry/layout、page range、紙・余白・scale・reference metadataがrevisionを通して含まれます。版コピーに同じreportを移しても別版fingerprintには一致しません。取得時にはreport・元XLSX・既存基準PNG・診断PNGの内容SHAとrenderer identityも再照合します。設定・矩形・画像・版の変更後はUNVERIFIEDです。

現renderer identityはtype、LibreOffice build version、adapter version、起動実行ファイルSHA、DPI、PDF rasterizer種別を保持します。フォント実体やPDFium native libraryを含む完全な環境fingerprintは未実装です。ここも本番qualifiedを発行できない理由です。

`JobPageService._require_qualified_reference`は保存artifact検証へ接続しました。ただし現policyには認定済みrendererがないため、JSONのqualified=trueやocrReady=trueだけでは通りません。ACTIVE化側の既存拒否も解除していません。診断画像は別パスに保存し、既存plan/reference/job snapshotを書き換えません。qualified fixtureがないため、資格を通した新job受付・snapshot実行は未検証であり、完成扱いしません。

## 20–22. 指定5冊と20,000セル制限

| XLSX | 判定 | 主な根拠 |
| --- | --- | --- |
| 1 給水装置台帳等複写申込書.xlsx | needs manual page setup＋unsupported | 明示print areaなし。取得boundsはA1:AA1000で27,000セル。drawingあり |
| キャリア・プラン作成補助シート（学生用）.xlsx | unsupported | AlternateContent、複数sheet、hidden等。print area A1:AK102 |
| 再検査登録依頼書.xlsx | unsupported | extLst等。print area A1:BV75。既存source側はrotation/shrinkも拒否 |
| 口座振替依頼書.xlsx | unsupported | binary部品、図形・画像、3sheet、hidden、数式等。各print areaはA1:BW107/108 |
| 市・県民税申告書.xlsx | needs manual page setup＋unsupported | 明示print area A1:CH326自体が28,036セル。binary部品もあり |

全5冊で原本SHAは前後一致。各SHAとsheet別の根拠はaudit.json、raw printAreaとdimensionは[bounds.json](reference-renderer-evidence/bounds.json)にあります。

20,000セル制限は現在`read_source`がセル範囲を列挙・モデル化する際の上限であり、Pillowだけの制約でもExcelの制約でもありません。今回は増やしていません。明示print areaがある帳票ではdimensionより狭い正しい範囲を既に採用しています。市・県民税申告書はprint areaを使用しても超過します。給水帳票の空白らしい末尾を根拠なく切り捨てる変更もしていません。

新Office adapter自体はrotation/shrink属性を削除しませんが、既存source gateが許可していないため本番対象には増やしていません。hiddenを可視化したり、shapeを失ったコピーを通したりしません。画像/図形/controls/OLE、外部参照、数式、print titles、header/footer、高度な拡張は明示拒否です。外部参照のない数式の再計算許可も未実装です。

## 23–24. Resource/securityと再現性の境界

- 64MiB圧縮、128MiB展開、16MiB/part、5,000 entries、重複・暗号化・不正ZIPを拒否。
- XML DTD/entity、外部relationship、macro content type、数式、active/advanced elementsをOffice起動前に拒否。
- 一意な短い一時directory／page.xlsx、独立LibreOffice profile。元ファイル名をOfficeへ渡しません。
- `DisableMacrosExecution=true`、`DisableActiveContent=true`を独立profileへ設定。インストール済みLibreOfficeのregistry schemaで名称と意味を確認。
- 同一Python process内でOffice同時実行は1件、診断最大10ページ、1変換45秒、PNG最大20M pixels。全ページを先にXLSXコピーしてメモリに抱える処理はありません。
- timeout時はWindowsで自分が起動したPIDのprocess treeをtaskkill。合成timeoutテストで終了操作・temp cleanup・lock解放を検証。実Office正常終了後に残留soffice processがないことを確認。
- **OS強制メモリ制限、ネットワーク遮断、複数worker間の同時実行制限、server強制終了時の子process cleanup保証は未実装です。** profile設定や外部参照のpreflightを、OS sandboxの保証とは扱いません。
- 長い元ファイル名はOfficeから分離していますが、260文字超の実Windows経路によるend-to-end検証は未実施です。

反復PNG一致はこのhostの3人工fixtureだけの証拠です。他Office版・他OS・他font、実Excel PDFとの一致を示すものではありません。外部作成PDFを将来受け付ける場合も、任意PDFをそのまま信頼せず、元SHA・保存range・版・geometry測定を結び直す必要があります。

## 25–30. 変更と検証

今回追加: `office_reference_renderer.py`、`reference_geometry.py`、`reference_qualification_service.py`、`backend/scripts/audit_reference_renderer.py`、backend unit/integration各1ファイル、`ReferenceQualification.tsx`と同test、本報告・人工fixture証拠。

既存／前Phaseファイルへの今回の追加: `routes/print_layout.py`（qualification GET/POST）、`job_page_service.py`（evidence検証接続）、`frontend/src/api.ts`、`printLayout.ts`、`PrintLayoutEditor.tsx`と同test（UI接続とAPI mock）。その他開始時差分を今回の変更として整理していません。

| 検証 | コマンド（backendは同directoryから） | 結果 |
| --- | --- | --- |
| OCR契約 前後 | root: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py` | 194 / 194 passed |
| 新規境界 | `python -m pytest tests/unit/test_reference_qualification.py tests/integration/test_reference_qualification_api.py` | 最終17 passed（UTF-16 entity拒否を含む） |
| 既存Job/Page＋新API | `python -m pytest tests/integration/test_reference_qualification_api.py tests/integration/test_job_pages.py` | 実行時点13 passed |
| backend全体 | `python -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=.pt-rq-release` | 601 passed / 1 skipped。以後のUTF-8限定ガード追加は上記17件で確認 |
| frontend対象 | `npm test -- ReferenceQualification.test.tsx PrintLayoutEditor.test.tsx OcrPageBindings.test.tsx TemplatePage.test.tsx` | 35 passed |
| frontend全体 | `npm test` | 241 passed / 23 files |
| frontend build | `npm run build` | 成功、既存bundle size warningあり |
| Ruff対象 | 新規／今回変更のPython計8ファイル | 0 errors |
| 全体Ruff | `python -m ruff check app tests` | 47 errors、今回変更箇所の新規0 |
| 全体mypy | `python -m mypy app` | 36 errors / 6 files、今回追加モジュールの新規0 |
| 差分 | `git diff --check` | 成功 |

実Office監査再実行: `backend/.venv/Scripts/python.exe`をbackendから使い、`scripts/audit_reference_renderer.py --xlsx-directory C:/Users/hanam/Desktop/xlsx --output ../docs/reference-renderer-evidence`。

UIは実browserの独立component harnessでUNVERIFIED→FAIL、最大7.5px・平均2.40px・renderer版・理由表示と再試行ボタンを確認しました。APIはfixtureであり、実ユーザーのテンプレート操作からOfficeまでを通したUI E2Eではありません。

## 31–35. 実API、契約、残課題、終了状態

OpenAI API 0回、Google API 0回。追加されたOpenAI上限30回の許可は未使用です。qualification自体にLLMを使用していません。

provider/model/prompt/schema、既存OCR座標・X/Y画像合わせ・association/mapping、補正/確定/revision、既存job snapshot、XLSX出力、Drive/Pickerを今回変更していません。旧単一ページ経路への印刷設定の流入拒否と契約テスト前後一致を根拠に、**既存OCR contractを維持した**と判断します。ただし実API品質比較や未整備Golden replay全体を検証したという意味ではありません。

今回本番qualifiedにできた範囲はありません。単純Office fixtureもFAILであり、全体Phase完了とは扱いません。現時点で拒否する範囲は全paged referenceの本番有効化・OCR受付です。診断可能な範囲は1sheet、静的XML、可視セル、整数percent倍率の保存rangeに限定します。

次に必要なのは、(1) OfficeのNormal fontを含む列幅換算をsource geometryへ正確に反映する設計、(2) 未指定列幅・明示列幅・幅多数・高さ多数・倍率多数での独立校正、(3) OS隔離と環境fingerprintの認定、(4) 実帳票の安全な高度要素対応、(5) その後にだけqualified fixtureのACTIVE→受付→snapshot→mock OCRを接続して検証する作業です。許容誤差の拡大、offset補正、boolean gate解除による代用はしません。

終了時の`git status --short`はtracked変更18ファイル、untracked31項目（directory集約を含む）です。未コミット差分を保持し、commit・pushなし。今回の検証用tempとbrowser harnessは実ユーザーデータと分け、`C:/Users/hanam/.codex/tmp/llm-ocr-reference-20260908-0030`へ移動しました。今回起動したViteと検証tabは終了。soffice残留processはありません。
