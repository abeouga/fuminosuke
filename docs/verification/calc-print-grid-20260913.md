# Calc印刷グリッドの座標差 — 2026-09-13

## 状態と変更境界

以下は調査開始時の記録。2026-09-14の明示保存経路への接続は末尾の追記を参照する。

テンプレート有効化は未完了。参照測定が成立した4ページの保存座標とPDF罫線に
最大1.65625pxの差が残っているため、同じ入力・保存証拠で計算過程を調べた。
原因を再現する `backend/app/services/calc_print_grid.py` とunitを追加したが、
本番の保存・geometry・qualification処理からはimportしていない。
旧plan、canonical layout、実DB、元XLSX、スキャン画像、OCR snapshotは変更していない。

## 一次ソースと原因

固定環境のLibreOffice 7.3.7.2と同じtagを照合した。

- [FillInfo](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/sc/source/core/data/fillinfo.cxx):
  各行高・列幅をmm100へ換算し、1〜65535へ制限して整数化してから累積する。
- [PrintPage / InitModes](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/sc/source/ui/view/printfun.cxx):
  用紙矩形・余白・倍率は整数twipの計算を含む。中央配置の基準寸法は公称twip合計であり、
  切捨て後のグリッド合計ではない。原点をmm100へ変換してから描画倍率を適用する。
- [DrawFrame](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/sc/source/ui/view/output.cxx):
  グリッド罫線の開始位置に1mm100単位の差がある。
- [UNO寸法取得](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/sc/source/ui/unoobj/cellsuno.cxx)、
  [単位変換](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/include/o3tl/unit_conversion.hxx)、
  [XML変換](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/sax/source/tools/converter.cxx):
  twipから最寄りmm100、uint16、4桁inchという寸法出力経路がある。

現在の `wsl-fods-physical-length-v1` はFODSの物理長をそのまま累積し、
用紙原点を連続値で計算するため、上記の各段階の整数処理を再現していない。
最大差の例はページ1のfield `xlsx_AF6` 右辺で、保存値1027px、丸め前1026.50254px、
観測値1025.34375pxだった。これは原因説明の例であり、そのIDによる例外処理は追加していない。

## 同一証拠での読み取り専用比較

検証DB/storageコピー `C:/Users/hanam/AppData/Local/Temp/llmocr-prepare-copy-x5_l5t3k` を使用。
DBはSQLite `mode=ro` で開き、比較候補はメモリ上だけで生成した。
版 `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`、revision
`7648ae168127a4029875094e419f0469de490fb6e6f9fb8f1256ae5eb5d69d4b`。
所有reportは同コピーの
`artifacts/7b90d415-98d5-44e4-971a-e477ded0fdab/qualification/47e70d71e3194367842fe59a1589d9df/report.json`、
SHA `1654c4f979cb61d555df1617dbbbb6d3b5a5ac75e5941c2b7fadc984b59317cc`。
所有probe-vectorsと同じ測定PNGを再利用し、native描画を並行実行していない。

| ページ | field数 | 旧保存座標との差の最大px | 候補整数座標との差の最大px | 候補丸め前との差の最大px |
| --- | ---: | ---: | ---: | ---: |
| 1 | 60 | 1.656250 | 0.485417 | 0.002264 |
| 2 | 51 | 1.448409 | 0.501591 | 0.002559 |
| 3 | 37 | 1.390075 | 0.480758 | 0.002575 |
| 4 | 3 | 0.795833 | 0.410417 | 0.001821 |

151field・604辺すべてで、既存 `reference_vector_measurement.measure_fields` を再実行した。
全fieldが診断予算内、明示bindingsと各辺のsegmentIndicesは全件不変だった。
任意offsetのfit、PNG探索、許容値の拡大、近傍fieldへの付替えは行っていない。
これは同一帳票の診断であり、独立holdoutでの精度、保存response、実crop、認識精度の保証ではない。
候補planに対する正式な資格処理・保存はまだ行っていない。

## 寸法出力の制限

UNO出力にはmm100をuint16へcastする箇所があり、37154twip以上ではoverflowし得る。
当初の数学unitはuint16 twip全域でcast前の出力を仮定していた。一次ソース確認後、
非overflow域の全件復元と、overflow域の拒否へ訂正した。これは実rendererの全域成功証拠ではない。
新モジュールは4桁inchへ再出力して完全に一致する値だけを受け付け、単に近い値を採用しない。
ただしwrap後の小さい値は、本来小さい寸法とFODSだけでは区別できない。
呼出側で固定export条件・原本identity・元寸法の非overflowを独立に確認する必要がある。
この条件を満たす本番adapterは未実装であり、既存FODS evidenceへ条件成立を後付けしない。

