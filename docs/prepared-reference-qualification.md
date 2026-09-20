# WSL印刷確定版の参照検証

## 原因と変更境界

`ReferenceQualificationService.qualify/status` はWSL印刷確定版でもWindows版の
`environment_identity` / `render_office` / `renderer_identity` と旧geometry modelを使用していた。
WSLの保存済み成果物と異なるrendererを検証し、WindowsのIPC制限等に影響される経路だった。
frontendだけでは解決できないため、参照検証serviceにWSL版の明示分岐を追加した。

対象は `rendererVersion=wsl-print-reference-v1` の印刷確定済み版だけである。
旧single-page/Windows版のfingerprintと実行経路は維持する。
provider/model/prompt、値・判定、mapping/association、canonical座標、印刷ファイルは変更しない。

## 検証する対応

新しい `prepared_reference_evidence.py` は以下を読取専用で照合する。

- 現TemplateVersionのlayout SHA、geometry model、printFiles renderKey。
- 同じsource jobのsourceWorkbookとplan・FODS測定reportの原本SHA。
- 印刷XLSX/PDF/report/全ページ画像のJobArtifact ID、所有job、名前、path、media type、SHA。
- reportが示す入力XLSX・出力PDF・全画像のSHA、ページ数・寸法・DPI・ページidentity。
- FODS/PDFのfont解決結果、および記録されているruntime/font集合/worker証拠の一致。

内容サイズと全reference画像/PDFの合計上限を確認する。storageの読取APIを使用しており、
未信頼の任意巨大ファイルをstreaming parserで安全に解析したという保証ではない。
ここではPDF parseやOffice変換を行わない。
旧reportに環境情報が無いことを、環境認定済みの証拠として扱わない。

版のfingerprintにはWSL geometry modelと参照検証・worker・既存geometry/crop測定実装の
内容SHAも含める。size/mtimeから過去SHAを再利用しない。
qualifyは保存済み全ページ画像に既存の罫線測定/crop未検証判定を適用する。
閾値、anchor数、borderless fieldの扱いを緩和してPASSにはしない。
PNGは元の所有artifactを参照し、別rendererで再生成・置換・重複保存しない。

検証中の設定/原本/成果物変更は、保存前に再照合して409で拒否する。
重い画像測定はmutation lock外で行い、保存時に成果物の内容とmetadataを再検査する。
この再検査は通常のautosaveやステップ切替へ追加していない。
statusも保存booleanではなく、版・実装・成果物の現在の対応を検査する。

## 正式認定との区別

2026-09-14のPhase 3で `qualificationPolicy=wsl-prepared-reference-v2` を追加した。
Phase 2の保存済みreportは資格証として流用せず、独立artifact validatorを通してreplay、glyph、
field geometry、environment leaseを再読込し、現issuerで再評価する。証拠graphが完全一致する場合は、
native描画・測定を反復せず新しい正式recordを発行できる。

environment issuerのscopeは固定prepared-reference invocationである。lease v2はidentity freshnessの
証拠としてのみ用い、prepared execution reportの非特権実行、network/host filesystem隔離、
no-new-privileges、read-only font mount、resource limits、effective unit controls、worker/broker core
guard、bootstrap/native dependency identityと組み合わせる。

geometry issuerは `wsl-calc-print-grid-v1`、150dpi、整数percent scaleに限定する。原本から再計算した
plan/binding、151対象領域の全四辺、PDF整数座標、所有vector/raster証拠を照合し、数理的表現誤差境界で
判定する。旧5点anchor不足は診断に保持し、この正式Calc判定が成立したときだけ非blockingへ分離する。
legacy・不明geometryまたは不完全証拠では分離しない。

`deterministic=null` は未測定であり、非決定性を実測したfalseでも成功trueでもない。
後続の[再描画比較](prepared-reference-replay.md)では、固定環境の証拠がある版に限って
保存XLSXを再描画し、全ページ一致を検証する。その他の未認定条件は維持する。
保存reportのPASS/qualified/environmentQualifiedを書き換えても本番gateは開かない。
GETは保存booleanではなく版、原本、plan、graph、artifact、実装、現在環境、lease期限を再検証し、
DBを書き換えずに失効させる。正式資格がPASSでもDRAFTのままであり、既存のACTIVE/paged OCR受付拒否を
解除したとは扱わない。実scan registration/crop対応・実筆画clearanceはprovider送信前の責務である。

## 検証

- 直前のbackend full: 1114 passed / 1 skipped / 3 warnings（1671.95秒）。
  worker変更前の結果であり、workerと本変更の変更後fullとしては流用しない。
- 変更前OCR contract: 210 passed / 1 warning（68.98秒）。
- 初回新規セット（実WSL含む）: 11 passed（50.55秒）。
- 保存中の変更を追加した新旧qualification対象セット: 30 passed（165.62秒）。
  `tests/integration/test_prepared_reference_qualification.py`
  `tests/integration/test_reference_qualification_api.py`
  `tests/unit/test_reference_qualification.py`。
- report内のリンク不一致を追加した5ケースの初回は、別試験との変換mutex競合で
  setup時にRENDERER_BUSYとなった。排他制御・assertionを弱めず、先行終了後に直列再実行した。
  最終新規セットは **18 passed（56.53秒）**。mockのartifact照合と実WSLの2ページ経路を含む。
- 変更後OCR contract: 210 passed / 1 warning（73.42秒）。
  同じ契約検証と変更範囲を根拠に、既存OCR contractを維持した。
- 対象Ruff成功、限定mypyは2ファイル成功。frontendソースは変更していない。
- worker変更を含む変更後full: `.pt-wsl-reference-full-0913` の同一processが完走。
  **1146 passed / 1 skipped / 3 warnings（1698.47秒）**、終了コード0。
  後続の再描画比較テストは収集後の追加で、このfullには含まれない。

原本/実DB/実テンプレートへの変更なし、OpenAI/Google呼出し0。
実ユーザー版のqualification、実UI、本番有効化は未検証。

## Phase 3（2026-09-14）

formal issuer、Calc geometry tolerance、anchor診断の条件付き分離、record保存・GET再検証・失効を
実装した。対象実版で `qualified=true / environmentQualified=true` を発行したが、DRAFT、approve拒否、
OCR受付gateは維持している。条件・binding・検証結果は
[Phase 3正式qualification](verification/activation-phase3-qualification-20260914.md)を参照する。
