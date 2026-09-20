# Calc座標の整数PDF出力と残差

## 結論と変更範囲

実帳票4ページ604辺と独立holdout7ページ2304辺の残差は、Calcの整数グリッドから
PDFの整数point出力を再現することで全件説明できた。画像fitや個別帳票のoffsetは不要だった。
保存矩形の約0.5pxの差をそのまま未知のレイアウトずれと解釈せず、表現形式の丸めと区別する。

`backend/app/domain/calc_pdf_coordinates.py` に整数/有理数の計算モデルと表現誤差の上限を追加し、
`backend/tests/unit/test_calc_pdf_coordinates.py` に回帰試験を追加した。
この初期段階では診断・unitだけが利用し、本番の資格判定・保存・crop処理には接続していない。
後続の参照検証への接続は末尾に記録する。
閾値の緩和、座標やmappingの変更、有効化gateの解除は行っていない。

## 一次ソースと式

固定runtimeと同じLibreOffice 7.3.7.2 tagの
[PDFWriter](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/vcl/source/gdi/pdfwriter_impl.cxx)
の `nLog10Divisor` / `appendPoint` / `pointToPixel` / `appendFixedInt` と、
[座標変換](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/vcl/source/outdev/map.cxx)
の `LogicToLogic(Point)` / `fn5` を照合した。

page-relativeの未拡縮整数mm100座標をx,y、整数印刷倍率をz%、ページ高をH ptとする。
整数PDF座標（単位0.001pt）は次のとおり。

```
factor = z * 72 * 1000 / (100 * 2540)
X = nearest_away_from_zero(x * factor)
Y = trunc(H * 1000) - nearest_away_from_zero(y * factor)
```

PDF座標を画像の左上原点へ戻すと、YにHの切捨て残差が現れる。
これは旧X/Y画像合わせの補正ではなく、固定rendererの数値出力の再現である。
この式をB2DPolygon/矩形命令/任意CTM/UserUnit/回転の経路へ一般化していない。

整数point変換の誤差は各軸最大0.0005pt、正のページ高の切捨てはYだけ0.001pt未満。
通常の保存矩形辺の整数丸めは最大0.5px。したがって、この出力経路に限った合計上限は
Xで `0.5 + dpi/144000` px、Yで `0.5 + 3*dpi/144000` px未満となる。
150dpiではそれぞれ0.5010417px以下・0.503125px未満。
この値は実測最大値へのfitではなく単位変換から導いた表現誤差であり、OCR/crop許容値ではない。
幅/高さの丸めが0になったときのplannerのmin=1拡張は別条件なので、この0.5px説明に含めない。

## 同一保存証拠での比較

診断スクリプトは
`C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/inspect_pdf_coordinate_precision.py`。
DBはSQLite `mode=ro` / `query_only=ON`。元PDFを再解析・再描画せず、保存された観測と
整数グリッド候補を読み取る。private版は所有report/vector SHAを確認し、page/field inventory・
sourceRange/bindingsと整数倍率を照合する。入力は読取前後で内容不変を確認した。
数値同一性の比較幅1e-9ptは新しいgeometry許容値ではない。

| 入力 | ページ | 比較辺 | 不一致 | 再現値との差の最大pt |
| --- | ---: | ---: | ---: | ---: |
| 独立holdout、横中央69% | 2 | 768 | 0 | 5.69e-14 |
| 独立holdout、非対称余白83% | 3 | 768 | 0 | 2.85e-14 |
| 独立holdout、縦横中央51% | 2 | 768 | 0 | 5.69e-14 |
| 実帳票の最終privateコピー | 4 | 604 | 0 | 1.14e-13 |

holdout入力は `llmocr-calc-grid-c47d1ca0b3cc46bd81223ea6bbca81fb/native` 配下。
report SHAは条件順に
`0b66d3bac7a2b55c481a37edae23be2b3bb72792f99de0ab5669344b3e012d7f`、
`7957ea4ec42edba5d6effe54a300314da9525194bc21b174f796fe09baa34e76`、
`92284e85935c0d9d82c0ed74c9a7f3fc9970ccaebe5f199af13a486c6d73d3c9`。
実帳票は `llmocr-prepare-copy-vnxpvohr`、版 `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`。
report SHA `8e397cb8d1b26cd8da0603179b3d7fb3d84ffc9f442de45e89d588d4aa4a4f59`、
probe-vectors SHA `54e5a802381a42d3a510259d68290e82aa84e0d37522bacf460f80a5f8525d7e`。

最初のinline Python実行はPowerShellの引用符解釈によりSyntaxErrorとなった。
専用スクリプトへ分離して再実行した。JSONのq/Qを区別する必要があり、PowerShell側の
初回ConvertFrom-Jsonも失敗したため-AsHashtableで再読込した。いずれも入力を変更していない。

