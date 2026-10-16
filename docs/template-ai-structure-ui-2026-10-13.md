# AIテンプレート矩形差分の画面接続

修正4の画面接続と専用環境でのChrome受入を完了した。AIが提案する新設・変更・分割・結合・重複除去の枠を原画像で確認し、「案を反映」で下書きへ反映、「確認して保存」で保存する。独立審査と実Lunaの品質・速度評価は未完了であり、製品ゴールの達成とは扱わない。

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。実装モデルはGPT-6.1 Sol / High。実API使用・通常予約0回、共有台帳432/924・残492回を維持した。製品APIのGPT-6 Luna、共通保存、backendの物理検査・保存契約は[修正3](template-ai-structure-v1-2026-10-13.md)から変更していない。

## 画面と変更境界

- 追加は緑、変更後は青、変更前は灰色破線、除去予定は茶色破線とし、文字も添える。複数枠を同時表示し、折り畳んだ差分一覧から個別の「この枠を表示」、未解決欄から「位置を表示」を使える。
- 新設枠は表示専用の座標として扱う。プレビューだけでセル選択・layout・mappingを書き換えず、受付時のbindingへ固定する。
- 枠の移動は途中位置を描画し、画面外ならスクロールする。入力・ドラッグ中は保留し、キーボードフォーカスを奪わない。表名は見出しが隠れ、表本体が見えている間だけ追従表示する。
- 「案を反映」は下書きだけを変更する。反映後は「下書き」と表示し、明示保存・一体の取り消し・再取得の既存経路を使う。
- 手描きや同値の枠操作後は旧案を失効表示にし、プレビュー・反映を禁止する。解析中の手描きと遅延応答でも失効を維持する。再解析は現在の下書きを対象にする。
- 構造編集が可能な単一原画像の範囲だけに「枠」を解析内容として表示する。XLSXや選択表へ物理編集能力を追加していない。

frontendの表示・一時状態・既存反映経路への接続に限定した。draft、analysisMergeIds、manualTouchedFacetsの形式、provider schema、backend、DB schema、OCR受付・実行・補正・出力対応は変更していない。既存OCR contractを維持した。

## 画面確認で発見し修正した問題

1. 取り消し後にも反映成功の通知が出ていた。取り消しを区別し、未保存であることを含む通知へ修正した。
2. 解析前からある手修正を、反映APIにも解析後の競合として送り、再解析後に409となった。解析時点のserialを記録し、反映にはそれ以降のtouchだけを送る。解析作成と明示保存には全touchを保持する。
3. 新設案から別の新設案へ移ると、前の座標がregistryから消え、瞬時に切り替わった。同じ受付世代内の直前描画位置を始点に使い、世代変更時は破棄する。
4. 手描きセルには表示用初期値がまだなく、入力側だけを正規化する比較が入力変更を誤検出した。双方を同じ表示形式で比較し、実際の手動下書きは書き換えない。修正前にテストで失敗を再現し、修正後に反映・取り消し成功と後続手描きの拒否を確認した。

## 変更ファイル

| ファイル | 役割 |
| --- | --- |
| `frontend/src/editing/analysisPreview.ts` | 受付bindingと検証済み案から表示専用の座標を生成 |
| `focusState.ts`、`AiFocusLayer.tsx`、`editing.css` | 新設・除去・変更前後、途中描画、入力中スクロール保留 |
| `templateAnalysisStore.ts` | 旧案失効、個別枠・未解決欄のプレビュー、手修正の解析時点の区別、下書き・取り消し |
| `TemplateAnalysisPanel.tsx`、同CSS | 差分・凡例・失効・位置表示と明示反映／保存 |
| `frontend/src/components/LayoutEditor.tsx`、`frontend/src/pages/TemplatePage.tsx` | 手動操作との接続、手描き下書きの比較、取り消し通知、旧音声フォーカスの復活防止 |
| `templateAnalysisStore.test.ts`、`AiFocusLayer.test.tsx`、`TemplateAnalysisPanel.test.tsx`、`frontend/src/components/LayoutEditor.headingHover.test.tsx` | 非保存表示、失効・遅延返信、スクロール保留、途中描画、手描き反映境界 |

