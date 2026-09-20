# Activation Phase 3 formal qualification（2026-09-14）

## 結論

対象TemplateVersion `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` について、Phase 2で保存済みの
prepared graph・replay・glyph・field geometry・environment leaseを現行validatorで再読込し、
正式なenvironment/reference qualificationを発行した。

- qualification policy: `wsl-prepared-reference-v2`
- environment policy: `wsl-prepared-environment-qualification-v1`
- geometry policy: `wsl-calc-reference-geometry-qualification-v1`
- status: `PASS`
- `qualified=true`
- `environmentQualified=true`
- `geometryQualified=true`
- unresolved blockers: `[]`
- TemplateVersion status: `DRAFT`
- Template `active_version_id`: `null`

Phase 3ではDRAFT→ACTIVE、approve成功経路、OCR受付gate、provider送信を変更・実行していない。

## 正式environment判定

environment lease v2は資格証ではなく、identity freshnessの入力として使用した。formal issuerは
次を一つの固定prepared-reference invocation scopeとして評価する。

- runtime image、Windows/WSL host、worker、Windows font directory
- bootstrap Pythonとnative dependency graph
- renderer identity、worker/broker core guard
- UID 65534の非特権実行、network拒否、no-new-privileges、host filesystem非公開
- readonly Windows font mount
- memory 1 GiB、swap 0、pids 64、CPU上限とeffective unit controls
- TemplateVersion、prepared graph、qualification fingerprint
- lease freshnessと現installation再照合

host全体の完全dependency closure、全dynamic-load trace、実入力scanはscope外である。これらを抽象的な
永久blockerにはせず、固定scopeに必要な証拠の欠落・不一致・期限切れだけをfail-closedとする。

## 正式geometry判定

対象を `wsl-calc-print-grid-v1`、150 dpi、integer-percent scaleへ限定した。原本/layout/configから
page planと全bindingを再計算し、4ページ・151 OCR領域・604辺を確認した。

- 全field geometry: `OBSERVED_WITHIN_DIAGNOSTIC_BUDGET`
- 全必要edge: `OBSERVED`、独立segment evidenceあり
- coordinate precision: 全ページ `MATCH`
- field raster: 全ページ `PASS`
- X表現誤差境界: `<= 0.5010416667 px`
- Y表現誤差境界: `< 0.503125 px`
- 最大実測表現誤差: `0.5015912074 px`（Y境界内）

閾値は実測最大値へ合わせていない。integer edge、PDF coordinate、page-height conversionから導く
軸別境界を使用した。文字可視性、実scan registration/crop correspondence、実筆画clearanceは
geometry資格へ含めず、provider送信前検査へ残した。

## anchor診断

`REFERENCE_ANCHORS_INSUFFICIENT` は削除せずreportに保持した。対象版はCalc正式geometry条件が完全に
成立したため `NON_BLOCKING_DIAGNOSTIC` とし、blocking codesからだけ分離した。
legacy/不明geometry、plan/binding不一致、edge欠落、coordinate mismatch、raster/vector不成立では
従来どおりblockingとなる。

## record bindingとGET

正式recordは次へ束縛した。

- TemplateVersion IDとrevision
- source XLSX SHA `aa1d088cf5ebae9cfca0f42d0ac3cbe1d8b23f7067196407f6ae3ce9c3cc3176`
- printLayout SHA `4feaec0c8350cd2146b29f6a5e4923fb2d3df9537111e9818c6b3706369cd602`
- geometry model `wsl-calc-print-grid-v1`
- prepared graph SHA `8be1a32b32a3d11f6e6381cc415ce35e295b74780f0ea5de504b5685e5b903c6`
- renderer report、4 reference image、replay、glyph、geometry、environment evidence SHA
- qualification fingerprintとissuer/全体implementation fingerprint
- policy、issuedAt、expiresAt、unresolved blockers

発行時刻は `2026-09-14T23:21:42+09:00`、environment leaseに従う期限は
`2026-09-15T18:12:41+09:00` である。GETは同じbindingを再計算し、現host/runtime identityとlease期限を
再確認した。発行時とGET時のbindingは一致し、GET前後の`quality_json`は一致したためDB無書換えを確認した。

source、printLayout、geometry model、prepared/artifact graph、reference/replay/glyph/geometry/environment
artifact、renderer/qualification implementation、environment identity、lease、version/revision、再計算plan/
bindingの変更・欠落・失効ではrecord全体を無効とし、GETで補完・延長・修復しない。

## 保存済みPhase 2証拠の再利用

発行結果の `evidenceSource` は `SAVED_PREPARED_GRAPH` である。旧reportのboolean/fingerprintを資格として
流用せず、各artifactを現validatorで再読込した。replay/glyph/geometry/environmentのnative計測は再実行せず、
新しいformal reportだけを保存した。

## 検証

- formal issuer unit、失敗注入、save/GET/invalidation integrationを含む関連セット:
  `218 passed / 1 deselected`（100.79秒）
- 実WSL full失敗2件の修正後個別回帰: `2 passed`（235.32秒）
- OCR contract（最終変更後）: `210 passed / 1 warning`（44.45秒）
- Ruff: 成功
- `git diff --check`: 成功
- backend full初回: `2455 passed / 2 failed / 1 skipped / 3 warnings`
  - 1件はenvironment単独資格trueへの旧期待値。
  - 1件は全体資格false時にGET表示のenvironment component `issuedAt`が再評価時刻へ変わる問題。
  - 期待値を現契約へ更新し、component発行時刻を保存recordから再検証するよう修正した。
- backend full最終: `2457 passed / 1 skipped / 3 warnings`（2555.46秒、終了コード0）

strict mypyの横断実行は、既存のopenpyxl/pypdfium stub欠落とWindows上でLinux専用APIを解析する既知エラーを
含み成功していない。今回追加コードで検出された型エラー3件は修正済みである。必須判定は上記unit/
integration、OCR contract、backend fullで成功している。

OpenAI API 0回、Google API 0回。

## Phase 4へ残す範囲

- 正式資格をDRAFT→ACTIVEのapprove成功経路へ接続すること
- `require_legacy_ocr`、RepositoryのACTIVE切替、旧ACTIVEのARCHIVED化、`active_version_id`更新
- OCR受付gateとJobPageService成功経路
- provider送信前の実入力identity、scan registration、crop correspondence、INK_CLEARANCE

これらはPhase 3では開始していない。
