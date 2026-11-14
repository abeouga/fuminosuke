# チェックリストOCR

帳票のチェック欄（紙へのペン記入、Excelセル内の記号・チェックボックス）を読み取る機能の使い方と対応範囲。

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
| ページ別画像 | checklistのbinding/crop対応あり。既存の準備・全ページ確認が必要 |
| 複数ページxlsx直接入力 | 未対応。ページ別画像を使用 |

セル内チェックボックスの構造は [MicrosoftのXFControls仕様](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-xlsx/3fc90597-a4e9-49d8-bd0a-896b674ed9b4) と [XFComplements仕様](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-xlsx/cb97d7fc-1434-498c-9bd3-aa5fae4e677c) に従います。

## 実装境界

新しいchecklistモードの型・候補検証、OCR指示・決定的割当、通常/pagedレビュー、テンプレート設定ボタン、Excelセル内checkbox読取/出力、単一ページxlsx入力はchecklistモード専用の処理。mappingId/targetPath/sourceRegionId、ASSOC、画像合わせ、crop生成、保存revision、確定snapshotは既存経路を使用する。

## 検証で確かめていないこと

- 実際の筆記・撮影による精度評価は行っていない。紙へのペン記入は手書き風の合成画像で確かめた。
- Excelデスクトップアプリで実際にクリックする操作は確かめていない。生成ファイルのOOXML定義・boolean状態・読込/出力と画像描画で確かめた。
