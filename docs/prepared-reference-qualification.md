# WSL印刷確定版の参照検証

WSLで印刷ファイルを確定した版（`rendererVersion=wsl-print-reference-v1`）について、
保存済みの印刷成果物が版・原本・変換環境と対応しているかを検証し、正式な参照資格を発行する仕様。

## 対象と変更境界

`ReferenceQualificationService.qualify/status`は、WSL印刷確定版に対して
Windows版の`environment_identity` / `render_office` / `renderer_identity`や旧geometry modelを使わず、
WSL版の明示分岐で検証する。WSLの保存済み成果物と異なるrendererを検証しないためである。

旧single-page/Windows版のfingerprintと実行経路は維持する。
provider/model/prompt、値・判定、mapping/association、canonical座標、印刷ファイルは変更しない。

## 保存成果物の照合

`prepared_reference_evidence.py`は以下を読取専用で照合する。

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
この再検査は通常のautosaveやステップ切替では行わない。
statusも保存booleanではなく、版・実装・成果物の現在の対応を検査する。

## 正式資格（`qualificationPolicy=wsl-prepared-reference-v2`）

上記の照合で保存したreportは、それだけでは資格証として扱わない。正式資格は、独立artifact validatorを通して
replay、glyph、field geometry、environment leaseを再読込し、現在のissuerで再評価して発行する。
証拠graphが完全一致する場合は、native描画・測定を反復せず新しい正式recordを発行できる。

environment issuerのscopeは固定prepared-reference invocationである。lease v2はidentity freshnessの
証拠としてのみ用い、prepared execution reportの非特権実行、network/host filesystem隔離、
no-new-privileges、read-only font mount、resource limits、effective unit controls、worker/broker core
guard、bootstrap/native dependency identityと組み合わせる。

geometry issuerは `wsl-calc-print-grid-v1`、150dpi、整数percent scaleに限定する。原本から再計算した
plan/binding、全対象領域の全四辺、PDF整数座標、所有vector/raster証拠を照合し、数理的表現誤差境界で
判定する。旧5点anchor不足は診断に保持し、この正式Calc判定が成立したときだけ非blockingへ分離する。
legacy・不明geometryまたは不完全証拠では分離しない。

`deterministic=null` は未測定であり、非決定性を実測したfalseでも成功trueでもない。
固定環境の証拠がある版に限り、保存した印刷XLSXを同じruntimeで再描画し、全ページの順序・寸法・画像SHAの一致を
検証できる。その他の未認定条件は維持する。
保存reportのPASS/qualified/environmentQualifiedを書き換えても本番gateは開かない。
GETは保存booleanではなく版、原本、plan、graph、artifact、実装、現在環境、lease期限を再検証し、
DBを書き換えずに失効させる。正式資格がPASSでもDRAFTのままであり、承認とOCR受付の条件は
[OCR Safety Contract §12](ocr-safety-contract.md#12-xlsx複数ページocrの安全契約)に従う。
実scan registration/crop対応・実筆画clearanceはprovider送信前の責務である。