## 検証

```powershell
# backend
.venv/Scripts/python.exe -m pytest tests/unit/test_calc_print_grid.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-calc-print-grid-export-0913
.venv/Scripts/python.exe -m ruff check app/services/calc_print_grid.py tests/unit/test_calc_print_grid.py
.venv/Scripts/python.exe -m mypy app/services/calc_print_grid.py --follow-imports=silent
```

unitは **40 passed（0.30秒）**。初版36件に固定export外・overflow拒否4件を追加した。
非表示dimensionのID/順序、各寸法の切捨て、余白・inclusive extent・中央配置、
ゼロ余白で負になる罫線原点を隠さないこと、範囲外入力拒否を含む。
本番に未接続なので、実行中の `.pt-splash-full-0913` がこの新モジュールを検証したとは扱わない。
既存OCR contractを維持し、provider/model/prompt/schema・association・cropは未変更。
実OpenAI 0/100回、Google 0回。実認識の誤対応・schema error・文字誤読率は未測定。

## 次の検証条件

固定exportと元寸法の対応を確認し、独立した行高/列幅・倍率・余白・中央配置・
非表示行列・ページ開始位置のholdoutを同じ隔離環境で描画する。
既存版を暗黙更新せず、新geometry modelとして明示再確定する経路を設計する。
fit/中央配置用の公称twipと描画用mm100を混同せず、元範囲・mapping・page identityを保持する。
全体回帰が終了するまで本番importやhelper bundleを変更せず、実native検証を重複起動しない。
座標改善だけで環境資格・本番許容値・入力時crop安全性やACTIVE/受付gateを解除しない。

## 独立した実描画holdout

先行Splash接続後のbackend fullは **1964 passed / 1 skipped / 3 warnings（2166.97秒）**
で終了した。1 skippedは外部OpenAI用opt-in markerであり、実APIは呼び出していない。
終了を確認した後にだけ、以下のnative描画を実行した。テストの重複実行はない。

スプレッドシートスキルに従い、bundled Artifact Toolで検証専用の24行×8列を新規生成した。
セルは0〜9の数字と罫線だけで、8種類の列幅と6種類の行高を持つ。元帳票やラベルは使用しない。
再計算・値/エラー検査・全範囲の画像確認・XLSX exportを行った。
最初のpreviewでは右端の余白がなく、表示範囲だけをI25まで広げて全体を再確認した。
印刷条件は製品の原本保持adapterが検証用コピーへ設定し、元fixtureのSHAを終了時に再確認した。

検証ディレクトリ:
`C:/Users/hanam/AppData/Local/Temp/llmocr-calc-grid-c47d1ca0b3cc46bd81223ea6bbca81fb`

- `outputs/01a0956d-ab53-77f3-8be4-2f20a5aec89a/grid.xlsx` SHA:
  `cc0c7c380edfb70128e50fab49bdff3bc9d842563976b1b5ed08f44f6481a230`
- `native/source.fods` SHA:
  `438231346a34f8ecb8c025c8a409935a66ea816c531c7c6e12e133d32074cd2e`
- `native/import.json` に固定runtime/font/workerとFODS SHAを保持。
- 各条件ディレクトリにprint.xlsx、print.pdf、全ページPNG、renderer.json、vectors.json、
  candidateと全辺観測を含むreport.jsonを保存。`native/summary.json`に集計を保存。

`backend/scripts/verify_calc_grid_holdout.py` を追加した。行・列indexから各セルの四辺を予測し、
同じ固定helperが解析した実PDFの線と照合する。原本の位置やIDを画像でfitしない。
寸法読取と描画のruntime/font集合/worker identityを照合し、候補実装SHAを記録する。
観測不成立または丸め前誤差0.01px超過では証拠を保存して非ゼロ終了する。
この値は事前に置いた計算仮説の判定値であり、本番OCR/cropの許容値ではない。

| 条件 | ページ | セル | 最大整数座標差px | 最大丸め前差px | 未観測セル |
| --- | ---: | ---: | ---: | ---: | ---: |
| 69%、縦向き、横中央、左右18/22・上下19/13mm、12行で分割 | 2 | 192 | 0.497917 | 0.002575 | 0 |
| 83%、横向き、中央なし、左右7.1/13.3・上下11.7/16.2mm、7/19行で分割 | 3 | 192 | 0.479298 | 0.002887 | 0 |
| 51%、縦向き、縦横中央、左右9.2/14.1・上下12.3/18.7mm、12行で分割 | 2 | 192 | 0.487500 | 0.002543 | 0 |

