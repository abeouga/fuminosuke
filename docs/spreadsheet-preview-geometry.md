# XLSXプレビュー座標・ページ編集の検証（2026-09-09）

新規取込の矩形座標を修正し、ページ編集に拡大・境界操作を追加した。
**依頼全体の完了条件は未達。キャリア帳票そのものの境界保存は、既存のunsupported条件で拒否される。**
これを解除する変更やrenderer qualificationの変更は行っていない。

## HEAD・変更範囲

開始・終了HEADはいずれも `b880d76484e3c687b9efc3032fcc5d6cae1c0ff9`。
staging・commit・pushなし。前回の印刷用XLSX/PDF取得の未コミット差分と、開始前からの
`frontend/src/styles.css` の変更を保持した。以下が今回の変更ファイルである。

- `backend/app/services/spreadsheet_preview_geometry.py`（新規）
- `backend/app/services/spreadsheet_template_import_service.py`
- `backend/app/services/layout_service.py`（保存寸法を復元へ渡す1行）
- `backend/app/services/print_layout_service.py`（同上）
- `backend/tests/unit/test_spreadsheet_preview_geometry.py`（新規）
- `frontend/src/components/PrintLayoutEditor.tsx`
- `frontend/src/components/PrintLayoutEditor.css`
- `frontend/src/components/PrintLayoutEditor.test.tsx`
- `docs/ocr-safety-contract.md`
- `docs/spreadsheet-preview-geometry.md`（本書）
- `docs/spreadsheet-preview-geometry-evidence.json`（新規）

開始時差分のコピー・ハッシュ、原本の診断用コピー、PDF/PNG、Office FODS、before/after比較HTML、
測定スクリプトは `C:/Users/hanam/.codex/tmp/xlsx-geometry-20260909/` に保存した。
実帳票の画像・セル文字をrepositoryへ追加していない。

## 原因と正常帳票との差

最初の不一致は **XLSXセル寸法→プレビュー座標** の計算で発生していた。
`_parse_workbook` は列幅を `max(12, width*7+5)` px、行高を
`max(14, heightPt*96/72)` pxとし、縮小後の各行・列を丸めてから累積していた。
さらにOfficeの列幅換算・producerに応じた行高丸めと、この固定換算は一致しない。
既存の外周罫線によるX/Y補正で外周を一致させても、内部の寸法比の違いが残る。

| 項目 | 債権者登録依頼書 | 給水装置台帳等複写申込書 | キャリア・プラン作成補助シート |
| --- | --- | --- | --- |
| worksheet dimension | A1:CO77 | A1:AA1000 | A1:AJ105 |
| legacy取込対象bounds | A1:BV75 | A1:AA31 | A1:AK102 |
| 矩形数（変更前→後） | 226→226 | 74→74 | 367→367 |
| 明示列幅の例 | 0.875 / 1.625 | 2.29 / 3.29 / 4.71 | 1.88671875 / 3.6640625 |
| Office読込後列幅の例（pt） | 4.8 / 8.95 | 12.7 / 18.25 / 26.15 | 11.0 / 20.15 |
| 行高の差 | 2.25pt等に旧14px下限が作用。9.95→9.75ptも実測 | 対象行は下限を超え、Office読込後の行高も同じ | 20.1→19.5pt等 |
| 非表示 | なし | なし | A列 |
| 用紙・向き | A4・縦 | A4・縦 | A4・縦 |
| 原本print area | A1:BV75 | 未指定 | 原本設定はevidence参照 |
| legacy PDFページ数 | 1 | 1 | 1 |

給水帳票の1000行は既存の安全な空末尾検査で31行までに絞られる。
今回はused range切捨て・merged cells・隣接セルの統合・セルの役割判定を変更していない。
給水帳票のページ編集用 `read_source` は別経路でcalculate_dimensionを使うため、
20,000セル制限に抵触する。この既存差異も調査で確認したが、過去printLayoutのrangeを
変える修正は今回含めていない。

3冊の原本SHAはevidenceに記録し、終了時にも一致を確認した。
row heights、grouped column widths、hidden、全merged ranges、margins、page setup、
sheetFormat、print area、raw/align後の罫線boundsはevidenceのsourceMetadataに収録した。

## 修正方法と保存版の独立性

新規取込時に、既存legacyプレビューが準備する一時XLSXコピーを、同じOffice profileで
PDFおよびFODSへ変換する。FODSの物理行高・列幅を既存 `read_office_dimensions` で
整数twipへ戻し、pt単位で取得する（1pt=20twip、1in=72pt）。
FODS取得が失敗した場合は取込を失敗させる。推測寸法へ自動的に戻さない。