## 検証と残条件

backendで以下を実行する。

```
.venv/Scripts/python.exe -m pytest tests/unit/test_calc_pdf_coordinates.py tests/unit/test_calc_print_grid.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-calc-pdf-precision-final-0914
.venv/Scripts/python.exe -m ruff check app/domain/calc_pdf_coordinates.py tests/unit/test_calc_pdf_coordinates.py
.venv/Scripts/python.exe -m mypy app/domain/calc_pdf_coordinates.py --follow-imports=silent
```

整数倍率10〜100%、正負の全127分母位相、96/150/300dpi、整数/端数ページ高、
半数丸め、入力型/範囲/中間値overflow拒否を対象とする。
先行の全体回帰2094件はこの新モジュール追加前であり、今回の全体回帰とは扱わない。
最終対象試験は **72 passed（3.25秒）**、対象Ruffと1モジュールのMypyは成功。
backend full・OCR契約スクリプト・frontend試験はこの計算単体の追加では再実行していない。

既存OCR contractを維持した。新モジュールは本番経路から未参照であり、元layout・
既存plan・受付snapshot・provider/prompt/model・association・cropに変更はない。
環境認定、文字/罫線の可視性、実入力の位置合わせと筆画安全性、基準点診断との統合、
正式な資格発行・有効化・実OCR/E2Eは未完了。OpenAI 0/100、Google 0。

## 保存された参照検証への接続

`services/calc_reference_precision.py` を追加した。保存済みCalcモデルにだけ適用し、
検証済みの寸法evidence・ページ範囲・fieldの開始/終了行列から整数グリッドを再計算する。
観測値に近づくよう原点や倍率を推定せず、浮動小数点の保存座標からグリッドを逆算もしない。
四辺の通常の整数丸めと保存rectangleの一致、およびPDFの整数point出力予測と各観測辺の
数値同一性を別々に検査する。min=1で拡張された極小extentは別理由で未検証にする。

`prepared_reference_geometry._observations` / `assess_geometry` へ接続した。
元PDFまたは検証済み測定コピーの所有vector/rasterから再計算し、`coordinatePrecision` を返す。
保存・GETの両方で同じ検査を実行する。曖昧/未観測/ずれ/保存丸め不一致では
`REFERENCE_PDF_COORDINATES_UNVERIFIED` を追加し、旧診断codesを削除しない。
旧geometry modelには新属性を後付けしない。`MATCH` は数値対応の一致であって資格ではなく、
`thresholdQualified` / `visibilityVerified` / `productionQualified` と本番gateは従来どおり維持する。

参照qualificationのfingerprintへ計算モデル・照合実装を追加した。コード変更時は旧資格結果を
失効させ、保存済みbooleanやsummaryだけで新検証の成功を補完しない。
この判定は版所有のsource/plan/PDF evidenceを使用するため、frontendの表示変更だけでは実現できない。
provider/model/prompt/schema・実crop・旧単一ページ・ユーザー意味づけ・原本は変更していない。

変更前OCR契約は210 passed / 1 warning（63.40秒）。初回のbackend/scripts指定は存在せず失敗し、
`rg --files` で確認したrepository-rootの `scripts/verify_ocr_contract.py` を実行し直した。
接続対象unitは112 passed（3.94秒）。所有保存/再読込、summary改変、失敗保持、実装SHA変更時の
資格失効を追加して検証する。対象Ruffと3サービスのMypyは成功。

最終privateコピーの読み取り専用DBで `_prepared` による原本・保存plan照合後、
`load_prepared_geometry` で所有artifact全体から再計算した。全4ページ・151欄は
`coordinatePrecision.status=MATCH`、既存欄別診断も `MEASURED`。版・layout・quality_jsonは不変。
これは旧fingerprintを新資格として受理した操作ではなく、診断的な再計算である。
Office/OpenAI/Googleを起動せず、資格recordの書換えも行っていない。

接続後の所有保存/再取得・資格失効を含む対象試験は62 passed（33.58秒）。
変更後OCR契約は210 passed / 1 warning（63.20秒）。これらの終了を確認してから、
backendで `RUN_OPENAI_INTEGRATION_TESTS=false`、
`pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-calc-precision-full-0914`
による全体回帰を開始した。全体試験の終了までは今回のbackend fullをPASSと報告しない。
全体回帰中は製品コードを固定し、別のnative rendererを同時起動しない。

この全体回帰は終了コード0、**2157 passed, 1 skipped, 3 warnings（2087.95秒）**で終了した。
警告は既存のSpreadsheet取込テストの非推奨font.copy呼出し3件。
ダンプ抑止処理の製品反映前の結果であり、以後のrenderer変更の回帰結果としては流用しない。
