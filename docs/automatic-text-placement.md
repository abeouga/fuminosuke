# 矩形ごとの二軸自動文字配置

テンプレート編集のプレビューで、各矩形の文字を横・縦それぞれどこに置くかを決める仕様。

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

## 実装の場所（リポジトリ相対）

- backend/app/layout_analysis/models.py：nullableな縦配置metadata
- backend/app/layout_analysis/text_placement.py：縦位置判定とXLSX書式変換
- backend/app/layout_analysis/detector.py：ページ座標系の文字領域から判定
- backend/app/services/label_cell_ocr_service.py：crop内の文字領域から判定
- backend/app/services/spreadsheet_template_import_service.py：取込時に書式取得
- backend/app/services/layout_service.py：旧XLSXの配置情報を読取時に補完
- backend/app/template_packages/schemas/template-v2.schema.json：生成schema
- frontend/src/types.ts：metadata型
- frontend/src/components/LayoutEditor.tsx：矩形ごとの解決と自動ボタン
- backend/tests/unit/test_text_vertical_alignment.py：画像・XLSX・旧データ境界
- docs/ocr-safety-contract.md：表示metadataの契約

## OCR契約との関係

表示metadataの保存形式を拡張したもので、認識文字列、矩形座標、crop、mapping、
physical identity、provider association、prompt/model/schema、
補正・確定・出力経路は変えない。
