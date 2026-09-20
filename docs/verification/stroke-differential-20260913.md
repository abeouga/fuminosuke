# 細い破線の描画寄与を測定する処理

## 再現条件と変更境界

先行の実帳票4ページ151項目の検証で、page 1に1本、page 2に9本の線分が
`NO_INTERIOR_PAINT_SAMPLES` となった。0.5 pxの両側transition guardに対して
paint区間が短いためであり、印刷線の欠落を証明した結果ではない。
元のguard、ink/contrast閾値、座標、mapping、原本、印刷成果物は変更しない。

新しい処理は、glyph検証済みPDFの純粋なstroke命令1個だけを測定コピー内で抑止し、
同じ固定環境での全ページRGB差分を記録する。元のPDFやXLSXには書き込まない。
後続の塗りつぶしで線が隠れる場合、抑止してもその場所は変わらないため、
単に暗い画素があることと、その線が描画へ寄与したことを区別できる。

## 実装

- `renderer_stroke_probe_pdf.py`: 固定pypdf tokenizerで命令のbyte位置を取得する。
  文字列・コメント内の文字を検索置換しない。S/sの1 byteだけをnへ変更し、他のdecoded
  content、他ページ、用紙、文字プログラム、font resourceの不変を再検査する。
  合成fill/stroke、clip、curve、hairline、inline imageの推測変換はしない。
- `renderer_stroke_probe_pixels.py`: P6 RGBを厳密に読み、全ページXORを圧縮する。
  復元時に元/変更後/差分のSHA、寸法、画素数、展開上限、末尾余剰を照合する。
- `renderer_stroke_probe_worker.py`: 既存隔離境界内でのみPDF parseと固定Popplerを実行するための
  worker処理。最大32描画命令、20M画素、60秒の追加処理上限を持つ。
  同じページの元画像は1回だけ描画し、所有するscratchファイルだけを回収する。
- `reference_stroke_differential.py`: backend側ではPDFをparseせず、所有されたPNGの実RGBと
  helperの元RGBの一致、対象命令集合、単一変更のmanifest、全ページ差分を再検査する。
  対象命令のstroke領域外に1画素でも差があれば拒否する。画素footprint内のpaint区間と
  実差分の対応を計数し、全paint footprintに寄与がある場合だけ `OBSERVED` とする。

新観測は従来の `NO_INTERIOR_PAINT_SAMPLES` の補足に限定する。
既存の部分ink欠落、未対応cap、境界切れ、別segmentを新観測で上書きしない。
元raster診断は保持する。描画寄与の観測は、文字の可視性、geometry本番許容値、
環境資格、実スキャンのcrop安全性を認定しない。すべての資格booleanはfalseを保持する。

## 接続前の検証

独立した新規4モジュールのunitは109 passed（1.26秒）。対象Ruffと4モジュールの
限定mypyは成功。差分抑止44件に、PNG照合・回転・せん断・部分/全overpaint・改変・
別位置の変化・旧raster観測との境界65件を追加した。

```powershell
# backend
.venv/Scripts/python.exe -m pytest tests/unit/test_reference_stroke_differential.py tests/unit/test_renderer_stroke_probe_pdf.py tests/unit/test_renderer_stroke_probe_pixels.py tests/unit/test_renderer_stroke_probe_worker.py -q -o addopts='' -p no:cacheprovider
```

この時点ではproduction worker bundleと資格保存処理に未接続であり、実PDFの追加描画比較も
未実施である。先行のbackend全体テストは新モジュール追加前に開始しているため、
その結果を新しい差分処理の全体回帰成功と扱わない。
接続後に固定WSLの実行・実帳票・保存/GET再計算・OCR contract・全体回帰を確認する。

OpenAIは作業全体0/100回、Google 0回。実認識の誤読率・誤対応件数は未測定。
テンプレートはDRAFTのままで、本番有効化と全ページ実OCRの完了は未達である。

## 既存保存経路への接続と実PDF確認

先行全体検証の終了（1677 passed / 1 skipped / 3 warnings、2029.08秒）後に接続した。
固定vector bundleに3つのnative helperを追加し、glyph bundle自体は変更していない。
現在のbundle SHA、manifest、全ページRGBを検査した `strokeDifferential` を
`prepared_reference_geometry.py` の保存/GET再計算へ追加した。資格fingerprintにも
新実装のSHAを含める。古い証拠に現在の結果を後付けしない。

接続前の新規8件は6 failed / 2 passed。接続後は既存関連を含め208 passed（2.01秒）。
bundle内容の完全一致assertionは、新たに同梱する3ファイルを明示して更新し、
既存glyph bundleの不変検査は維持した。実WSLの既存2件は58.15秒で通過した。
保存artifactを再hashしても異なるPDFの差分証拠をGETが拒否するAPIテストを追加し、
関連APIは13 passed（14.52秒）。接続後OCR contractは210 passed / 1 warning（44.98秒）。