全3条件・7ページ・576セル・2304辺が成立し、CLIはexit 0。
PDFスキルに従って初回生成前にoperationを記録し、全7ページの実PNGを目視確認した。
文字・罫線の欠落やページ外へのはみ出しは観察されなかった。
これは人工fixtureでの検証であり、手書き文字のcrop安全性や認識精度の保証ではない。
非表示行列は実帳票の観測と数学unitがあり、今回の独立native fixtureには含まない。
元寸法のoverflowをFODSだけで識別できない制限も残る。

```powershell
# backend。outputは未作成の専用ディレクトリを指定する。
.venv/Scripts/python.exe -m scripts.verify_calc_grid_holdout INPUT_GRID_XLSX NEW_OUTPUT_DIRECTORY --runtime 9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01
.venv/Scripts/python.exe -m pytest tests/unit/test_calc_print_grid.py tests/unit/test_calc_grid_holdout.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-calc-grid-holdout-unit-0913
```

投影の入力不変・range/IDの区別を検査する追加unitと前段を合わせて **41 passed（0.33秒）**。
対象4ファイルRuff、2ファイル限定mypy、diff check成功。
今回も本番保存経路・原本・実DBを変更せず、既存OCR contractを維持した。実API 0回。
次は新geometry modelの明示再確定/再取得/編集保存への接続であり、旧保存版へ暗黙適用しない。

接続前baselineとして、2026-09-14 00:00 JSTに
`backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py` が
**210 passed / 1 warning（49.99秒）** で終了した。実API無効。
最終コードでunit 41件、対象Ruff、限定mypyを再実行して成功した。
実native holdoutと全体回帰はいずれも終了しており、残存する実行handleはない。

## 2026-09-14 明示保存経路への接続

計算モジュールを `backend/app/domain/calc_print_grid.py` へ移し、
`services/calc_workbook_geometry.py` を追加した。新モデルは `wsl-calc-print-grid-v1`。
`PrintPreparationService` と `/prepare?geometry_model=wsl-calc-print-grid-v1` が
固定runtime IDを要求して新モデルを選択する。frontendの明示印刷確定ボタンだけが指定する。
旧モデルのGETや編集保存は移行せず、新モデルの再確定はモデル指定省略でも旧方式へ戻さない。
`PrintLayoutService._source` は保存evidenceから同モデルを復元する。
参照検証fingerprintは新adapter・旧寸法adapter・Calc計算・page plannerの実装SHAを含む。
新geometry候補は版の保存印刷範囲を維持し、旧canonical layout/configの意味づけを変更しない。
資格発行、ACTIVE/受付gate、provider/model/prompt/schemaには変更を加えていない。

新しいno-op編集保存テストの初回はbindings不一致で失敗した。
共通 `test_print_layout_api.print_template` fixtureがconfigへmappingを直接注入し、
layout relationを作成していないことを確認した。既存fixture/期待値は変更せず、
新テスト内で正式なauthoring APIから同じ意味づけを保存してbaselineを成立させた。
その後、明示prepare・GET・no-op authoringでcell ID/sourceRange/rectangle/bindingsの
全件一致、printFiles/transform保持、stale revisionと失敗時の状態不変を確認した。
空bindingsへの期待値変更や比較項目削除は行っていない。

新API・adapter・Calc計算・旧plannerの対象検証は **75 passed（5.42秒）**。
直前の旧prepare/pinned native/geometry/planner検証は **96 passed（78.65秒）**。
新経路の実帳票native再確定・新fingerprintでの資格再検証は未実施。
既存native holdoutは計算式の証拠であり、新保存経路のE2E証拠ではない。
元寸法overflowの独立検証、正式な環境資格、本番許容値、実入力ink clearanceは未完了。

接続後のOCR contractは **210 passed / 1 warning（48.31秒）**。
frontend fullは **35 files / 373 passed（17.78秒）**、build成功（chunk size警告あり）。
対象13ファイルRuff、8 production files限定mypy、`git diff --check` は成功。
backend fullは `.pt-calc-connected-full-0914` で
**2022 passed / 1 skipped / 3 warnings（2097.73秒）**、exit 0で終了した。
skipはOpenAI opt-in test、warningsは既存openpyxl `Font.copy` deprecation。
この全体回帰の収集後に追加したprivate-copy CLIテスト11件は、下記の別実行結果として扱う。
実OpenAI **0/100回**、Google **0回**。新経路の実OCR認識指標は未測定。

