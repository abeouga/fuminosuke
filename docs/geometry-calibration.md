# XLSX / LibreOffice Geometry Calibration — 2026-09-08

後続の[Environment Qualification](renderer-environment-qualification.md)でWindows Job Object、
環境identity、96/150/300dpiを検証した。本番環境は未認定のまま。以下の校正記録は保持する。

新規printLayoutへ`lo-import-twip-v1`を採用した。画像への後付けX/Y倍率・offset補正は追加していない。26個の人工XLSXを150dpiで実測し、validation 6件の最大誤差はX 0.739px、Y 1.055px。旧近似のX 34.452px、Y 15.625pxから改善した。geometry calibrationの範囲を完了したが、**本番qualifiedは発行していない**。

正本は[dataset.json](reference-renderer-evidence/calibration/dataset.json)、[analysis.json](reference-renderer-evidence/calibration/analysis.json)。各fixtureに元の人工XLSX、Office PNG、measurementを保存。実ユーザー帳票の内容値は含めていない。

## 1–3. HEAD・worktree・baseline

開始/終了HEAD: `d33e192cb4483fe3a2a857a2b5f51f046a8f8fbe`、main、単一worktree。開始時tracked変更18ファイル、untracked31項目（directory集約あり）。前Phaseの未コミット実装を保持しており、HEADとの差分全体を今回の変更とは扱わない。

AGENTS.md、Safety Contract、print layout文書、直前qualification報告、audit.json、bounds.json、現在の実装・テストを確認した。変更前の専用OCR contractは194件、print layout / qualification / job pages関連は51件成功。今回の変更前backend全体実行はしていない。migration追加・適用、実DB更新なし。

## 4–9. 原因・検出器・仕様・font・行高

**仕様で確認したこと:** OOXML列幅はNormal fontの最大数字幅が基準で、pointそのものではない。明示widthの符号化にはpaddingが含まれ、baseColWidthとdefaultColWidthは意味が異なる。[Microsoft Column資料](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.column?view=openxml-3.0.1)のpixel式を候補Bで検証した。ただしBはMDW=7px固定の対照で、全fontのExcel挙動を再現したものではない。