実PDFへの最初の追加描画は、pypdfのFloatObject書出し精度が用紙寸法を丸めるため、
厳密なgeometry一致検査で停止した。高精度MediaBox/UserUnitの合成PDFで同じ失敗を再現した。
測定用writerが所有する数値だけ、元の浮動小数値へround-tripする固定小数表現で保存する。
libraryのglobal精度、元reader、decoded content、用紙の一致条件は変更しない。
同じ失敗テストを含む関連25件は修正後成功した。

修正後の実4ページPDFは、16命令すべての隔離描画を完了した。
元PNGとhelperの元RGBは一致し、対象stroke領域外の変更画素は0。
入力PDF/renderer JSON/全PNGは前後bytes一致を確認した。
結果は `C:/Users/hanam/AppData/Local/Temp/llmocr-stroke-differential-z5io1dwm`。
`vectors.json` は全16差分を含み、`page-N-operation-M.png` はその復元画像である。
最初の `report.json` は全footprintを要求する旧集計であり、後続のcomponent集計とは区別する。

## 端画素と交点の区別

正の面積が少しでも重なる全pixelを要求すると、antialiasの量子化により端画素に差が出ない。
元footprint集計は消さず、1 px未満のpaint componentだけを別集計する。
各componentのraster footprintに実差分を要求し、長いdashの内部観測や部分ink欠落は救済しない。
全componentに差がない、別位置へ移動した、ページ/元画素が異なる場合は成功にしない。

実データの追加集計では、page 1の対象線は218 component中217、page 2の9本は各436中433に
描画寄与があった。残りは他の実線との交点に一致する。
page 1はvector segment 1、page 2はsegment 20/21/22が候補であり、帳票名やラベルではなく
同じreport内の直交線分と位置から照合した。候補の存在だけでは観測成功にしない。
候補の独立raster観測が `SAMPLED` / inkCoverage=1 / contrastCoverage=1 の場合だけ補足できる。
現在の実帳票ではこの条件をすべて満たさず、10本の未検証判定を維持している。
閾値・必要coverageの割合を実帳票に合わせて下げる変更は行っていない。

component/交点の境界追加後は関連87 unitが成功。実帳票の現状は未検証のままであり、
線の欠落やOCR誤対応が発生したとの結論ではない。正当な交点での観測条件と
本番の環境資格・許容値、実入力の安全検証・全ページ実OCRは引き続き未完了である。

## 最終変更後の回帰状況

- 関連unit: **223 passed / 2.66秒**。対象17ファイルのRuffと8モジュールの限定mypyが成功。
- 固定WSLのvector helperと実資格保存/GET: **3 passed / 165.24秒**。
  高精度用紙値を保持するwriterへの修正後に実行した。合成帳票のnative検証であり、
  実帳票の資格PASSではない。DRAFTと本番拒否のassertionを維持した。
- OCR contract: **210 passed / 1 warning / 43.62秒**。
- frontend全体: **34 files / 372 passed / 17.75秒**。`npm run build`も成功。
  既存の500 kB超bundle警告あり。今回はfrontendを変更していない。
- `git diff --check`は成功。staging/commit/push、原本・実DBの変更なし。

実帳票の保存vector report SHAは
`22db8261b8ce7aba8ca5024127ddfd708974b5eb70266032a23750e482cca825`。
元helperの全画素差はこのreportに保存され、component/交点の後続集計は同じ証拠から
再計算した。元の不完全なfootprint観測を変更して成功したreportに置き換えていない。

変更後のbackend全体は、他のnative実行終了後に次の条件で開始する。
完走結果は別途記録する。先行1677件の全体成功とは分ける。

```powershell
$env:RUN_OPENAI_INTEGRATION_TESTS='false'
.venv/Scripts/python.exe -m pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-stroke-differential-full-0913
```

変更範囲は参照画像の検証・証拠保存であり、既存OCR contractを維持した。
provider/model/prompt/schema、mapping ID、physical identity、既存crop、補正/確定値は
変更していない。テンプレートの本番有効化・実手書きOCRの完了を意味しない。
## 全体回帰の完走結果

継続実行した `.pt-stroke-differential-full-0913` は、2026-09-13に
**1810 passed / 1 skipped / 3 warnings（2177.21秒）** で完走した。
これは差分描画の接続までのコードを検証した結果であり、収集後に追加した
測定コピー対象選別のコード・テストを含む全体成功とは扱わない。
