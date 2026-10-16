# チェックリストOCR

## 操作

1. テンプレートの記入欄を選び、「読み取り方法」→「チェックリストを読む」を選択します。
2. 保存先とチェック項目を設定します。複数の印を1つの欄で読み取れます。チェックボックスだけの欄は「チェック済み」など候補を1つにします。
3. ペンで記入した帳票画像を読み取ります。Excelテンプレートでは、同じ配置の記入済みxlsxも選択できます。
4. 確認画面で原画像と各チェックを照合し、修正を保存して確定します。全未選択と判別不能は別状態です。
5. 確定後にXLSXを取得します。セル内チェックボックスを含む原本ではチェック機能と原sheet名を保持します。

## 対応範囲

| 入力 | 対応 |
| --- | --- |
| 紙へのペン記入 | チェック欄の画像OCR。手書き風合成画像で検証 |
| Excelセル内の☑・□等 | 画像OCR、および同じExcelテンプレートのxlsx直接入力 |
| Excelセル内チェックボックス | FeaturePropertyBag形式。1セル1候補。TRUE/FALSEと表示定義を検証 |
| 旧フォーム/ActiveX | xlsx直接入力は拒否。画像化した欄は画像OCR |
| 数式・外部リンク | xlsx直接入力は拒否。値へ変換して使用 |
| ページ別画像 | checklistのbinding/crop対応を追加。既存の準備・全ページ確認が必要 |
| 複数ページxlsx直接入力 | 未対応。ページ別画像を使用 |

セル内チェックボックスの構造は [MicrosoftのXFControls仕様](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-xlsx/3fc90597-a4e9-49d8-bd0a-896b674ed9b4) と [XFComplements仕様](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-xlsx/cb97d7fc-1434-498c-9bd3-aa5fae4e677c) に従います。

## 作成したテンプレート

- チェックリスト検証用・申請受付: template 042cfc9b-771d-4264-9fc4-5724b7795971、ACTIVE v2 68d49fb2-5323-4b31-8820-86ffcf6a2461。
- セル内チェックボックス検証用・申請受付: template eeccf11e-8663-483a-a634-5a70022afb92、ACTIVE v1 a63a9a9a-686f-43ee-80b8-d1e2ec09cc2f。
- 検証用Excel: outputs/checklist-20261004/。受付番号、複数選択、1件選択、全未選択、同意確認を配置。
- 今回のExcelでは既存取込座標が実際の印刷画像からずれたため、作成した検証用テンプレートの欄を実罫線に合わせて設定した。既存テンプレートの座標は変更していない。一般のExcel取り込み座標の全面修正は含まない。

## 実装境界

新しいchecklistモードの型・候補検証、OCR指示・決定的割当、通常/pagedレビュー、テンプレート設定ボタン、Excelセル内checkbox読取/出力、単一ページxlsx入力を追加。mappingId/targetPath/sourceRegionId、ASSOC、画像合わせ、crop生成、保存revision、確定snapshotは既存経路を使用する。

検証スクリプト、合成入力、途中応答、画面証跡は D:/llm-ocr-archive/checklist/20261004/ に保存し、製品に使い捨てテストを追加していない。

## 検証記録

- frontend: npm run build、TypeScript/Vite成功。
- backend: 変更対象のruff F/I、短い境界検証11項目成功。
- 紙のペン記入: 合成画像CHK-002で複数/単一/全未選択が期待どおり。実筆記・撮影ではない。
- Excel記号画像CHK-001: 複数/単一/全未選択が期待どおり。
- セル内checkbox付きxlsx: 初回はproviderがreadable/nullを返し、正しくuncertainになった。専用指示でvへの返却を明示後、同じ入力で期待どおりの結果を取得。
- ブラウザー: 書類識別確認→チェック変更→保存→再表示→元の選択へ戻して保存→確定を確認。
- 起動/停止/再起動: 5180/8010、停止後listener/旧PIDなし、再起動後200、既存の確定済み結果不変。
- 全件unit/E2E、実筆記・実撮影による精度評価、paged実レンダラーを通した新規OCRは未実施。ページ別対応は短いbinding検証に限定。
- OCR実APIは合計8回（4ジョブ、各2回）。合成画像作成の画像生成1回は別。ASSOC照合は20/20一致、不一致0。既存OCRのモデル設定は変更していない。
- 単一ページの確定前出力拒否、判別不能チェックリストの確定拒否、補正revision競合拒否を実APIで確認。native結果は再編集→未選択保存→確定→再編集→選択保存→再確定を検証し、最終revision 3で確定済み。
- native XLSX出力でTRUE/FALSEの両方を確認。対象sheetのcell XML以外のZIP partは原本とbyte一致。書式・FeaturePropertyBag・原sheet名を保持。補正前後のmappingId/sourceRegionId/boundingBoxは不変。
- 記入欄以外の値変更、数式、書式なしboolean、不正なcheckbox値の4入力をHTTP 422で拒否。外部API呼び出しなし。
- Excelデスクトップアプリで実際にクリックする操作は未検証。生成ファイルのOOXML定義・boolean状態・読込/出力と画像描画を検証した。