`quality_json.spreadsheetPreviewGeometry` に `spreadsheet-preview-office-v1`、原本SHA、
シート名、bounds、行高・列幅を保存する。元XLSXのmerged rangeを使って端点を累積し、
各セルの座標だけを置換する。細い列・低い行に表示上の最小寸法を強制しない。
丸めはセル端点で行う。既存の外周画像合わせは維持し、新たな固定倍率・offsetは追加しない。

snapshotがある版を「元のExcelセル構造に戻す」場合やprintLayoutの元セル対応を検証する場合は、
保存寸法と原本SHAを使う。現在のOfficeで再測定しない。snapshotのない版は従来計算のままである。
TemplateVersionの既存fork経路はqualityを複製するため、親版や過去jobを変更しない。
DB schema、version semantics、新しいpage breakモデルは追加していない。

legacyの横1×縦1プレビュー設定・PageRange=1・canonical 1240×1754を維持した。
新規printLayoutの `lo-import-twip-v1` や資格判定とは別のsnapshotである。
今回のlegacy PDF/FODS描画成功を隔離rendererの成功・本番認定とは扱わない。

## 単位・画像・ブラウザの確認

元行高はpt、元列幅は文字幅単位であり、固定7px換算だけではOffice読込後寸法を表せない。
新しい計算はOffice物理寸法(pt)からcanonicalへ変換する。EMUはこのセル座標経路で使っていない。
原本のscale/fit値はevidenceに保存。legacy一時コピーはfitToWidth=1、fitToHeight=1、scale未指定。
これは印刷用TemplateVersionのcommonScaleとは異なり、新規取込時には保存printLayoutはない。
約150dpiのA4 canonicalにPDFを描画し、最終画像を1240×1754に揃える従来経路を維持した。

実ブラウザでキャリア帳票のcanvasは内部1240×1754、表示771.28125×1090.984375 CSS px。
画像と矩形は同一canvasへ描画され、canvasのtransform=none、padding=0、border=0だった。
DOMのimg要素としてのnaturalWidthはないが、投入画像のデコード寸法は1240×1754。
wrapperのpaddingやCSS全体縮小で矩形だけがずれる構造は確認されなかった。

## 誤差の測定と限界

同じ原本・同じ実PDF/PNGを対象に、旧計算と新計算を比較した。
四辺罫線がある幅45px以上・高さ20px以上の矩形を対象に、端の3pxを除いた辺の70%以上で
濃度180未満の罫線を検出する。新予測の±5px内から対応する辺を選び、旧・新を同じ実測辺で採点する。
未検出を誤差0とは扱わない。これは網羅的な独立トポロジー認定ではなく、実帳票の部分測定である。

| 帳票・測定数 | 最大X誤差 | 最大Y誤差 | 最大幅誤差 | 最大高さ誤差 |
| --- | --- | --- | --- | --- |
| 債権者・59/対象68矩形 | 73.5→1.5px | 13.0→2.5px | 81.0→3.0px | 14.5→1.5px |
| 給水・10/対象10矩形 | 2.5→1.5px | 1.5→1.5px | 1.5→1.0px | 1.5→1.0px |
| キャリア・8/対象8矩形 | 3.5→3.5px | 4.0→1.0px | 7.0→7.0px | 1.0→0.5px |

全226/74/367矩形の完全一致を証明した数値ではない。細い枠、部分罫線、文字だけのセルは対象外。
キャリアの横方向残差は今回の2冊のgeometry完了判定に混入させず、残差として記録した。
実測値・セルID・平均絶対誤差はevidenceのmeasurementsに収録。

## ページ編集ができなかった理由と操作

キャリア帳票はlegacyの全体fitで罫線幅が229px程度になり、全体previewが小さかった。
ページ編集は `source.unsupported.length > 0` によりボタン・fieldset・preview APIを無効化する。
実原本に非表示A列とD3等のセル内縮小があり、誤ったdisabled属性だけが原因ではなかった。
さらに元のNormal fontは現在の確認対象外であり、これらを除去すれば直ちに利用可能とは保証しない。

既存UIに以下を追加した。

- 横幅基準の連続シート。縦横スクロール、50〜300%の拡大縮小、倍率リセット。
- 「追加する境界（行の後）」で行を選択して「改ページを追加」。未選択なら表示中の中央に近い合法境界を使う。
- 青い「移動」ハンドルのドラッグ、または各Pageの最終行選択。merged cell内部の行は候補に出さない。
- 隣の境界を越えない移動、個別削除、ページごとの行範囲。
- 上部の保存・取消・再取得、未保存表示、保存済みページ数。保存中は既存のbusy排他を維持。
- 未対応・DRAFT以外・テンプレート側の未保存状態を区別した説明。未対応でも表示の拡大は可能。