## 接続済み計算経路の実帳票リプレイ

`backend/scripts/inspect_calc_plan.py` を追加し、同じprivate DBをSQLite `mode=ro` / 
`query_only=ON`で開いて実行した。通常の `PrintLayoutService._source` で原本・canonical
セル対応・config mappingを検証し、新adapterと`page_plan`へ渡す。
保存FODS寸法への新model指定はメモリ上の比較候補だけであり、版へ書き戻さない。
元qualificationの現在有効性を宣言せず、所有artifact graphとSHAを照合して
`load_prepared_geometry`から全辺観測を再計算した。同じprobe-vectorとPNGから新候補を測定した。

4ページ・60/51/37/3領域で最大誤差は **0.485417 / 0.501591 / 0.480758 / 0.410417px**。
全151領域のcell ID/sourceRange/bindings、観測罫線segmentIndicesが保存観測と一致した。
倍率は0.69のまま。事前の独立計算と同じ数値が、接続済みのsource→adapter→plannerでも得られた。
初回のinline診断は保存plan全体をrequest validatorへ渡したためextra-field拒否で終了した。
製品validatorは変更せず、CLIでは製品prepareと同じ明示設定項目だけを渡して修正した。

```powershell
# backend。readonly診断であり、Office/OpenAIを起動しない。
.venv/Scripts/python.exe -m scripts.inspect_calc_plan PRIVATE_DATA_DIRECTORY VERSION_ID
```

最終CLIはexit 0、Ruffと限定mypy成功。全体pytestの開始後に追加した独立診断であり、
全体pytestに含まれる検証とは区別する。原本・DB・印刷成果物の変更、native描画、実API呼出しは0。
新modelの実prepare、保存後の新qualification、本番有効化/実OCR成功の証拠ではない。

## 実prepare比較CLIの準備

既存 `scripts/dry_run_print_preparation.py` に任意の
`--geometry-model wsl-calc-print-grid-v1` を追加した。省略時の完全不変比較は維持する。
新指定時は測定済み旧WSL版を要求し、変換前に同じ保存寸法から新座標を予測する。
実prepare後に全field（新座標含む）・transform・倍率が予測と一致することと、
全非geometry metadata・ページ範囲・意味づけ・canonical layout・旧参照PNGが不変であることを
分けて検査する。保存後GETも完全一致を要求する。原本DBはreadonlyのまま、コピーだけを変更する。

予測/identity unit 9件と、変換器stubによるprivate-copy成功・失敗2件の計 **11 passed（3.05秒）**。
初回はunit/integrationの同名test moduleによる収集衝突を修正した。
次の2件は実行中fullが保持する共有renderer mutexにより開始前に拒否された。
native import/renderが明示stubである当該テストだけ、実mutex関数へ固有のLocal名を渡して
分離した。製品のGlobal mutexや実native試験の排他条件は変更していない。
この11件は実Office成功や資格の証明ではない。開始済みfullの収集対象にも含まれない。

## 新モデルの実prepare・保存後照合

2026-09-14 00:54 JST、backend fullの終了を確認した後、固定WSL runtimeを使い、
旧モデルの検証コピーから別のprivate copyを作成して実prepareした。
`dry_run_print_preparation --geometry-model wsl-calc-print-grid-v1` は
**exit 0（46.229秒）**。元DBはreadonlyで開き、本番DBには接続していない。

- 入力コピー: `%TEMP%/llmocr-prepare-copy-x5_l5t3k`
- 出力コピー: `%TEMP%/llmocr-prepare-copy-l7um7_0d`
- Version: `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`、DRAFTを維持
- 更新前revision: `7648ae168127a4029875094e419f0469de490fb6e6f9fb8f1256ae5eb5d69d4b`
- 更新後revision: `191fc63a83ca8a43d2ea66ae03d116d38a75321e69bb61e3f1a7582425a65fac`
- 新print artifact generation: `0fc2a5fbaaaa4d44956e920e9c916e9c`

4ページ範囲、全151fieldの新座標と予測、全非geometry metadata・意味づけ・physical identity、
canonical layout、倍率・transform、保存後GETの一致、元入力artifactの不変を全て確認した。
全4ページPNGのSHAも旧生成物と一致した。新PNGを全ページ目視し、明らかな欠落・切れ・
重なりは見られなかった。これは全glyphの機械検査やcrop安全性の証明ではない。
原本・本番DB・過去job・旧印刷ファイルを置換していない。OpenAI/Google呼出しは0回。