**renderer一次情報:** 実行版はLibreOffice 26.2.3.2、build `70e089b17412e4cb7773e41413306b17a2328c34`。同版の[UnitConverter](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/sc/source/filter/oox/unitconverter.cxx)、[WorksheetHelper](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/sc/source/filter/oox/worksheethelper.cxx)、[StylesBuffer](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/sc/source/filter/oox/stylesbuffer.cxx)、[SheetDataContext](https://github.com/LibreOffice/core/blob/libreoffice-26.2.3.2/sc/source/filter/oox/sheetdatacontext.cxx)を確認した。Normalのstyle XFが指すfontを利用し、reference deviceから数字幅を得る。明示列幅は数字幅との積を整数twipへ丸める。baseColWidthには5 screen pixel相当を加えるが、明示widthには再加算しない。

**実測:** 独立UNO ReferenceDeviceで数字0–9を測り、11ptの最大数字幅はCalibri 111twip、Arial 122、Meiryo 137、Yu Gothic 122だった。Aptos指定では122が返ったが、hostに実体がなく、実際の代替fontは不明。CへAptosの値を流用していない。Calibri14もCの未測定条件とした。

Calibri11・width=8.43の旧計算は`(8.43*7+5)*0.75=48.0075pt`。Officeは`round(8.43*111)/20=46.8pt`。差1.2075ptは150dpiで**毎列2.515625px、3列で7.546875px**。raw境界の旧最大7.674pxと整合する。同じPNGを既存二値検出器で測る回帰テストでも旧FAIL、新モデル2px以内を確認した。

default列は別の誤差になる。baseColWidth=8のOffice寸法は48.15pt、旧近似は45.75pt。5列全体の旧累積誤差は約25px。前報告の約5pxは±8px探索窓内で照合できたセルに限られ、ページ全体の誤差上限ではなかった。

raw検出器は予測位置から探索せず画像全体の罫線帯を抽出し、既知fixtureの線数と一致しなければUNMEASURABLEにする。X/Y別にpredicted/actual/signed/absolute、先頭offset、各区間差、終端累積driftを保存。1/2/3px幅、100/100.25/100.5px位置、kernel 1/9/15、X/Yの54条件で、既知coverageを積分した人工線の検出最大誤差は**0.000981px**。理想線の校正であり、実PDF全般の不確かさ上限ではない。従来検出器の2px閾値・±8px窓・anchor条件は変更していない。

Normal取得は元styles.xmlの`cellStyles(builtinId=0) → cellStyleXfs(xfId) → fonts(fontId)`をたどる。fonts[0]固定ではないことをテストした。Calibri/Arial/Meiryo/Yu Gothic/Aptosの既知Windowsファイルの実体family・SHAを確認する。このhostでは前4種あり、Aptosなし。未確認fontの新規プラン作成はunsupported。font追加・repoへのfont格納なし。ファイル存在はOfficeの実選択fontの完全な証明ではなく、`fallbackVerified=false`を保持する。

Yには独立した原因がある。OfficeのMSO文書読込条件では明示行高を0.75pt単位へ切り下げる。10→9.75、20→19.5、25→24.75、17.35→17.25ptを確認。旧行高を残すCは列方向だけ改善し、validationのY最大15.625pxを残す。Dは読込後の行高も取得する。producer-hintはdocPropsのApplicationだけをLibreOfficeへ変更した人工対照で、13.75ptが保持された。**本物のLibreOffice作成XLSXとExcel実機保存XLSXの比較ではない**。producer条件を自作の単一丸め式へ一般化していない。

## 10–13. 余白・中央配置・倍率・origin

以下は採用Dのraw境界測定、単位px。

| fixture | 先頭X offset | 先頭Y offset | 最大X | 最大Y |
| --- | ---: | ---: | ---: | ---: |
| margins（左23/右7/上18/下12mm） | -0.422 | -0.682 | 0.596 | 0.921 |
| centering（同余白、縦横中央） | -0.421 | -0.393 | 0.421 | 1.134 |
| origin（D7開始） | -0.535 | -0.558 | 0.535 | 0.931 |

print areaをB2からD7へ移しても同一寸法の物理境界が一致した。原点を引いてから共通倍率、余白/中央offsetの順で変換する既存page transformを維持。右下配置holdoutでもX最大0.306、Y最大0.870px。単純結合fixtureはX最大0.535、Y最大0.931pxで、結合による列幅変化は見られない。

| 保存倍率 | 全幅からの実測X倍率 | 全高からの実測Y倍率 |
| --- | ---: | ---: |
| 100% | 1.001395 | 0.999320 |
| 95% | 0.950550 | 0.949209 |
| 90% | 0.900454 | 0.899743 |
| 80% | 0.800279 | 0.799717 |
| 70% | 0.700594 | 0.699666 |

比率は端点のraster誤差を含む診断値で、補正には利用しない。新モデルの終端累積driftはvalidationでX最大0.407、Y最大0.418px。残差はpixel中心・罫線raster化・twip丸めに整合するが、各寄与の厳密な分離は未完了。X/Y独立scaleやglobal translationを追加して残差を消していない。

## 14–17. fixture・分布

初期calibration 17件、未使用組合せのvalidation 5件を分離した後、採用Dを固定して右下holdout 1件と追加calibration 3件を測った。最終20 calibration / 6 validation。下記統計は全件。Aptosも診断値として含むが、本番適用可能fontではない。CはAptos/Calibri14を除くためcalibration母集団が異なる。

- calibration: default、explicit843、width-matrix（5/8.43/10/12/15/20/30）、height-matrix（10/12/15/18/20/25/30/17.35）、many-columns、scale-95/90/80/70、margins、centering、origin、font-Arial/Meiryo/YuGothic/Aptos、font-size（Calibri14）、defaultColWidth、column-flags、producer-hint。
- validation: holdout-mixed、holdout-many、holdout-bottom、holdout-center、holdout-merged、holdout-right-bottom。小数列幅、混合行高、多数列/行、非A1原点、中央配置、結合、右下配置を含む。完全な入力と境界はdatasetのdefinition/modelsへ保存。

統計表は末尾。符号はactual−predicted。mean/median/p95/maxは全境界の絶対誤差をpoolした値、終端driftは先頭誤差を引いた終端誤差の絶対値の最大。fixture平均ではない。

## 18–24. 採用・version・gate・実帳票

**D: Office読込直後のsource寸法を採用。** 一時コピーを既存Office adapterでFODSへ変換しcolumn-width/row-heightを取得する。FODSの0.001cm丸めの半量子は0.2835twipで、元の整数twipへ戻せる。画像/PDF罫線/OCR結果を寸法の入力に使わない。print scale前の値であり、既存uniform page transformと重複しない。実画像は独立検証対象。

Aはfont/明示widthの意味を失う。Bは固定7pxの仕様式がOfficeのreference deviceと一致しない。Cは計測済みfont/サイズに限定され、producer依存行高を解決しない。DはOffice読込挙動を取得でき、未使用組合せでも改善した。独立profileでのexplicit843/holdout-center再renderはPNG bytesとrenderer identityが一致。他Office版での保証ではない。

`geometryModelVersion=lo-import-twip-v1`と寸法・font SHA・source SHA・renderer identityを保存printLayoutへ記録。revisionとqualification fingerprintにも現在のgeometry implementation versionを加え、旧reportを現在のvalid資格として扱わない。保存寸法を再利用し、原本/font不一致は409。新規未保存プランの取得/候補生成にはOffice変換が追加され、依存と待ち時間が増える。旧保存プランは旧計算を保持し、legacy layout/configや受付済みjobを再計算しない。

2px暫定budgetは変更しない。今回150dpi分布は収まるが、実罫線・他DPI・環境を含む本番閾値認定には不足。96/300dpiは未測定。既存画像合わせ補正と印刷倍率を統合していない。

本番gateは閉鎖。thresholdQualified/productionQualifiedはfalse。OS資源隔離、network isolation、環境fingerprint完全性、実帳票検証は未解決。実XLSX 5冊のSHAは前auditと全件一致。unsupported要素・20,000セル制限を解除せず、元XLSXと出力XLSXの動作は変更していない。

## 25–32. 変更・検証

追加: `backend/app/services/workbook_geometry.py`、`geometry_calibration.py`、`backend/scripts/calibrate_geometry.py`、`summarize_geometry.py`、`backend/tests/unit/test_geometry_calibration.py`、`backend/tests/integration/test_geometry_version_boundary.py`、本報告・calibration evidence。

前Phase未追跡ファイルを更新: `office_reference_renderer.py`（FODS）、`print_layout_service.py`（新規source適用）、`reference_qualification_service.py`（fingerprint）、`app/domain/print_layout.py`（version/evidence）、print layout/qualification文書。frontend・provider/model/prompt/schema・既存OCR処理・DB/migration・XLSX出力を今回変更していない。

| 検証 | 結果 |
| --- | --- |
| 変更前 `scripts/verify_ocr_contract.py` | 194 passed |
| 変更前 print layout / qualification / job pages関連 | 51 passed |
| 変更後 `python -m pytest -q -o addopts='' -p no:cacheprovider` | **660 passed, 1 skipped**、290.06秒。既存skip維持 |
| full後追加の互換性2件・旧画像回帰1件を含む新規2 testファイル | **61 passed**。full収集後の3件はこの対象実行で検証 |
| print layout / qualification API | 10 passed（実Office変換を含む） |
| 変更後 `scripts/verify_ocr_contract.py` | **194 passed** |
| frontend関連4 testファイル | **35 passed** |
| frontend build | 成功。既存bundle size warningあり |
| Ruff（今回の10 Pythonファイル） | 新規違反0 |
| `python -m mypy app` | 36 errors / 6 files。今回変更モジュールの新規0、既存エラー未修正 |
| `git diff --check` | 成功 |

backendの`.venv/Scripts/python.exe`、frontendの既存npm scriptsを使用。Office行列は`python scripts/calibrate_geometry.py --output ../docs/reference-renderer-evidence/calibration`、保存結果再集計は`python scripts/summarize_geometry.py`。前者は人工データの一時コピーだけをOfficeへ渡す。OpenAI API **0/30回**、Google API **0回**。

旧保存プランへの新計算の流入禁止、cell/range/identity保持、契約前後194件、backend全体検証を根拠に、**既存OCR contractを維持した**。新paged field座標は意図して変更するが、旧canonical矩形や既存画像合わせX/Y倍率を変更していない。実APIのOCR品質、実帳票Golden replay全体、利用者UI E2Eを今回検証した意味ではない。

## 33–35. 未解決・次Phase・Git

本番隔離、完全なrenderer/font識別、実font fallback選択、実Excel/LibreOffice保存producer比較、別DPIでのraster成分分離、実帳票の安全な高度要素対応が残る。hidden行列は現在unsupported。太字/斜体・theme font等を網羅したfont解決器は実装していない。

次Phaseはfont選択と環境固定・隔離を整備し、実帳票と異なるDPIで閾値を検証する。その根拠が揃ってからqualification認定とpaged実OCRを接続する。geometry改善だけでgateを解除しない。

未コミット差分を保持。staging・commit・pushなし。終了時tracked変更18ファイル、untracked36項目。
最終statusは[git-status.txt](reference-renderer-evidence/calibration/git-status.txt)へ記録。
検証tempだけを`C:/Users/hanam/.codex/tmp/llm-ocr-geometry-20260908-final`へ移動し、
元データや既存変更は整理・破棄していない。今回のOffice残留processはない。

## 分布の数値表（px）

| set | model | axis | mean | median | p95 | max | 終端drift最大 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| calibration | A-existing | x | 10.997 | 5.488 | 43.522 | 73.462 | 73.997 |
| calibration | A-existing | y | 1.030 | 0.702 | 3.606 | 7.999 | 7.441 |
| calibration | B-OOXML-7px | x | 14.885 | 10.168 | 44.286 | 96.946 | 97.482 |
| calibration | B-OOXML-7px | y | 1.030 | 0.702 | 3.606 | 7.999 | 7.441 |
| calibration | C-font-twip | x | 0.461 | 0.497 | 0.678 | 0.805 | 0.408 |
| calibration | C-font-twip | y | 1.064 | 0.705 | 3.647 | 7.999 | 7.441 |
| calibration | D-office-import | x | 0.463 | 0.516 | 0.677 | 0.805 | 0.408 |
| calibration | D-office-import | y | 0.709 | 0.697 | 0.974 | 1.134 | 0.741 |
| validation | A-existing | x | 8.158 | 4.246 | 29.380 | 34.452 | 33.856 |
| validation | A-existing | y | 5.768 | 5.037 | 13.520 | 15.625 | 15.001 |
| validation | B-OOXML-7px | x | 14.845 | 10.985 | 37.715 | 42.423 | 43.019 |
| validation | B-OOXML-7px | y | 5.768 | 5.037 | 13.520 | 15.625 | 15.001 |
| validation | C-font-twip | x | 0.448 | 0.526 | 0.687 | 0.739 | 0.407 |
| validation | C-font-twip | y | 5.768 | 5.037 | 13.520 | 15.625 | 15.001 |
| validation | D-office-import | x | 0.448 | 0.526 | 0.687 | 0.739 | 0.407 |
| validation | D-office-import | y | 0.728 | 0.700 | 0.994 | 1.055 | 0.418 |
