# テンプレート警告パネルの実ブラウザ確認と表示修正

確認日: 2026-09-20

以下の初回記録は当時の仕様と検証結果である。後続の「セル単位フォーカスの追加」が現在の仕様を示す。

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

## セル単位フォーカスの追加（同日・後続作業）

Phase 1で既存実装と安全契約を確認した。開始時のHEADは `018326c5f`、worktreeはcleanだった。
既存Phase 1〜3実装を基準に、Phase 2でfocus requestを次の二種類へ分けた。

- `cells.<cellId>.markSets`: `mark-cell`。現在の編集layoutに同じIDのmarkセルが存在すれば、markSetsが空でも選択し、キャンバスへfocus・内部中央スクロール・一時強調する。選択肢エディタへはfocus・強調しない。
- `cells.<cellId>.markSets.<setId>.options`: `mark-options`。同じmarkセルとsetIdの存在を検査し、従来の選択肢エディタfocusへ進む。
- 複数pathが同じセルの異なるセットを指す場合はセル単位へ限定する。異なるセルを指す場合は選択しない。
- 不明path、存在しないセル、現在の編集layoutでmark以外へ変更されたセルは選択しない。

日本語本文からのID抽出、setIdの補完、保存・mapping生成は行わない。
ページ解決は保存済みfield/sourceParts bindingを使用する。変更は一時的なselection/focus/highlightとそのリクエスト解釈に限定し、既存OCR contractを維持した。

### Phase 3の検証

- 対象テスト: 84件成功。空markSetsのセルfocus、完全pathのエディタfocus、曖昧・不明pathの拒否、別保存ページへの誘導、下書きのrole変更保護を含む。
- frontend full test: 35ファイル、408件成功。その後、実ブラウザで見つかった下書きrole不一致のテストを1件追加し、対象84件で再検証した。
- `npm run build`: 成功。既存の500 kB超bundle注意あり。
- `git diff --check`: 成功。終了時の変更は今回の5ファイルだけで、stage・commit・pushは行っていない。
- 320px/420px幅の左右12px余白、内部スクロール、折りたたみ後の3件表示、再展開を既存Chromeタブで再確認した。viewport指定は解除した。

実Chromeでは同じ下書き版を開き、警告クリックでページ1からbindingに基づくページ2（2 / 4）へ移動した。
ただし、現在のlocalStorage下書きの `xlsx_D33` は `role=input`、保存版の同じセルは `role=mark` だった。
現在の編集layoutを優先する検査によりセル選択を拒否し、工程2へのfocusと案内表示になった。
下書きを消去したり、roleを変更して成功に見せたりする操作は行っていない。
この状態での自動セルfocus成功は未確認であり、現在layoutもmarkである成功経路は自動テストで検証した。

クリック後の通信はページ2画像GETと定期health/workspace-sync GETだった。原因確認のため別途layout GETを1回行った。
保存・mapping・OCR APIの呼出しはなかった。今回の変更はTemplatePage、LayoutEditor、関連テスト2ファイルと本文書である。

## 下書き不一致への共通対策と局所修復

同日後続依頼で、保存元を検証せず下書き全文を復元する共通経路を修正した。
保存元signatureをブラウザ内に記録し、旧形式・保存元不一致・結果不明の下書きは内容を保持したまま確認表示し、
明示採用まで自動保存と終了時の保存を停止する。詳細は[下書きの保存元照合](../template-background-save.md)。

実データでは `xlsx_D33` の保存版が `role=mark, source=detected`、下書きが `role=input, source=manual` だった。
正規化でroleを変更する旧連番キーの対象ではなく、現行コードに「空のmarkSetsならinputへ変換する」処理もない。
区分変更ボタンは `source=manual` を設定するが、過去の操作ログがないため、手動変更・旧実装・古い下書き等の
どの履歴から発生したかは断定できない。確定した不具合は保存元不明の下書きが無検証で復元・自動送信される経路である。

ユーザーの局所適用指示に従い、既存ChromeのlocalStorageで次を実施した。

- 版: `b92f4035-06be-4d09-8cc4-c0a55aba51ba`
- バックアップキー: `template-layout-draft:b92f4035-06be-4d09-8cc4-c0a55aba51ba:backup-before-role-repair-20260920`
- 変更: 元の下書きの `cells.xlsx_D33.role` だけを `input` から保存版と同じ `mark` にした。
- 変更前後と再表示後の比較で、上記1フィールドを除く全JSONが一致した。source、markSets、座標、結合、見出し、意味づけ、他セルは保持した。
- 保存版・API・DBへの書込みは行っていない。保存版は既にmarkだったため、修復対象はブラウザ下書きだけである。

