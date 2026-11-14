# XLSX印刷ページ設計

XLSX由来のテンプレートDRAFTで、原本の行範囲を印刷ページ（横1ページ×縦Nページ）に区切り、
ページごとの参考画像と座標変換を版に保存する仕組みの仕様。

- 「印刷ページを編集」で縦方向の境界を編集・保存する。解除操作は新しいprintLayoutだけを削除し、
  legacy画像・layout・configを維持する。
- 保存したページ上での区分・意味づけ・結合の手動編集は[保存ページ上での区分・意味づけ・矩形編集](page-authoring.md)、
  印刷用XLSX/PDFの取得は[保存済みTemplateVersionの印刷用XLSX / PDF](template-print-files.md)、
  ページ単位のOCRの受付・実行の条件は[OCR Safety Contract §12](ocr-safety-contract.md#12-xlsx複数ページocrの安全契約)を参照する。
- 原本の列幅・行高から求めるsource geometryは版ごとにmodelを記録する
  （`geometryModelVersion`。新規は`lo-import-twip-v1`やWSL印刷確定の`wsl-calc-print-grid-v1`、旧保存プランは旧モデルのまま）。
  以下の計算式は旧モデル（`fixed-7px-plus-5-v1`）を含む基本の考え方である。

## データと計算

- 原本はSHA-256を照合して読み取るだけ。XLSX/OOXMLを再保存しない。
- `PrintLayoutRequest`: 用紙、方向、mm余白、中央配置、横1/縦目標数、inclusiveな改ページ行。
- `breakAfterRows=null` は候補要求、`[]` は全範囲1ページ。行の重複・逆順・範囲外は禁止。
- 保存済み範囲 > 元のmanual breaks > 目標数/現在寸法の候補。自動配置ボタンだけが明示的に候補を作り直す。
- `printLayout.schemaVersion=1`: シート、原本SHA、layout SHA、取得OOXML設定、範囲、共通倍率、
  ページごとのtransform・参照画像・fieldsを保持。各ページは行/列/rangeを保持し、pixelだけでは保存しない。
- 物理単位はpt、描画は150dpi。列幅は従来に近い `width*7+5` の96dpi推定からptへ変換。
  行高はpt。列幅の物理サイズはExcelの字体依存の厳密値ではない。
- `scale = min(1, available_width/content_width, available_height/page_height for every page)`。
  全ページ共通・縦横同率。70%未満は警告。用紙余白を除き、中央配置をoffsetとして加える。
- `transform_rect`が原点差引き、scale、offset、端点丸めを一元化。
  ページPNG、物理プレビュー、候補矩形はこの計算結果を使う。
- セルID/元範囲と既存mappingの明示的IDを対応付ける。任意に移動/拡縮した矩形は、
  旧基準画像に対する再構築結果との不一致として拒否し、逆変換で推測しない。
- 原本結合セルを横切る境界を禁止する。GUIでの結合・解除は[ページ単位の目視編集](page-authoring.md)の検証済み経路に限る。

## 描画の限界

参考画像はPillow/MeiryoまたはNoto CJKによるセル構造の参考描画。
Excel/LibreOfficeの文字配置、太さ、表示書式、フォント、組版を完全再現しない。
連続編集ビューと物理ページビューを分離し、UIにも参考描画であることを表示する。
編集中のSVGは配置案、保存時のPNGはサーバーの参考描画。どちらも同じ矩形座標を使うが、
ブラウザーとサーバーで文字の組版は同一とは限らない。
日本語フォントがなければ503で拒否し、文字欠落した画像を保存しない。
印刷入口は保存済みの全ページ画像を表示する。旧1ページPDFを新設定のPDFとして返さず409にする。
出力XLSXの印刷設定・セル出力処理は変えない。

以下は検出して保存を拒否する: 離れた複数print area、print titles、header/footer、
横複数ページ、図形/画像/フォーム部品、数式、書式付き数値/日付、回転文字、元セルを確定できない編集矩形。
非表示行/列とセル内縮小文字は、原本を保持した下書き表示（[ページ単位の目視編集](page-authoring.md#非表示縮小文字の下書き表示)）に限って扱う。
自動で非表示部分や式の値、失われたページ数を推定しない。
OOXML属性は存在する辞書/存在しないnullを保持し、openpyxl既定値で埋めない。

## 所有権・snapshot・競合

TemplateVersionのquality内が正本。公開版をPUT/DELETEできない。
準備jobの`config_snapshot_json.printLayout`に同じページ定義・画像SHA/参照を保存する。
これは編集可能なDRAFT準備jobであり、受付済みOCR jobのsnapshotではない。
過去OCR jobを更新しない。ページ単位のOCR受付snapshotはOCR jobの側に別に固定する（[OCR Safety Contract §12.3](ocr-safety-contract.md#123-受付snapshotと実行retry)）。
fork時はセル/画像identity・パスを新版へ分離し、layout hashと準備snapshotを更新する。

GETで版/現layout/printLayout/inputRevisionのhashをrevisionとして返す。
試算・保存・解除はbaseRevisionを照合。保存は描画後にもmutation lockと書込みtransaction内で
再照合し、競合した古い結果を採用しない。画像は新しいUUID付きパスに作り、上書きしない。
DB rollback時に作成済み未参照画像が残る可能性はあるが、旧画像・旧版を破壊しない。
画像取得はlayout hashと画像SHAを確認。layout編集後の古い画像は409で拒否する。
既存検証fingerprintにもprintLayoutを含める。旧版で未設定なら従来fingerprintは変わらない。

テンプレートパッケージはページ設定を持たない。printLayoutのある版の書出しは`PRINT_LAYOUT_PACKAGE_UNSUPPORTED`で拒否し、
metadataを落として他PCへ運ぶ経路は作らない。

## API

すべて既存の認証・バックアップ排他ミドルウェア内。

- `GET /api/v1/template-versions/{id}/print-layout`: 原本論理構造、設定、revision
- `POST .../print-layout/preview`: 保存しないページ計算
- `PUT .../print-layout`: DRAFT設定/全ページPNG/準備snapshotの保存
- `DELETE .../print-layout?base_revision=...`: 新設定を解除し旧経路を使用
- `GET .../print-layout/pages/{number}/image`: hash検証した保存PNG

印刷用ファイルの確定・取得（`.../print-layout/prepare`、`.../print-layout/files/{format}`）と、
参照資格・OCR準備確認（`.../print-layout/qualification`、`candidate-readiness`、`ocr-readiness`）も同じrouterにある。

## 実装の場所

- backend: `app/domain/print_layout.py`、`app/services/print_layout_source.py`、
  `app/services/print_layout_renderer.py`、`app/services/print_layout_service.py`、`app/api/v1/routes/print_layout.py`
- frontend: `src/printLayout.ts`、`src/components/PrintLayoutEditor.tsx`、`src/api.ts`、`src/pages/TemplatePage.tsx`
- tests: `backend/tests/unit/test_print_layout.py`、`frontend/src/components/PrintLayoutEditor.test.tsx`
