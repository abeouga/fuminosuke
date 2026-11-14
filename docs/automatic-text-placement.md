# 矩形ごとの二軸自動文字配置

## 仕様

「文字位置」の「自動」は横・縦の両軸を自動に戻す。「縦位置」の「自動」は
縦軸だけを自動に戻す。各矩形のtextAlignment / textVerticalAlignmentを使用し、
手動ボタンはプレビュー全体の一時的な上書きとする。

XLSX取込では元セル書式を使用する（top / center / bottom、未指定はbottom）。
画像では既存文字検出領域の上下余白を比較する。複数行は文字領域全体で判定する。
縦情報がない旧データや未対応の均等配置はnullとし、表示は上へfallbackする。
旧画像の縦配置情報を取得するには項目名の文字候補取得が必要。

旧XLSXは同じ版に保存されたspreadsheetPrintCellsから、id・座標・寸法が
一意に一致する矩形だけを補完する。DBの公開版や過去jobは更新しない。

## 変更ファイル（リポジトリ相対）

- backend/app/layout_analysis/models.py：nullableな縦配置metadata
- backend/app/layout_analysis/text_placement.py：縦位置判定とXLSX書式変換
- backend/app/layout_analysis/detector.py：ページ座標系の文字領域から判定
- backend/app/services/label_cell_ocr_service.py：crop内の文字領域から判定
- backend/app/services/spreadsheet_template_import_service.py：取込時に書式取得
- backend/app/services/layout_service.py：旧XLSXの配置情報を読取時に補完
- backend/app/template_packages/schemas/template-v1.schema.json：生成schema更新
- frontend/src/types.ts：metadata型
- frontend/src/components/LayoutEditor.tsx：矩形ごとの解決と自動ボタン
- frontend/src/components/LayoutEditor.test.tsx：二軸9通りと自動への復帰
- backend/tests/unit/test_text_vertical_alignment.py：画像・XLSX・旧データ境界
- docs/ocr-safety-contract.md：表示metadataの契約

## 検証（2026-09-09）

- `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`：変更前194件、変更後194件通過。
- backend対象4ファイルのpytest：40件通過。追加後の新規境界テストは14件通過。
- backend `pytest -q --basetemp=.pt-align-after-0909`：全体708件を実行。
  renderer清掃テストで残留一時ディレクトリを検出し1件失敗。
  その後renderer_process全6件と配置境界14件の再実行は20件通過。
  全体一括実行がすべて成功したという結果ではない。
- frontend `npm.cmd test -- LayoutEditor.test.tsx`：102件通過。
- frontend `npm.cmd run build`：成功、既存のchunkサイズ警告あり。
- `git diff --check` とpackage schemaの `--check`：成功。
- 変更対象のRuff：import serviceを除き成功。import serviceの既存25件は未修正。
- mypy：既存箇所と依存ライブラリの型定義不足で29件。完全通過ではない。
- Chrome：右・下から自動を押すと両軸の自動選択へ戻ることを確認。

## 変更境界と未検証範囲

表示metadataの保存形式を拡張した。認識文字列、矩形座標、crop、mapping、
physical identity、provider association、prompt/model/schema（provider側）、
補正・確定・出力経路は変更せず、既存OCR contractを維持した。
実OpenAI/Google API呼出しは0回。実帳票OCR精度・誤対応件数は今回未測定。
汎用の保存response Golden replay、frontend全体テストは未実施。

現在のport 8000 backendは自動再読込なしで、新metadataを返していないことを
OpenAPIで確認した。使用にはbackend再起動後にテンプレート画面を開き直す必要がある。
新backendを使った現行帳票の実UI表示・保存確認は未実施。