修復した下書きを開き、警告クリックでページ2（2 / 4）、選択ID `xlsx_D33`、同IDのhighlight、
キャンバスfocusを実Chromeで確認した。画面キャプチャでも対象セルの強調と帳票表示を確認した。
選択肢エディタへfocusしていない。取得通信はページ画像とhealth/workspace-sync GETのみで、保存・mapping・OCR要求なし。
通常のDOM状態取得が2回タイムアウトしたため、同じChrome接続のCDP読取とスクリーンショットで完了を検証した。

他の下書き差分は自動採用せず、確認案内と自動保存停止を維持している。
これにより前節の「実データでのセルfocus成功未確認」は、局所修復後の当該セルについて解消した。

### 共通対策の最終検証

- `npm test -- --run src/components/LayoutEditor.autosave.test.tsx`: 13件成功。保存元不明・不一致の送信停止、明示採用、保存元が一致する正当な未保存編集の復元を確認した。
- `npm test -- --run`: 35ファイル、412件成功。TemplatePage、LayoutEditorの保存・下書き・identity・警告誘導テストを含む。
- `npm run build`: 成功。既存の500 kB超bundle注意は残る。
- `git diff --check`: 成功。
- backend・API・保存payload・mapping・physical identity・OCR処理は変更していない。共通変更は下書き復元時の確認と保存開始条件であり、既存OCR contractを維持した。
- 今回のコード・テスト変更はLayoutEditor、LayoutEditor.autosave.test、TemplatePage.test。文書は本記録とtemplate-background-saveを追記した。開始時の5ファイルの未コミット差分を保持し、stage・commit・pushは行っていない。

実ブラウザで未確認なのは、他テンプレート・他セルすべての組合せと複数タブ同時編集である。
当該セルの局所修復後の選択・強調・focus成功と、API書込みがないことは実Chromeで確認済みである。

## 意味づけ済みセルなし警告からのセル誘導（同日・後続作業）

先の保存DBだけを根拠に「`MARK_OPTIONS_REQUIRED` は誤警告ではない」とした判断は誤りだった。
DBの保存版は `role=mark` だったが、画面が編集していた正本はlocalStorageから復元した現在の下書きであり、
同じ `xlsx_D33` は `role=input` かつ保存先割当済みだった。保存版のfield errorを現在下書きへ無条件表示したため、
現在の画面に対しては誤警告になり、保存版で対象を解決してから現在下書きで拒否するためfocusも失敗していた。

仕様上の区別は次のとおりである。

- 記入欄（`input`）は保存する情報とのrelationまたは直接のtarget割当を必要とするが、選択肢は持たず、要求もしない。
- 選択欄（`mark`）だけがmark set、保存先、1件以上の選択肢を必要とする。
- 選択欄から記入欄へ変更した現在下書きに、保存版由来のmark選択肢警告を適用しない。

共通修正として、`LayoutEditor` が現在下書きのセル区分、mark set ID、未割当セル、選択欄の不完全状態、
利用可能な意味づけ件数を一時UI stateとして`TemplatePage`へ通知する。右上パネルは従来どおり
`mappingWarningsForIdentity`を通過した警告だけを入力とし、その後に現在下書きとの整合性を確認する。
構造化field pathが現在の記入欄を指す`MARK_OPTIONS_REQUIRED`と、現在下書きに有効な意味づけがある場合の
「読み取りに使用できる意味づけ済みセルがありません。」は表示しない。不明・不正形式で対応を確定できない警告は維持する。

「読み取りに使用できる意味づけ済みセルがありません。」が現在下書きでも有効な場合は、
現在下書きの未割当セル、次に不完全な選択欄を対象とし、Step 2へ移動してキャンバス内の同じセルを選択・focus・中央表示・一時強調する。
`MARK_OPTIONS_REQUIRED`の対象は引き続き`FieldMappingsResponse.fieldErrors`の
`cells.<cellId>.markSets[.<setId>.options]`だけから解決する。日本語警告本文、座標、配列indexからセルを推定しない。

実Chromeの既存タブをコード変更前に取得し、`xlsx_D33`が記入欄、保存先割当済み、未割当0件である一方、
上記2警告が残る状態を確認した。変更後も同じタブを再取得し、2警告が消え、独立した
「書類を見分ける項目が未設定です。」だけが1件残ることを確認した。新規タブ、保存、mapping再生成、OCR要求は行っていない。

- 対象テスト: 2ファイル69件成功。下書きのmark→input変更後の警告除外、未割当inputへのfocus、未知pathの維持、通信なしを含む。
- frontend full test: 35ファイル413件成功。
- `npm run build`: 成功。既存の500 kB超bundle注意あり。
- `git diff --check`: 成功。
- backend、API、mapping生成、保存payload、physical identity、OCR pipelineは変更していない。