固定runtime SHAは `9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01`、
worker SHAは `4ef76e57fe0002aa44d4479597dfdfc8ba6952a7174599c98c0fb1e8620264e7`、
578 font files / 665977778 bytesの集合SHAは
`47071a85470e130419c56f116d4294978ba536e78f30ac164f860f5a32786571` と一致した。
`productionQualified=false`のままであり、実prepare成功を資格・ACTIVE化・実OCR成功とは扱わない。
新revisionに対する参照qualificationも同じ出力コピーで実行し、exit 0で終了した。
保存直後のstatus再計算との完全一致、DRAFT/layout/printLayout/revisionと入力SHAの不変を確認した。

- qualification generation: `065a69c7e1324762a290cc5b1ee22762`
- report SHA: `54b077d9ec465ead8cf589fea3b0a9a2b17202cabb77b5c389388ba243f22b31`
- fingerprint: `b113ee7643d0749d7e9a20c7ba9e17fc4207ba283c731a8d134644c157196db6`
- 保存PDF SHA: `780eb36358cad2597ed1867809bbd25c7be8199a6eb3a96b4bc6ed034d9db0c7`

新描画・独立測定コピー・全字形検査を経た4ページの全151fieldは `MEASURED`。
最大誤差は **0.485417 / 0.501591 / 0.480758 / 0.410417px** で、事前のreadonly予測と一致した。
3515 text-instruction glyphs / 640 distinct font codesが一致し、宣言faceとの照合も成立した。
字形一致は文字の全可視性や実手書きclearanceの証明ではない。

判定は `qualified=false`。codesは `REFERENCE_ANCHORS_INSUFFICIENT`、blockersは
`WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED` / `GEOMETRY_TOLERANCE_UNQUALIFIED`。
旧anchor診断の削除やthreshold変更を行っていない。計算式・保存経路・実描画の改善は確認できたが、
本番資格、ACTIVE化、実provider→review→出力の成功は未検証のままである。

## 新保存座標と実スキャン8枚の照合

参照qualification完走後、同じ新コピーへ `scripts.inspect_print_scans` を実行した。
未記入1〜4面・記入済み1〜4面を以前目視確認した明示bindingで指定した。
全8枚が `ALIGNED_DIAGNOSTIC_ONLY`、原本/参照/版状態の前後一致、外部API 0回。
新baselineは `%TEMP%/llmocr-scan-baseline-zrbb0l6u/report.json`、SHAは
`313597cac6f54dc15dc5c76dafe1b619d59fa38ce9cc63c0110f17fdcfc00ca8`。

旧baseline `llmocr-scan-baseline-pjo23fxk` と入力SHA・明示pageで対応を取り、
全302 region IDと領域件数を照合した。参照画像、位置合わせ後PNG、最終homographyは8枚全て一致。
新モデルの座標から生成したcrop bboxは294/302件が変わった（片組あたり60/50/34/3件）。
X位置の最大差2px、Y位置・幅・高さは最大各1px。新prepareの計算済みgeometry変更に伴う差であり、
crop画素を旧方式と不変とは報告しない。記入済み4面のcrop overlayも全ページ目視した。
provider association、値、補正・出力への対応を実APIで検証したわけではない。

`scripts.inspect_scan_clearance` を同じblank/filledの明示SHA組で再実行し、exit 0。
結果は `%TEMP%/llmocr-scan-clearance-nhdhtqqv/report.json`、SHAは
`1f6e1a1a7578c365d80d163637aec5992d5b2eaccf1a957fcd195ff45ab6d090`。
151領域中、crop端2px以内の暗い差分候補は31件（6/14/9/2）だった。
全151領域に印刷画素近傍のunknownが残る。候補は印刷残差/ノイズを含むため、
31件を手書き欠落件数、残り120件を安全と解釈しない。全件 `inkClearanceVerified=false`。
旧候補23件と新31件は異なるcrop座標での診断であり、認識精度の改善/悪化率ではない。

この区間の製品コード変更はなく、実prepare・資格/scan診断と本記録の更新を行った。
OpenAIは作業全体 **0/100回**、Google **0回**。認識誤り・association mismatch・schema errorは未測定。
既存OCR contractを維持したが、新paged OCR安全性を完全には検証できていない。
正式な環境資格、geometry/可視性の本番判定、送信前の実筆画検証、有効化・実OCR・画面取得E2Eが残る。
