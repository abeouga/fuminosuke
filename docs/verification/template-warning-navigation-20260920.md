# テンプレート警告パネルの実ブラウザ確認と表示修正

確認日: 2026-09-20

## 今回の修正

- 固定「要対応」パネルの幅を、スクロールバーを含む `100vw` から表示領域基準へ変更した。狭幅では左右12pxを指定する。
- パネルの見出しを残し、高さ制限時は警告一覧だけを内部スクロールさせる。
- 対象セルを安全に特定できない場合、既存の「対象セルを特定できないため工程2を開いた」に、帳票プレビューから手動選択する案内を追加した。
- 実データに存在した `cells.<cellId>.markSets` の短いfield pathについて、推測選択せず、警告保持・追加通信なしで工程2だけを開く回帰テストを追加した。

## 維持した境界

警告の入力はidentityによる除外後の `visibleMappingWarnings` のまま。
セル誘導は `fieldErrors` の `cells.<cellId>.markSets.<setId>.options` と現在layoutの一致を要求する。
日本語の警告本文からIDを抽出せず、欠落したsetIdを補完しない。
保存済みXLSXページへの誘導も既存のfield bindingによる判定を維持する。

今回変更した実装はパネルのCSSと案内文だけであり、選択・ページ切替の条件や保存処理を変更していない。
backend、API、types、mapping生成、保存payload、physical identity、boundingBox/crop、revision、OCR pipelineは変更していない。
既存OCR contractを維持した。これはOCR認識精度や本番qualificationを再認定する検証ではない。

## 自動検証

frontendディレクトリで実行:

- `npm test -- --run TemplatePage.test.tsx LayoutEditor.mappingValidation.test.tsx LayoutEditor.pages.test.tsx`: 3ファイル、80件成功。
- `npm test -- --run`: 35ファイル、405件成功。既存の保存・下書き・identity設定テストを含む。
- `npm run build`: 成功。500 kBを超えるbundleの注意あり。

リポジトリrootの `git diff --check` も成功（出力なし、終了コード0）。既存dirty差分の復元・整理・stage・commit・pushは行っていない。

## 実Chromeで確認した内容

開いていた `http://127.0.0.1:5173/` の既存タブをChrome拡張経由で再利用した。
ページ再読込やテスト用データの保存は行わず、開発サーバーの更新反映を使用した。

| 表示条件 | 実測結果 |
| --- | --- |
| 通常画面（有効幅1849px） | パネル幅380px、左1451px・右1831px |
| viewport幅420px（有効幅405px） | 左12px・右393px。修正前の左-3pxのはみ出しを解消。スクリーンショットでも確認 |
| viewport 320×300px（有効幅305px） | 左12px・右293px、上12px・下288px。一覧高さ231pxに対し内容高さ285pxで内部スクロール |
| 折りたたみ・再展開 | 「要対応 3件」を維持し、再展開で一覧が復帰 |
| MARK警告クリック | 工程2の見出しへfocus。警告3件と手動操作案内を保持し、誤ったセル強調なし |

クリック後のNetwork観測ではGETのhealth/workspace-syncだけが発生し、保存・mapping・OCR API呼出しはなかった。
取得したconsole errorは0件。確認後はviewport overrideを解除し、元の通常画面へ戻した。

## 実データによる確認の限界

開いていた版の警告は `cells.xlsx_D33.markSets` で、該当セルのmarkSetsは空だった。
必要なsetId/optionsまでの構造化pathがないため、自動セル選択を行わないのが指定された安全動作である。
この状態を成功誘導に見せるための推測・データ改変は行っていない。

正しい構造化pathでのセル選択・強調と、別の保存済みXLSXページへの切替は自動テストで確認済みだが、
今回開いていた実データでは成功経路の実ブラウザ確認は未実施である。
画面全体の既存の狭幅横スクロールは今回のパネル修正とは別の課題として残る。
