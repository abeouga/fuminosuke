# 細線の画素境界への丸め — 2026-09-13

## 状態

テンプレート有効化は未完了。前段の[欄別検査](field-raster-scope-20260913.md)で残った
測定線1本について、Popplerのstroke adjustmentと検査位置の不一致を特定した。
固定校正PDFの隔離実描画で256ケース/4352点の一致を確認し、参照検証の保存/GETへ接続した。
元の診断と座標誤差は保持し、資格発行・ACTIVE/受付gateは未開放。最新の検証内訳は末尾に記載する。

## 原因の根拠

固定runtime image `9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01` 内の
`usr/share/doc/poppler-utils/changelog.Debian.gz` を読み取り専用で確認し、
Poppler `22.02.0-2ubuntu0.13` と一致した。runtime imageの変更や依存導入は行っていない。

同じUbuntuパッケージの一次ソースを照合した。

- [SplashOutputDev.cc](https://git.launchpad.net/ubuntu/+source/poppler/plain/poppler/SplashOutputDev.cc?h=ubuntu/jammy-updates):
  ページ開始時にstroke adjustmentを有効にし、PDFのSA値で更新する処理は無効化されている。
- [Splash.cc](https://git.launchpad.net/ubuntu/+source/poppler/plain/splash/Splash.cc?h=ubuntu/jammy-updates):
  正の線幅のpathはstroke outlineへ展開され、単独butt-cap線には両側の調整hintを付ける。
- [SplashXPath.cc](https://git.launchpad.net/ubuntu/+source/poppler/plain/splash/SplashXPath.cc?h=ubuntu/jammy-updates):
  両側の座標をroundし、同じ値になった場合は上限側を1画素進める。

対象線は投影中心x=909.61875、幅0.2083125px。両側は約909.514594と909.722906なので、
どちらも910へ丸められ、描画区間は列910の1画素になる。現在の検査は連続座標上の
線幅から列909を選んでおり、丸め済み描画位置を検査していなかった。

検証用DBの所有PDF/PNG/vectorは前段と同一。新関数はPNGを引数に取らず、vectorの数値だけから
列910を返した。その後に画像を調べると、25サンプル全件で元画像は白、測定画像は赤だった。
これは画素探索や閾値緩和ではない。一方、単独の実例だけで他の幅/小数位置を保証しない。

全4ページの明示fieldが参照するeligible細線は12/2/2/0本。
新予測位置では全16本のinkが全サンプルで観測された。うち7本は交差箇所等でcontrastが
部分的であり、全線のcontrast成功とは扱わない。旧ink欠落から変わるのは問題の1本だけだった。

## 追加コードと境界（接続前の記録）

- `backend/app/services/reference_splash_raster.py`: 独立した単独segment、実線、butt cap、
  axialで非shearの正のsubpixel幅だけに予測を限定する。破線、clip、curve、複合path、
  不明なsubpath、1px以上の線を推測処理しない。元のfield座標・crop・画素閾値は変更しない。
- `backend/app/services/renderer_splash_calibration.py`: 固定256ケースの校正PDFを生成し、
  既存隔離workerのbounded rendererから生RGBを収集するための未接続モジュール。
  8小数位置×4線幅×縦横×正逆×赤黒を含む。利用者の帳票値や名前を生成に使わない。
  PDFは固定pathだけの決定的な4オブジェクトで、外部resource/font/javascriptを含まない。
  処理系追加を避けるため、この固定fixtureだけは専用の決定的なbyte生成を使用する。
- 保存成功booleanを信頼せず、白画像からの可逆RGB差分を検証・復元して256ケース/4352点と
  両隣の白画素を再検査する。1ケースの欠落もOBSERVEDにしない。元PDFの解析はhostへ戻さない。
- 新しいモジュールは実行中fullの収集後に追加した。本番import・資格fingerprint・gateへ
  接続していないため、このfullを新方式の全体回帰成功とは扱わない。

PDFスキルに従い、固定fixtureの最初の生成前にartifact operationを記録した。
実native結果のPNG全体と線の拡大確認は未実施であり、生成コードのunitを目視検証の代用にしない。

## 検証結果（接続前）

```powershell
# backend
.venv/Scripts/python.exe -m pytest tests/unit/test_reference_splash_raster.py tests/unit/test_renderer_splash_calibration.py -q -o addopts='' -p no:cacheprovider --basetemp=.pt-splash-calibration-unit-0913
.venv/Scripts/python.exe -m ruff check app/services/reference_splash_raster.py app/services/renderer_splash_calibration.py tests/unit/test_reference_splash_raster.py tests/unit/test_renderer_splash_calibration.py
.venv/Scripts/python.exe -m mypy app/services/reference_splash_raster.py app/services/renderer_splash_calibration.py --follow-imports=silent
```

- unit **57 passed（2.68秒）**、Ruff、2モジュール限定mypy成功。
- 最初の数学unitはfixture helperへの入力単位を二重変換して32 failed/10 passed。
  helperが既にpixel→PDFを行うことを確認して生成入力を訂正し、期待値は維持した。
- 最初の校正unitは54 passed/3 setup errors。共有pytest tempのアクセス拒否で、未使用の
  repo-local basetempを指定して全57件を再実行した。製品の拒否条件やassertionは変更していない。
- 新方式は未接続なので、既存OCR contract・provider・mapping・crop・補正/確定・実DBは不変。
  実OpenAI 0/100回、Google 0回。native校正・保存/GET連携・資格発行は未検証。

## 接続時に要求した条件

実行中 `.pt-field-scope-full-0913` 完走後に、同じ固定環境と隔離条件で校正PDFを描画する。
予測だけでなく全ケースの実画素を検査し、ずれ/欠落注入も拒否することを確認する。
その証拠をvector helperのcode/input/runtimeに束縛し、保存/GETで再検査した場合だけ
新しい丸め位置をink観測に利用する。旧raw観測と座標誤差は保持し、環境・可視性・
cropの未検証条件をこの数学モデルだけで解除しない。

## 補助観測の追加（同日、全体回帰実行中）

`reference_splash_raster.observe_adjusted_ink` を追加した。本番importはまだない。
所有helperの入力/code/runtime照合を呼出側の前提とし、固定校正の生RGBを再検査してから、
同じPNG SHA・明示segmentの予測画素だけを読む。既存の全線診断を変更せず別結果を返す。
端点guard、ink/contrast閾値、sample budgetを維持し、共有辺の重複samplingを避ける。
operation件数は一度だけ集計し、複合pathを除外する。画像内の探索・geometry補正は行わない。
暗い背景ではinkとcontrastを区別し、object identity・可視性・本番資格はfalseのままとする。

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_reference_splash_raster.py tests/unit/test_renderer_splash_calibration.py tests/unit/test_reference_splash_observation.py -q -o addopts='' -p no:cacheprovider --basetemp=.pt-splash-observation-final-0913
```

- 新規補助観測18ケースを含む **75 passed（6.92秒）**。
- 縦横/正逆、隣接位置のinkを拾わないこと、欠落、暗背景、校正不成立/欠落、
  PNG/校正SHA・寸法変更、共有辺、sample上限、複合pathを検証した。
- Ruffは上記3テストと2モジュールで成功、同2モジュールの限定mypyも成功。
  初回Ruffでloop変数capture/行長の指摘があり、修正後に全75件を再実行した。
- `.pt-field-scope-full-0913` は引き続き実行中。この追加をその全体回帰の対象と主張しない。
- 隔離native校正・保存/GET接続・資格発行・ACTIVE・実OCRは未実施。原本/実DBは変更せず、
  既存OCR contractを維持した。実OpenAI 0/100回、Google 0回。

## 固定helper・保存/GET接続と実4ページの確認

前段の全体回帰は **1874 passed / 1 skipped / 3 warnings（2136.18秒）** で完走した。
その後、`renderer_vector_worker.py` の同じ隔離内で固定校正を収集し、追加native処理の後にも
code/input/font/runtimeの既存再照合を行うよう接続した。`renderer_vector_bridge.py` は
生RGBの構造・SHAを検査する。不一致の校正は診断として残るが、補助観測には使用しない。

`prepared_reference_geometry.py` は所有証拠を検証した後、校正と元PNGから補助観測を再計算する。
`reference_field_raster.py` は同じ明示segmentの新しい全サンプルにinkがある場合だけ補完を使用し、
旧raster/differentialを保持する。両新モジュールを資格fingerprintにも追加した。
保存summaryの改変、helper生RGBの改変（外側SHAの再計算を含む）、実装変更を拒否する。
Safety Contract §12.7へこの観測境界を記載した。元のpath・crop・mappingや本番gateは変更しない。

### 実nativeの校正

初回の固定helper校正で **256ケース・4352点の一致、未一致0** を確認した。
PDFスキルに従って生成前にoperationを記録し、復元PNG全体を目視確認した。
記録は `C:/Users/hanam/AppData/Local/Temp/llmocr-splash-calibration-7039t6p3/vectors.json`、
SHA `1f4c6a6eb81b3c52a3365e433fa27ca9ab1c9500709862097de273dd81497fb2`。
これはimport順整理前のcode bundleに束縛した実行記録であり、後の資格へ再利用していない。

### 現行コードでの実テンプレート比較

検証DB/storageコピー `llmocr-prepare-copy-x5_l5t3k` で資格処理とGETを再実行した。
版 `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` はDRAFTのままで、
revision `7648ae168127a4029875094e419f0469de490fb6e6f9fb8f1256ae5eb5d69d4b` は不変。
layout・plan・source/config・保存印刷XLSX/PDF/report・全ページ参照PNGの不変を確認し、
新旧測定PNGのSHAと座標誤差も全4ページで一致した。資格保存responseとGETは完全一致。

| ページ | field数 | 最大誤差px（不変） | 四辺検査 | 丸め補助で変わったsegment |
| --- | ---: | ---: | --- | --- |
| 1 | 60 | 1.65625 | MEASURED | 7のみ |
| 2 | 51 | 1.4484087926498432 | MEASURED | なし |
| 3 | 37 | 1.3900754593166766 | MEASURED | なし |
| 4 | 3 | 0.7958333333333485 | MEASURED | なし |

保存reportは同コピーの
`artifacts/7b90d415-98d5-44e4-971a-e477ded0fdab/qualification/47e70d71e3194367842fe59a1589d9df/report.json`、
SHA `1654c4f979cb61d555df1617dbbbb6d3b5a5ac75e5941c2b7fadc984b59317cc`、
fingerprint `bfd14c907f8ad96555f82903f871a8f354c99250783005796d178aee0614872e`。
現行code bundleは `9b2ceec76cd64b084ab72a7a66a04ef15795b95258538010a8c49ff2337c8bfc`。
元PDF/測定PDFそれぞれで新しい校正生RGBを収集して検証し、旧helper reportへ後付けしていない。

`REFERENCE_RASTER_INK_UNVERIFIED` は解消したが、`REFERENCE_ANCHORS_INSUFFICIENT`、
`WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED`、`GEOMETRY_TOLERANCE_UNQUALIFIED` は残る。
qualifiedはfalse。3515文字命令・640font codeの照合は成功だが、認識精度やcrop安全性の証明ではない。
実DBのACTIVE化、実OCR、レビュー/出力、カード取得のE2Eは未完了。実APIは引き続き0回。

### 接続後テスト

- bridge接続前: 新規の改変拒否5 failed / 2 passed。接続後は同期待値を含む **59 passed**。
- geometry接続対象 **108 passed（26.43秒）**、補助位置の保存計算5件 **5 passed（1.82秒）**。
- 生RGB改変の保存/GET検査追加後、API **16 passed（20.34秒）**。
- 6モジュール限定mypy、最終15ファイルRuff、diff check成功。
- 実装fingerprintの2追加ケースと実WSLを含む最終対象回帰は **230 passed（103.23秒）**。
  nativeの固定helper校正と異なるreference寸法の拒否も含む。
- 変更前OCR contractは210 passed（43.63秒、前段field-scope後）、変更後は
  `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py` で
  **210 passed / 1 warning（40.53秒）**。実API無効。
- 変更後fullは別に実行し、1874件の先行fullで今回の接続を代替しない。

```powershell
# backend、実nativeとの重複実行を避ける
$env:RUN_OPENAI_INTEGRATION_TESTS='false'
.venv/Scripts/python.exe -m pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-splash-full-0913
```

同コマンドは **1964 passed / 1 skipped / 3 warnings（2166.97秒、36分6秒）** で完走した。
先行full後に接続したSplash観測と改変拒否を含む全体回帰で、実APIは無効。
並行して追加した未接続のCalc print gridモジュールは収集後の追加なので、このfullには含まない。
全体回帰終了後にのみ、独立したCalc座標holdoutのnative描画へ進んだ。
