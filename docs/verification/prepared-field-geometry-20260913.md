# 版所有の項目別四辺測定証拠

## 実装範囲

`prepared_reference_geometry.py` に、元PDFの線分・最終PNG・必要最小限の罫線を追加した
測定コピーを照合する処理を追加した。`reference_qualification_service.py` の明示検証で
`fieldGeometry` と所有された `geometryEvidence` を保存し、GET時に再計算する。
元の５点anchor診断は `pages` に残す。新しい観測で既存の拒否条件を消さない。

- 固定runtimeを記録していない旧成果物には実行環境を後付けしない。
- 原本/印刷XLSX/PDF、保存座標・page range・mapping・実スキャンを変更しない。
- 測定コピーは欠落した辺だけを明示cell ID/sourceRangeから選択する。曖昧な線を
  近傍の別線へ付け替えず、境界座標を合わせ直さない。
- コピーの変更は既存のbyte保持方式に限定し、元ファイルからの再生成bytes一致を検査する。
  非stroke文字プログラムとfont resourceの一致、用紙geometry、固定環境の一致を要求する。
  最終PNGの差が赤い測定線近傍以外に存在する場合は拒否する。
- 元/probe vector JSONとprobe XLSX/PDF/report/全PNGは同じsource jobの専用artifactとして保存する。
  版、準備済み成果物graph、artifact ID/owner/name/path/media/attempt/SHAを再検査する。
  GETからOffice/PDF helperを実行しない。再hashした不正なhelper情報も受理しない。
- 共通の `reference_border_probe.py` を使用し、従来の診断CLIは同じ関数をimportする。
  診断CLI自体をbackendからimportしない。

`MEASURED` は座標と画像の観測結果であり、資格PASSではない。
四辺の位置だけ存在する白紙PNGは `REFERENCE_RASTER_INK_UNVERIFIED` とする。
黒塗り等はinkの存在だけでobject identityやvisibilityを証明できないため、
contrast観測を残し、`visibilityVerified` / `thresholdQualified` / `productionQualified` はfalse。
環境資格・本番の許容値・実筆画clearanceを代替せず、ACTIVE/受付の拒否は維持する。

## 同じ実帳票の保存証拠による再計算

2026-09-13、既存の `llmocr-border-probe-27etx0v5` と、最新の文字プログラムを持つ
`llmocr-border-text-ej2nj08l` の保存データを読み取り専用で使用した。
これは新しいOffice変換や、実DBへの資格保存ではない。

| page | 項目数 | 最大座標差 px | raster検査位置数 | 判定不能な線分 |
| --- | --- | --- | --- | --- |
| 1 | 60 | 1.656250 | 19,488 | 1 |
| 2 | 51 | 1.448409 | 25,644 | 9 |
| 3 | 37 | 1.390075 | 23,908 | 0 |
| 4 | 3 | 0.795833 | 8,802 | 0 |

151項目の四辺が観測された。全ページで測定線近傍以外の変更画素は0。
page 1/2は細かい破線に内部paint sampleがないため、rasterの未検証codeを保持する。
これを線の欠落や実筆画の切断と断定しない。page 3/4も本番資格を発行したわけではない。
印刷XLSX SHAは `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`。
使用したvector helper bundleは
`77bdc694b0065564df4bdfb1f7065f2311458fe688ac22fab68dbd76520642ab`。

## 検証経過

- 初期の38 unitは2 failed / 36 passed。visibility区分の欠落と、白紙PNGを座標だけで
  `MEASURED` とする不足を再現した。追加後は38 passed。境界追加後は54 passed。
- 共通関数化後、geometry/probe/vector/rasterの関連unitは117 passed（1.05秒）。
- API接続前の新規テストはgeometryEvidence欠落で1 failed。
- API接続後の最初の実行は進行中の全体テストとmachine renderer mutexで競合した。
  1 failed / 1 passed / 10 setup errorsであり、製品回帰の確定や成功証拠には扱わない。
- 新APIテストのnative処理はすべてsynthetic fixtureであるため、そのファイル内だけ
  machine mutexを使用しないfixtureに分離した。process-local lockは残し、未mockの
  vector helper呼出しはテストを失敗させる。12 passed（13.23秒）。これはAPI/DBの
  所有・保存・失効検証であり、実WSL隔離や正式な資格の証明ではない。
- API接続前OCR contract: 210 passed / 1 warning（60.31秒）。
  接続後も210 passed / 1 warning（42.51秒）。
- 追加の集約code検証後、API/新規unitは66 passed（23.50秒）。関連vector/helperを含む
  unit全体は189 passed（1.90秒）。対象Ruffと限定mypyを実施した。
  新コードの全体回帰と実WSLの追加経路は別途確認が必要。
- 起動オプション修正の先行全体テストは、この追加より前に収集・開始されている。
  新geometryの全体成功として流用しない。結果は1 failed / 1610 passed / 1 skipped /
  3 warnings（1975.58秒）。失敗はprint-layoutのinvalid要求で期待422に対し409。
  並行fixtureの共有mutex競合と時刻が重なるため、同じ失敗テストと関連APIを直列再検証する。

