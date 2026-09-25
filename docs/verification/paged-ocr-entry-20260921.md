# 通常読み取り画面からページ別入力への接続（2026-09-21）

## 到達範囲

公開済みXLSXページ版を、通常の読み取り画面から既存の `OcrPageBindings` へ接続した。
本番OCR受付・送信の開放は未完了。画面へ進めることと、OCR受付可能であることを分離する。

前回の実作業で版 `b92f4035-06be-4d09-8cc4-c0a55aba51ba` は正式資格PASS・ACTIVEになったが、
通常画面はlegacyの `quality.layoutVerified` を必須にしていたため「編集中・OCR利用不可」と表示した。
さらに現 `JobPageService.catalog` は `available=false` を返し、
`_require_qualified_reference` も正式資格にかかわらず `PRINT_REFERENCE_NOT_VALIDATED` で拒否する。
フロント接続だけで本番OCRが開始できるという説明は誤りである。

## 変更

- `frontend/src/pages/OcrPage.tsx`: ACTIVE・printLayoutありのXLSX版はページ入力画面を開ける。
  新しいDRAFTがある版の操作制限と、legacyのlayoutVerified判定は保持する。
  カードに「ページ別の受付条件を確認」と表示する。mapping取得・validForOcr判定も維持する。
  未対応の書類ライブラリ文脈を捨てて新規paged jobへ送らない。
- `frontend/src/components/OcrPageBindings.tsx`: 同じ版の正式資格を読取APIで取得し、
  サーバーcatalogの受付可否・reasonと別表示する。資格PASSでavailableをtrueへ変更しない。
  版違いcatalogを拒否し、取得失敗の再取得と選択画面へ戻る操作を追加する。
  明示page ID/input ID bindingを維持し、hash計算中の版切替で旧要求を送らない。
  二重送信と受付成功後の再送を防ぐ。レビュー・確定が未実装という古い表示を訂正する。
- 上記2ファイルのテストを追加。

## 保護した契約

今回backend・API・DB・保存layout・mapping・qualification issuer・受付/実行gateは変更していない。
page ID、input ID、入力SHA、template revisionを既存multipart manifestへ渡す。
legacy OCRへのfallbackは追加しない。mappingId/targetPath/sourceRegionId、座標/crop、
provider/model/prompt/schema、受付snapshot、補正・revision・出力対応は不変。
既存OCR contractを維持した。実OCRの正確性を今回証明したという意味ではない。

## 検証

- 対象: `npm.cmd test -- src/pages/OcrPage.test.tsx src/components/OcrPageBindings.test.tsx`、29 passed。
- frontend全体: `npm run test -- --run`、35 files / 424 passed（追加テストを含む最終実行）。
- `npm.cmd run build`: 成功。既存500 kB chunk警告あり。
- `git diff --check`: 成功。
- 既存Chromeタブで変更前画面を取得。変更後は同じタブを再読込し、公開版選択→画像の準備へ→
  保存した4ページの入力欄、「この版の正式資格は有効です。」と
  `PRINT_REFERENCE_NOT_VALIDATED`・送信ボタン無効を確認。変更後の画面も取得した。
  新規タブなし。ファイル指定・OCR送信は行っていない。
- backend product未変更のためbackend full/契約scriptは今回未実施。
  OpenAI/Google APIの明示実行0回。既存バックグラウンド同期の通信回数は未計測。

## 本番開放へ残る実装と証拠

1. 実入力検査の根拠を固定する。`paged_crop_correspondence.py` は実cropと所有全体画像の
   一致を検証するが、局所的な位置合わせ誤差や実筆画clearanceは認定しない。
   `paged_safety_stages.py` の `INK_CLEARANCE` は検査項目の宣言であり実装ではない。
   未記入/記入済みの差分maskだけでは、罫線や印刷文字の残差と実筆画を分離できない。
   未測定・曖昧な欄は拒否する条件、独立した正解/故障fixtureを先に用意する。
2. 受付snapshotに束縛した実入力検査をprovider生成前へ接続する。
   page/input/reference SHA、mapping/crop対応、局所registration誤差、crop端/隣接領域の筆画を
   検証し、失敗時は該当page/fieldと根拠を保存する。既存の補正・成功ページ・retryを保持する。
3. 上記検査が実装・検証された後、正式資格の再検証をpaged受付へ接続する。
   `layoutVerified=true` の手動補完や、無条件拒否の単純削除で代用しない。
   失効/証拠改変/版競合/誤ページ/欠落入力/切れた筆画ではprovider送信0回を検証する。
