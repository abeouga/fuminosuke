# XLSX印刷ページ設計 (2026-09-07)

2026-09-09追記: [ページ単位の目視編集](page-authoring.md)を追加した。
非表示行列・セル内縮小を含む帳票は、原本を保持した未認定の下書き表示に限定して扱う。
保存ページ上で既存矩形の区分・意味づけを編集できる。以下の初期Phaseの拒否範囲と
レイアウト編集後の画像失効は、専用authoring APIの条件を除く記録として参照する。

2026-09-08追記: 新規printLayoutのsource geometryは`lo-import-twip-v1`を使用する。
旧保存プランとlegacy OCR座標は旧モデルを保持。以下は初期Phaseの記録であり、
校正の現状・実測・制約は[Geometry Calibration](geometry-calibration.md)を参照。

## 到達範囲

この実装はDRAFTの印刷ページ設計・参考画像生成まで。複数ページ実OCRの完成ではない。
新規/既存XLSXのDRAFTで「印刷ページを編集」を開き、縦方向の境界を編集・保存できる。
設定済み版の有効化とOCR受付・実行は `PRINT_LAYOUT_OCR_NOT_AVAILABLE` で拒否する。
1ページに設定した場合もこの新経路のOCRは未対応。既存1ページOCRは旧経路で維持する。
解除操作は新しいprintLayoutだけを削除し、legacy画像・layout・configを維持する。

## 一次情報とbaseline

開始HEAD: `d33e192cb4483fe3a2a857a2b5f51f046a8f8fbe`、worktree: clean。
前回の調査にあったdirty worktreeは現在の状態ではなかった。
`AGENTS.md` と `ocr-safety-contract.md`、現実装・テストを再確認した。
既存renderは `fitToPage=True`, `fitToWidth=1`, `fitToHeight=1`, `scale=None`,
`PageRange=1`。既存alignmentのX/Y別倍率とともに維持した。
実DB `backend/data/app.db` はread-only接続でrevision `0031_template_field_identity`を確認。
既存`template_versions.quality_json`と準備jobのJSONを使用し、migration追加・実DB更新なし。

変更前の専用OCR script: 194 passed。
XLSX import、版管理、XLSX出力、migrationの対象セット: 23 passed。
実APIへの課金呼出しは不要な変更であり、OpenAI/Google APIは使用していない。

## データと計算

- 原本はSHA-256を照合して読み取るだけ。新経路でXLSX/OOXMLを再保存しない。
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
- 原本結合セルを横切る境界を禁止。安全な元セル対応を証明できないGUI結合・manual矩形は未対応。

## 描画の限界

新経路はPillow/MeiryoまたはNoto CJKによるセル構造の参考描画。
Excel/LibreOfficeの文字配置、太さ、表示書式、フォント、組版を完全再現しない。
連続編集ビューと物理ページビューを分離し、UIにも参考描画・実OCR未対応と表示する。
編集中のSVGは配置案、保存時のPNGはサーバーの参考描画。どちらも同じ矩形座標を使うが、
ブラウザーとサーバーで文字の組版は同一とは限らない。
日本語フォントがなければ503で拒否し、文字欠落した画像を保存しない。
印刷入口は保存済みの全ページ画像を表示する。旧1ページPDFを新設定のPDFとして返さず409にする。
出力XLSXの印刷設定・セル出力処理は変更していない。

以下は検出して保存を拒否する: 離れた複数print area、print titles、header/footer、
横複数ページ、非表示行/列、図形/画像/フォーム部品、数式、書式付き数値/日付、
回転/セル内縮小文字、元セルを確定できない編集矩形。
自動で非表示部分や式の値、失われたページ数を推定しない。
OOXML属性は存在する辞書/存在しないnullを保持し、openpyxl既定値で埋めない。

## 所有権・snapshot・競合

TemplateVersionのquality内が正本。公開版をPUT/DELETEできない。
準備jobの`config_snapshot_json.printLayout`に同じページ定義・画像SHA/参照を保存する。
これは編集可能なDRAFT準備jobであり、受付済みOCR jobのsnapshotではない。
過去OCR jobを更新しない。新形式のOCR受付snapshot/入力ページ対応は未実装。
fork時はセル/画像identity・パスを新版へ分離し、layout hashと準備snapshotを更新する。

GETで版/現layout/printLayout/inputRevisionのhashをrevisionとして返す。
試算・保存・解除はbaseRevisionを照合。保存は描画後にもmutation lock/BEGIN IMMEDIATE内で
再照合し、競合した古い結果を採用しない。画像は新しいUUID付きパスに作り、上書きしない。
DB rollback時に作成済み未参照画像が残る可能性はあるが、旧画像・旧版を破壊しない。
画像取得はlayout hashと画像SHAを確認。layout編集後の古い画像は409で拒否する。
既存検証fingerprintにもprintLayoutを含める。旧版で未設定なら従来fingerprintは変わらない。

パッケージ形式v1へのページ設定の組込みは未実装。書出しを明示拒否し、metadataを落として
他PCへ運ぶ経路は作らない。旧パッケージ・Drive/Pickerは従来どおり。

## API

すべて既存の認証・バックアップ排他ミドルウェア内。