直列の関連API検証は52 passed / 1 failed（144.99秒）。先行のprint-layout失敗は再現しなかった。
残った失敗は、新しく追加した実geometry証拠のassertionである。対象fixtureは既存の
`installed_runtime`（名前順で最初の固定イメージ）を選び、そのイメージには
`/usr/local/lib/llmocr-glyph-v1/bin/python` が存在しなかった。実helperの再実行でも同じ
`bwrap: execvp ... No such file or directory` を確認した。字形・線分解析の成功を
この旧fixtureに要求したことが誤りだった。

新規5 assertionは削除・緩和せず、同じ試験帳票を実描画し、専用依存probeで選択した
`glyph_runtime` を使う `test_wsl_glyph_qualification.py` へ移した。既存の印刷用runtime
テストのassertion・選択方式・製品のfallback拒否は変更していない。両経路を直列で再検証する。

両経路の再実行は1 passed / 1 failed（155.53秒）。旧runtimeの印刷資格テストは成功した。
専用依存がある経路はgeometry測定を完了したが、保存時にWindowsのパス長制限で失敗した。
対象ファイルは250文字、atomic writerの一時名はそれを超える長さだった。
新しいgeometry成果物だけ不要な `geometry/` 階層を除き、既存の同世代qualification
ディレクトリ直下へ固有名で保存するよう修正した。artifactの版・所有・SHA条件は維持する。
原本や既存の成果物を移動・削除せず、共通storageの仕様も変更していない。
保存先の形をunitで固定し、同じ実テストを再実行する。

保存階層修正後の実WSLテストは **1 passed（94.48秒）**。
固定runtimeで印刷準備→再描画→字形/線分検査→geometry artifact保存→GET再計算が通過した。
生成済み印刷ファイル/planが不変であること、DRAFT維持、有効化拒否の既存assertionも通過した。
このnative検証は合成帳票であり、実帳票の本番認定とは区別する。

## 実帳票コピーでの保存・再取得

2026-09-13 20:11 JST、既存の検証用保存先
`C:/Users/hanam/AppData/Local/Temp/llmocr-prepare-copy-x5_l5t3k` に対して
`ReferenceQualificationService.qualify` と `status` を実行した。
ライブのDBではなく、このコピー内の資格証拠だけを保存した。

- 4ページ151項目を測定し、geometryの9 artifactを保存した。
- GET相当のstatus再計算結果は保存時のreportと完全一致した。
- 実行前後の保存plan/printFiles/ページfieldsは一致し、版はDRAFTのまま。
- 字形と再描画の未検証条件は残っていない。一方、環境資格とgeometry許容値の未認定、
  page 1/2の細かい破線に関するraster未検証を保持し、資格結果はFAIL/qualified=false。
- 測定値は上表と同じ。これは本番有効化成功や手書きOCR結果の証明ではない。

コピー内report:
`artifacts/7b90d415-98d5-44e4-971a-e477ded0fdab/qualification/cccc470ea0634e0cad90e6105a78abf1/report.json`

SHA-256: `520a59e79b576b1b6d7187bd43cb375266e86ffb87129bd85441d7abe1fd9e51`。
対象の保存PDF SHA: `3d91dd21e60cf0f5248cc415bfd4865e7f2e73a0ad471a7381248fa13fd9965f`。
資格fingerprint: `0c39827a049b3042a54693d9475cb5bc07cd5f12a80349cf5ef74df07e583352`。

最終保存先修正後のunitは54 passed（0.93秒）、OCR contractは210 passed / 1 warning
（46.65秒）。対象Ruffと4モジュールの限定mypyは成功した。frontendは変更・再検証していない。

```powershell
# backend: 関連unit
.venv/Scripts/python.exe -m pytest tests/unit/test_prepared_reference_geometry.py tests/unit/test_border_probe_diagnostic.py tests/unit/test_reference_vector_measurement.py tests/unit/test_reference_vector_raster.py tests/unit/test_renderer_vector_bridge.py tests/unit/test_renderer_vector_pdf.py -q -o addopts='' -p no:cacheprovider
# backend: API所有証拠（synthetic helpers）
.venv/Scripts/python.exe -m pytest tests/integration/test_prepared_reference_geometry_api.py -q -o addopts='' -p no:cacheprovider
# backend: 実WSL生成からGETまで（他のrenderer/full suiteと並列実行しない）
.venv/Scripts/python.exe -m pytest tests/integration/test_wsl_glyph_qualification.py -q -o addopts='' -p no:cacheprovider
# root: OCR contract
backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py
```

新コードを含む全体回帰を、他のrenderer呼出し終了後に
`RUN_OPENAI_INTEGRATION_TESTS=false`、`pytest -q --tb=short -o addopts='' -p no:cacheprovider
--basetemp=.pt-field-geometry-full-0913` で実行する。完走結果は別途確認する。

この全体回帰は **1677 passed / 1 skipped / 3 warnings（2029.08秒）** で完走した。
先行の共有mutex競合は再現しなかった。この実行の収集後に追加した
[破線の差分描画](stroke-differential-20260913.md)は、ここでの全体成功に含めない。

既存OCR contractを維持する変更範囲であり、provider/model/prompt/schema/association、
既存crop、レビュー補正・確定値は変更していない。実OpenAIは作業全体0/100回、Google 0回。
本番のテンプレート有効化と全ページ実OCRの通し検証は未完了。