境界の正本・payloadは従来の `breakAfterRows` とサーバーが返す `pages[].startRow/endRow/range`。
既存 `PUT /api/v1/template-versions/{id}/print-layout` にbaseRevisionを渡し、
TemplateVersionのquality.printLayoutへ保存する。409時は入力を保持し、再取得は未保存時に無効化する。
API、revision照合、merged crossing検証、DRAFT条件、qualification条件は変更していない。

## 検証結果

- 変更前/変更後 `scripts/verify_ocr_contract.py`: 各194 passed。
- geometry対象（新規8件、既存geometry calibration・import testsを含む）: 72 passed。全体テスト収集後にFODS失敗テスト1件を追加し、最新の新規testファイル9件もpassed。
- frontend全体: 24 files / 254 passed。追加4件はzoom、明示行追加・保存・再取得、scroll/zoom座標でのdrag、閲覧専用・合法境界なし。
- frontend build: 成功。既存の500kB chunk警告あり。
- 新規Python helper/testのRuff: 成功。既存変更先のlintは開始前の診断と照合し、新規診断なし。
- 新規helperのmypy（既存同様stub不足を除外）: 成功。既存importer/layout_serviceには既存型エラーが残る。
- backend full tests: **762 passed, 1 skipped**、582.53秒。既存skip維持。収集後追加の1件は上記9件で検証。
- `git diff --check`: 成功。開始差分と今回差分の範囲を確認。

主な実行コマンド（backendのpytestは毎回異なる専用basetempを指定）:

```powershell
backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py
# backendディレクトリ
.venv/Scripts/python.exe -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=C:/Users/hanam/.codex/tmp/xlsx-geometry-20260909/pytest-full
.venv/Scripts/python.exe -m pytest tests/unit/test_spreadsheet_preview_geometry.py -q -o addopts='' -p no:cacheprovider --basetemp=C:/Users/hanam/.codex/tmp/xlsx-geometry-20260909/pytest-final-focus
# frontendディレクトリ
npm test
npm test -- --run src/components/PrintLayoutEditor.test.tsx
npm run build
```

実UIは専用DBのbackend 8018 / frontend 5178で確認。本来の稼働DBは変更していない。
検証後、専用serverと検証用ブラウザを終了した。
キャリア原本をブラウザから投入し、取込成功、ページ編集、unsupported表示、125% zoom、
連続領域scrollTop=680を確認。表示SVGは1200×5944.89、scroll枠は525.52×680だった。
境界追加・保存は実原本のunsupportedにより実行不可。未検証を成功とは扱わない。
対応済み人工帳票（90行、既存結合B40:C42）では取込→ページ編集→20行後へ追加→22行後へ実ドラッグ→不要な境界を削除→保存→再取得を確認。DBでも A1:D22 / A23:D35 / A36:D90 の3ページを確認した。40・41行は結合内部として候補から除外された。

保存バーの固定表示が既存ヘッダーと重なり、クリックがホームへ届く問題を実操作で検出したため、固定表示を削除した。修正後は保存成功まで実測。最終CSS変更後のfrontend対象9件とbuildも成功した。

## 保持した契約・未解決事項

セル自動結合、AIによる意味推定、細かいセル削減を追加していない。多数矩形は元構造に応じて許容する。
既存の1000矩形上限や役割分類・装飾セル判定も変更していない。必要な入力領域の結合はExcel側で行う。
OCR認識、provider、PaddleOCR、OpenAI、association、mapping、correction、review、confirmed、
Google Sheets、過去job snapshotは変更していない。新規テンプレートの矩形座標は意図的に変更したため、
OCR-sensitiveなgeometry修正として契約検証を実行した。

本番 `environmentQualified=false`、`productionQualified=false`、ACTIVE化・paged OCRの拒否を維持。
既存OCR contractを維持したが、複数ページOCR全体の完成・認定を意味しない。外部OpenAI/Google API呼出し0回。

残る事項は、キャリア原本の非表示・高度な書式・未確認fontへの安全な対応、実原本でのページ境界保存、
細い/部分罫線を含む全矩形の網羅的測定、既存保存版の明示的なgeometry移行である。
横複数ページ、縦横2次元分割、新しい分割アルゴリズム、renderer隔離再設計、qualification緩和、
印刷成果物生成の大規模再設計、無関係なrefactorは意図的に対象外とした。