- `GET /api/v1/template-versions/{id}/print-layout`: 原本論理構造、設定、revision
- `POST .../print-layout/preview`: 保存しないページ計算
- `PUT .../print-layout`: DRAFT設定/全ページPNG/準備snapshotの保存
- `DELETE .../print-layout?base_revision=...`: 新設定を解除し旧経路を使用
- `GET .../print-layout/pages/{number}/image`: hash検証した保存PNG

## 実OCRを妨げている境界

`ImageExtractionService.process`は厳密に1入力を要求する。単一config/canonical画像に
normalize/register/cropを実行し、単一ExtractionOutputを渡す。ImageLoaderの入力はJPEG/PNG。
`DocumentOcrExecutionService`はこの単一出力をOCR workflowへ渡す。
結果/補正revision/確定snapshot/レビュー画像・Spreadsheet出力はjobに結び付く。
ADD_PAGE/RESCAN_PAGEは既存文書reconciliationであり、今回のtemplate pageとの対応ではない。

残る必須実装は、1job内の入力ページ順序・欠落確認、ページ別config/crop/画像の受付固定、
ページを含むphysical identity、OCR結果の集約、ページ別レビュー/補正/確定/retry、
出力時の同一帳票統合、これらの保存response・実画像比較。
現在はこれらを推測実装せず、新印刷設定を実OCRへ渡す直前に拒否する。
将来の実OCR対応では原本描画の忠実性とスキャン位置合わせも再評価する必要がある。

## 検証結果

以下はこの変更の検証であり、新経路の実OCR精度保証ではない。

| 検証 | コマンド/結果 |
| --- | --- |
| OCR contract変更前/後 | rootで `backend\.venv\Scripts\python.exe scripts\verify_ocr_contract.py`。194 passed → 194 passed |
| 既存関連baseline | backendで `.venv\Scripts\python.exe -m pytest tests/unit/test_spreadsheet_template_import_service.py tests/integration/test_template_versioning.py tests/unit/test_spreadsheet_template_output_service.py tests/integration/test_migration.py -q -o addopts='' -p no:cacheprovider --basetemp=<unique>`。23 passed |
| 新規ページ計算/API | `tests/unit/test_print_layout.py` と `tests/integration/test_print_layout_api.py`。24 passed。binding照合の追加後もAPI 6 passed |
| backend全体 | backendで `.venv\Scripts\python.exe -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=<unique>`。最終575 passed / 1 skipped。skipは既存の実OpenAI opt-inテスト |
| frontend全体 | frontendで `npm test`。235 passed |
| 最終UI変更後 | `npm test -- PrintLayoutEditor.test.tsx TemplatePage.test.tsx`。29 passed |
| frontend build/typecheck | `npm run build` 成功。既存500KB超chunk警告あり |
| Ruff | 新規7 Pythonファイル通過。変更済み既存PythonファイルはHEAD内容との指摘比較で新規0件。XLSX importerの既存25件は未解消 |
| mypy | backendで `.venv\Scripts\python.exe -m mypy app --no-pretty`。36 errors / 6 files。未通過。新規5モジュールの限定検査はopenpyxlの型stub不足3件のみ。`--follow-imports=silent --disable-error-code=import-untyped`を明示した限定検査では0件。全体成功とは扱わない |
| 差分 | `git diff --check` 通過 |

新規テストは共通scale、ページownership、保存済み範囲の維持、manual breaks優先、結合横断の拒否、
極小scale警告、属性欠落null、高度機能拒否、画像の画素位置/ハッシュ決定性、元XLSX不変、
旧job不変、競合/公開版書込拒否、途中描画失敗、古い画像拒否、版複製のID/パス分離を含む。

実ブラウザーは合成XLSXを専用の検証DBへ取り込み、実LibreOfficeによる旧プレビュー生成を経て確認。
2ページ自動配置、31行目での保存、Page 2=B32:D63、共通scale90.9%、保存PNG表示、
改ページ線を31→32行へ実ドラッグ、取消→再取得で31行へ復帰を確認した。
確認操作中のconsole warning/errorは0件。検証用サーバーとタブは終了した。
実データDB・実ユーザー帳票の変更、実OpenAI/Google API、物理印刷は実施していない。

## 変更ファイル

- 新規backend: `domain/print_layout.py`、`services/print_layout_source.py`、
  `services/print_layout_renderer.py`、`services/print_layout_service.py`、`api/v1/routes/print_layout.py`
- 接続/保護: `api/v1/router.py`、`api/v1/routes/templates.py`、`repositories/template_repository.py`、
  `services/job_service.py`、`services/extraction_service.py`、`services/layout_service.py`、
  `services/template_version_service.py`、`services/spreadsheet_template_import_service.py`、
  `template_packages/canonical_mapper.py`
- frontend: `src/printLayout.ts`、`src/components/PrintLayoutEditor.tsx`/`.css`、
  `src/api.ts`、`src/pages/TemplatePage.tsx`
- tests: `backend/tests/unit/test_print_layout.py`、`backend/tests/integration/test_print_layout_api.py`、
  `frontend/src/components/PrintLayoutEditor.test.tsx`
- docs: 本書と `docs/ocr-safety-contract.md`

backendのパスは`backend/app/`を基準とする。staging/commit/pushなし。
既存OCRのprovider/model/prompt/schema/association、既存mapping/crop、補正・確定・XLSX出力は
変更していない。旧経路のテストとsnapshot保護により既存OCR contractを維持した。
新しい印刷設定に対する明示的な受付拒否を追加したが、統合要件全体の完成ではない。
