# 欄別の描画寄与検査 — 2026-09-13

## 結論

テンプレート有効化は未完了。長い罫線の欄外部分の未観測を、各欄自身の描画欠落と
扱う問題を限定修正した。実帳票のページ2はFAILからMEASUREDへ変わったが、ページ1の
測定線と画素の対応に別の未解決条件が残る。MEASUREDも環境・許容値・本番資格ではない。

## 原因と変更境界

`prepared_reference_geometry._observations` は元のvector/raster/differentialを保持したまま、
新しい `reference_field_raster.observe_field_ink` で明示fieldの各辺の範囲を検査する。
長い破線が複数欄を横断する場合、他の欄の交点まで当該欄の必要条件に含めていた。
実原画像で該当した56個のfield/edge組合せすべてで、描画差分のないcomponentは
当該欄の既存guard内側区間の外だった。欄名や固定座標による例外は加えていない。

補足を許可するのは `UNVERIFIABLE/NO_INTERIOR_PAINT_SAMPLES` で、完全なfresh差分証拠に
subpixel描画寄与があり、実際のpaint componentが当該欄内に存在し、その区間に未観測
componentがない場合だけ。SAMPLEDの欠落、未対応path、差分欠落/重複/不完全/ゼロ、
空の内部区間は救済しない。同じ線でもfieldごとに検査し、mapping bindings不一致は拒否する。
作業量上限も既存MAX_SAMPLES以下に制限する。

端点guardは既存の `max(2px, strokeWidth/2+1)` のまま。画素閾値、contrast、許容誤差、
crop/padding、文字・座標・mappingを変更しない。元の線全体の未検証結果は消さない。
保存/GETは所有された入力証拠から再計算し、保存booleanを信用しない。
資格fingerprintに新モジュールを含め、実装変更による失効を検査する。

## 同じ実入力による比較

検証用DB: `llmocr-prepare-copy-x5_l5t3k/app.db`、版
`6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`。ライブDBではない。
plan SHA: `7595cf111b328b1e4433075f6f612d91a73a22f154200cd211b39d4d64a1f51a`。
元PDF SHA: `ffa3992302a1d765bdad9b210abf66c8311b149e43d415bcdf3a6236e2be522d`。
未観測辺のみの測定PDF SHA: `71e4864a8aa8e4b989747a9c9255d00eb5038f52059697c107c0d369eab7b3ff`。
native code bundle SHA: `c8bdf20a7c73996e4a038dd417481f2a81b0a205b36bdbfd9ad916cb94c77a14`。
元vectorは `llmocr-stroke-differential-z5io1dwm/vectors.json`、測定vectorは
`llmocr-stroke-differential-mwnp4y51/vectors.json`。一時正規化実験のvectorは流用していない。
後者のSHAは `fa88efccc848c4c97ff77ce6e646ba339ae9617e9adbd3820bb1fa30c54916dd`。

`assess_geometry` の前後で同じPDF/PNG/vector/planを使用し、原本から測定XLSXのbyte再生成、
環境・text program・font・page geometry、測定線外の画素差分0を検査した。

| ページ | 欄数 | 修正前 | 修正後 | 最大座標差px |
| --- | ---: | --- | --- | ---: |
| 1 | 60 | FAIL | FAIL | 1.65625 |
| 2 | 51 | FAIL | MEASURED | 1.44840879265 |
| 3 | 37 | MEASURED | MEASURED | 1.39007545932 |
| 4 | 3 | MEASURED | MEASURED | 0.79583333333 |

ページ1の残存理由は `REFERENCE_RASTER_INK_UNVERIFIED`。測定segment 7の投影中心は
x=909.61875、幅0.2083125px、y=234.709925..264.089091。
既存のnormalサンプル列909では25点とも白、隣列910では25点とも元の白から赤(255,0,0)へ
変わっていた。隣接列を探索して成功へ変更せず、検査位置と描画時の丸めの不一致を
次の調査対象とする。現在のコードはこの線を未検証のまま保持する。

## 実際の保存・再取得

検証用DBに対する `ReferenceQualificationService.qualify` を実WSLで再実行し、
保存後の `status` と応答全体が一致した。版status/layout/plan/revision、原本、
config、印刷XLSX/PDF/report、全ページPNGの内容SHAは不変だった。DRAFTを維持した。
今回の資格記録はsource job所有の `qualification/ac9df59eb17546c78c7ff75c9276153c/report.json`、
SHA `388c458c8c5c5ce759af907bbe384f22a4d80feb28010826107ba98486af4cfe`。
古い診断PDFを保存済みPDFに見せかけず、私有コピーの実保存PDFに対してnative検査を再実行した。

fieldGeometryは上表と同じ。字形検査は延べ3515字・640個のfont/code組でPASS、
全実faceが宣言faceに一致し、再描画も一致した。一方、従来anchor診断の
`REFERENCE_ANCHORS_INSUFFICIENT` とページ1のraster未検証は維持した。
本番blockerは `WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED` と
`GEOMETRY_TOLERANCE_UNQUALIFIED`。qualified=falseを維持し、ライブDBは更新していない。

## 検証

```powershell
# backend
.venv/Scripts/python.exe -m pytest tests/integration/test_prepared_reference_geometry_api.py tests/unit/test_prepared_reference_geometry.py tests/unit/test_prepared_stroke_differential.py tests/unit/test_reference_field_raster.py tests/unit/test_reference_stroke_differential.py tests/unit/test_prepared_measurement_selection.py tests/unit/test_reference_measurement_selection.py tests/unit/test_measurement_selection_diagnostic.py -q -o addopts='' -p no:cacheprovider --basetemp=.pt-field-scope-final-0913
.venv/Scripts/python.exe -m mypy app/services/reference_field_raster.py app/services/prepared_reference_geometry.py --follow-imports=silent
```

- 対象unit/integration: **214 passed（21.61秒）**。
  保存summary改変からの再計算、実装変更失効、欄内/欄外・共有辺・差分欠落を含む。
- 限定mypy2モジュールとRuffは成功。全体型検査の成功とは扱わない。
- 接続前の新テストはfieldRasterObservation欠落で1 failed/8 passed。その後の対象199件は成功。
- 縦/横・正方向/逆方向の追加4ケースを含む欄別unitは **23 passed（0.14秒）**。
- 変更前OCR contractは210 passed（43.25秒）、変更後も **210 passed / 1 warning（43.63秒）**。
  コマンドはrootで `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、実API無効。
- 検証用に保存した測定PNG全4ページを目視確認した。目視をcrop/文字可視性の資格には流用しない。
- 実WSL helper/保存資格integration `test_wsl_vector_helper.py` と
  `test_wsl_glyph_qualification.py`: **3 passed（166.93秒）**。
- 先行全体1810 passedはこの欄別修正を含まない。frontendは今回変更していない。

上記native完走後、他のnative実行を止めた状態で全体回帰を開始した。
全体回帰は **1874 passed / 1 skipped / 3 warnings（2136.18秒）**、終了コード0で完走した。
これは欄別観測の接続後、Splash校正のhelper/資格経路への接続前の実装に対する結果である。
後から追加した未接続モジュールの検証・本番認定を、この結果だけで代替しない。

```powershell
$env:RUN_OPENAI_INTEGRATION_TESTS='false'
.venv/Scripts/python.exe -m pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-field-scope-full-0913
```

原本・保存印刷成果物・実DB・provider/model/prompt/schema・既存crop・補正/確定値は変更しない。
既存OCR contractを維持した。実OpenAIは作業全体0/100回、Google 0回。
実手書きの認識精度/誤対応率、本番有効化、実カード取得はまだ未検証。