4. 契約script前後・backend full・frontend回帰を実施し、現在の印刷範囲に合う実画像で
   受付→ページ別OCR→人間の修正→全ページ確定→XLSX出力を確認する。

過去スキャンの報告 `real-print-scans-20260913.md` は改ページ28/60/89の旧版に対応する。
現在対象の29/61/94へ同じ入力を推測bindingしない。過去報告でも23/151領域のcrop端近傍に
候補があり、印刷残差を含むため `inkClearanceVerified=false` である。
この問題は利用者の意味づけ設定不足ではなく、本番の実入力検証方式・実装・証拠の不足である。

## 指定された実記入画像の再診断（2026-09-21）

`C:/Users/hanam/Desktop/実印刷記入済み/1.png`〜`4.png`を目視確認し、
現DBをSQLite read-only/query-onlyで照合した。現在ACTIVEの範囲は上記29/61/94のまま。
記入済み第4面はD-1/D-2/D-3を含むが、現在版の第4ページはD-2から始まる。
この画像を現在版の同じページ番号へ割り当てることはできない。

旧版 `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` は現在ARCHIVEDで、
旧範囲28/60/89と保存参照画像を保持している。この旧版に対する診断として
既存 `scripts.inspect_print_scans` を4枚の明示bindingで実行した。
一時生成物はプロセスのTEMP/TMPをDドライブへ指定して分離した。

- 全4枚が `ALIGNED_DIAGNOSTIC_ONLY`。対象領域60/51/37/3、合計151。
- ORB段階のmean reprojection errorは順に1.2866/1.2444/1.5371/1.4363 px。
  最終局所最大誤差や筆画clearanceの認定値ではない。
- 全ページ `inkClearanceVerified=false`、`productionQualified=false`。
- `originalUnchanged=true`。旧版・原画像・参照・configを変更していない。
- 診断report: `D:/llm-ocr-archive/llmocr-scan-baseline-x9m4gqan/report.json`。
- 実外部API 0回。新規OCR job・承認・公開版の変更なし。製品コード変更なし。

この4枚は旧版用の安全検査開発・診断資料には使えるが、現在公開版の正常入力E2E証拠には
使えない。現在の印刷版で再印刷・記入・スキャンするか、旧印刷範囲を維持した別DRAFTの
作成・再検証・承認を選ぶ必要がある。後者を無断実行して現行公開版を置き換えない。

## 旧印刷範囲向け下書きの作成（2026-09-21）

利用者の明示承認後、既存Chromeの「設定を確認・修正」から通常の版コピーを実行した。
系列 `9dc80bd9-0070-4754-a271-7ff274729e14` に
V2 `e3286670-4190-436a-a1b6-9f0d7e278b7c`（DRAFT）を作成した。
V1 `b92f4035-06be-4d09-8cc4-c0a55aba51ba` はACTIVEのまま。
V1のlayout/quality JSON SHAは作成前後一致。287セル・150接続を継承し、
通常forkのID置換を適用したlayout全体とV2の保存layoutが完全一致した。
この比較はコピー順と既存remap規則の検証であり、OCRの推測bindingではない。
既存タブのV2画面を取得した。再読み込み・新規タブ・OCR送信なし。

旧印刷範囲への変更は未保存。仮想見出し2件（作成日、名前）が `A1:AK29` に所属し、
3件のrelationが参照している。提案rangeを渡した既存 `validate_page_headings` の
メモリ内検証は `PRINT_VIRTUAL_HEADING_PAGE_INVALID` で拒否した。実DBへの失敗要求は送信していない。
現契約は所属rangeが消える場合に明示削除を要求するため、単純削除では意味づけ保持を満たせない。
見出しID・名前・接続を保持し、明示指定した新ページへの所属変更を改ページと一括検証・保存する
契約拡張が必要。現guard削除やDB直書きでは代用しない。
この拡張の合意前に削除・ページ再所属・資格発行・有効化は実行しない。

## 承認後の改ページ・見出し所属変更

上記は拡張合意前の記録。利用者承認後、明示ID/旧range/新rangeによる一括保存を実装し、
V2へ28/60/89の改ページと仮想見出し2件の所属変更を適用した。
正式印刷用ファイル生成と正式資格PASSまで確認済み。V1はACTIVE、V2はDRAFTを維持。
指定4スキャンのV2診断は全ページ位置合わせ成功だが、筆画clearanceは未認定。
変更境界・SHA比較・検証結果は [仮想見出しを保持した改ページ保存](page-heading-repage-20260921.md) を参照。