製品・テスト13ファイルと、この記録・AGENTS・Safety Contractの状態記録3ファイルを変更した。今回開始時からの差分は `final-scope.patch` に保存した。既存の未コミット差分を保持し、staging・commit・pushはしていない。

## 検証結果

証拠は `D:\llm-ocr-archive\template-ai\20261013-structure-ui`。

| 検証 | 結果と証拠 |
| --- | --- |
| frontend全件・build | `npx vitest run`：23ファイル・154件通過。`npm run build`成功。`frontend-full-final.log` / `frontend-build-final.log` |
| 手描き回帰再現 | 修正前は該当1件失敗・既存10件通過、修正後11件通過。`manual-draft-before.log` / `manual-draft-after.log` |
| 実Chrome受入 | 実React・専用FastAPI・専用SQLiteで10シナリオ通過。`verify_chrome.py` / `chrome-acceptance.json` |
| 原本投入から保存 | ブラウザー生成PNGを実ファイル入力へ渡し、実準備処理を実行。6枠→提案→7枠の下書き→取り消し→再解析→手修正→明示保存→ブラウザー再読込。保存前DB revision1・6枠、保存後revision2・7枠。手修正の項目名とmapping保持 |
| 複合矩形差分 | 変更前後10枠の同時表示、サイズ変更・分割・結合・重複除去・追加。保存後7枠、正規ID・結合元2セル・全7mapping座標をDBと再オープン画面で確認 |
| 移動・スクロール | 新設枠のx座標503→653の途中位置を実測。入力中はwindow562・内側0を保持。終了後window1183・内側2994へ移動し、画面外の対象枠を中央近くへ配置 |
| 解析中の手描き | 遅延応答でも手描き枠保持、旧案の選択・位置表示・反映を禁止。DB未保存を確認 |
| R04原画像 | 手描き下書きから再解析し、追加5・除去2を表示。明示保存後83枠、無関係77セルと手描き枠の座標保持。ブラウザー再読込で正規ID・83枠を確認 |
| 失敗・中止 | 不正digestを拒否、解析中止後の案を表示しない。保存済みR04 revision2・83枠は不変 |
| 表名追従 | 保存済み19行表で見出し可視なら非表示、見出しが隠れ表本体が可視なら表示、表本体も不可視なら非表示 |
| 最終状態 | 再読込後console error/warnなし、3対象のanalysisId・mergeIds・touch・heldを解消。途中の409は上記不具合の再現として記録 |
| 差分と保持 | `git diff --check`、通常DB全テーブル・schema・設定・863ファイル・781artifact、旧checkout、前工程の封印、最終4原本の14ファイルhashを照合 |

起動は自動承認レビューで`blocked by policy`となり、利用者が専用 `start-runtime.ps1` を実行した。その後8074／5274のプロセス所有と実HTTPを確認した。通常データへ接続せず、偽APIキー・制御HTTP応答・外向き接続拒否で検証した。専用サーバーは引き続き起動している。変更可能な稼働中DB・ログ自体は封印せず、読み取り専用接続から作成した `final-runtime-db.db` と固定の観測記録を封印する。

## 限界と次工程

有料provider HTTPだけを制御した画面・保存検証であり、実Lunaのschema受入・意味付け精度・矩形品質・応答5秒を証明していない。制御応答9件と専用台帳11行は実API利用回数ではない。R04の項目名は画面接続用の例であり、品質評価の正解として使わない。OSのファイル選択ダイアログと実マイクは未試験。手描きは実canvasの既存ハンドラーへDOM PointerEventsを送って確認した。最終評価用4原本は内容を開かずhashだけを照合した。

backend製品コードは変更していないため、修正3のbackend検証を重複実行していない。退役済みの旧検証スクリプト・旧経路も再導入していない。中間 `implementation-checkpoint.json` は当時の151件通過・Chrome未確認を記録した履歴であり、上書きしていない。最終状態は `phase-result.json` と `result-hashes.json` を参照する。

次は修正5＝Astra / XHighによる独立審査。修正6＝GPT-6.1 Sol / Highで実Luna比較、修正7＝Astra / XHighで未使用4帳票と手作業時間の最終評価が残る。
