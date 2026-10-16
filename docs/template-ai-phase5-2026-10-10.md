# AIテンプレート作成・フェーズ5：画面接続

2026-10-10。開発対象は利用者指定の `llm-ocr-deploy`。
[フェーズ3設計](template-ai-phase3-2026-10-09.md)と[フェーズ4バックエンド](template-ai-phase4-2026-10-10.md)へ画面を接続した。
実装と限定検証を完了した。通常環境への導入、実AI品質・速度の受入、全体回帰は未完了。

## 操作と保存境界

- 既存テンプレート編集画面へ「AIで意味付け」を追加。通常は分類・意味・表、設定から分類のみ、複数ページでは全ページを選択できる。
- 対象表の選択は任意。編集contextの既存選択を解析へ渡す。既存の分類を見直す操作は明示設定に限定する。
- 進捗を認証SSEで取得。検証済み案だけをGETし、分類の前後、意味名、表の行列数、保護・未解決を表示する。
- 選択した全セルを既存AIフォーカス枠で同時に強調する。手動セル選択とは分離し、既存の移動・スクロール・表名表示を再利用する。
- 解析開始から案の確認まで自動保存と音声編集を保留する。手動編集は継続できる。セルの手動選択だけで解析contextを失効させない。
- 「案を反映」は手元下書きだけを変更する。同値の手動再指定も保護し、競合単位を残して独立した単位だけ反映する。
- 「確認して保存」で既存manual-authoring、revision、receipt、MutationCoreへ渡す。AI反映記録と手動facetを同じ保存へ渡し、別の業務保存経路を作らない。
- 直前の未保存AI反映は取り消せる。その後に手入力した場合は、その入力を上書きせず取り消しを保留する。複数の過去反映を自由に戻す機能ではない。
- 再読み込み時には下書きと解析ID、要求ID、反映記録、手動保護を復旧する。既知の解析はGETで再取得し、応答不明の書込は元要求を再送する。新しい解析を自動で作らない。
- 未保存のAI案を画面遷移で自動保存しない。保存するか、AI反映を戻して解析を終了してから移動する。

## 変更ファイルと原因

| ファイル | 変更内容 |
| --- | --- |
| `frontend/src/editing/templateAnalysisClient.ts` | 既存認証transportを使う解析HTTPと認証SSE |
| `templateAnalysisStore.ts` | 解析・復旧・部分反映・取り消し・保存由来の状態管理 |
| `authoringFacets.ts` | 有限の意味facetと明示的な手動意図の記録 |
| `TemplateAnalysisPanel.tsx` / `.css` | 進捗、差分案、保護・競合、下書き反映と保存の画面 |
| `v2client.ts` / `workbenchStore.ts` | 解析有効状態、既存transportの共有、解析context、保存由来 |
| `reconcile.ts` | 保存時の新ID対応へfacet対象も追随 |
| `EditingControls.tsx` | 解析の確認中、手動選択による不要なcontext変更を抑制 |
| `frontend/src/components/LayoutEditor.tsx` | 手元下書きの取得・比較反映、自動保存保留、手動facet記録 |
| `frontend/src/pages/TemplatePage.tsx` | 解析パネル・編集器・既存保存・復旧を接続 |
| `backend/app/template_analysis/contracts.py` / `graph.py` | 公開プレビューへ分類前後・意味名・行列数を追加 |
| `docs/contracts/template-analysis-v1/http.schema.json` / `template-analysis.generated.ts` | 正本の生成器から更新した契約 |
| `templateAnalysisStore.test.ts` / `LayoutEditor.headingHover.test.tsx` | 応答欠落、手修正競合、復旧、取消、分類由来の対象検証 |

バックエンドの追加理由：従来の公開案は操作内部を隠す一方で、分類前後や表の行列数を返していなかった。
フロントエンドで内部操作を再解釈せず、バックエンドが検証した変更単位へ表示情報を付ける。
保存・物理対応・provider schemaは変更しない。依存単位の統合時に重複する分類プレビューも除去した。

実ブラウザで、既存の分類ボタンが `LayoutCell.source` を `manual` へ変更する不具合を再現した。
分類だけの手修正でも物理snapshotが変わり、三者反映が `SNAPSHOT_STALE` で拒否されていた。
分類・読取presetでは枠の由来を維持し、手動意図はfacet記録へ分離した。新しい手描き枠の由来は従来どおり。
既存保存データを遡及変換しない。

## 検証結果

成果物は `D:/llm-ocr-archive/template-ai/20261010-phase5`。通常環境のコピーだけを移行して使用した。
試験backend 8035、frontend 5205。実HTTP・実SQLite・実storage・実Chromeを使用し、外部AI providerだけ固定応答に置き換えた。
実APIの応答品質や所要時間を証明する試験ではない。

| 検証 | 結果・成果物 |
| --- | --- |
| `npm exec vitest run src/editing/templateAnalysisStore.test.ts src/editing/AiFocusLayer.test.tsx src/components/LayoutEditor.headingHover.test.tsx src/editing/workbenchStore.test.ts` | 4ファイル34テスト成功。`frontend-tests.log` |
| `npm run build` | TypeScriptとVite build成功。`frontend-build.log` |
| `.venv/Scripts/python.exe -m ruff check app/template_analysis/contracts.py app/template_analysis/graph.py` | 対象2ファイル成功。`backend-ruff.log` |
| 同Python `-m mypy --follow-imports=silent app/template_analysis/contracts.py app/template_analysis/graph.py` | 対象2ファイル成功。`backend-mypy-targeted.log` |
| import先も検査するmypy | 今回未変更の11ファイルに61エラー。`backend-mypy.log`。全体型検査成功とは扱わない |
| `verify_http.py` | 27条件成功。実受付、取得、部分反映、共通保存/undo、cancel、timeout、不正応答、stale、分類差分・行列表示等。`http-validation.json` / `http-trace.json` |
| 実Chrome・全案反映 | 10セルのAI枠を確認。反映だけではrevision 1・元分類を保持。明示保存後revision 2、意味結合5件・行2件、AI由来を保存。`browser-before-save.json` / `browser-after-save.json` |
| 実Chrome・同値の手修正 | c3を「読み取らない」と再指定。2単位だけ反映、1単位競合を表示。AI取り消し・再反映、再読み込み、明示保存を確認。c2/c3を除外のまま保存、結合4件・行2件、c3の手動由来を保存。物理セル不変。`browser-partial-save.json` |
| 実Chrome・復旧 | 上記の再読み込み前後で解析runは1、予約1のまま。下書き・適用済み表示・保護情報を維持し、追加解析なし |
| 実Chrome・中止/失敗 | 中止後の遅着結果を反映せず、不正な全体案を拒否。「解析を終了」から手動編集へ復帰。`browser-failure.txt` / `browser-terminal-states.json` |
| 実Chrome・診断 | 重複プレビュー修正後の対象時間帯に新しいerror/warnなし。`browser-recent-diagnostics.json` |
| 保全・差分 | 両通常DBの全テーブルとHEAD不変。旧llm-ocrソース不変、deploy通常863ファイルとartifact781件のSHA一致。`preservation.json` / `after.json` / `final-status.txt` |
| `git diff --check` | 成功。`diff-check.log` |
| 試験終了 | 専用backendは正常終了、frontendも停止、8035/5205のlistenerなし。`runtime-end.json` |

画面記録：[手修正を保持した部分反映](<D:/llm-ocr-archive/template-ai/20261010-phase5/browser-partial-merge.png>)。
初回のビルドで追加テストのTesting Libraryオプション不一致を検出して修正した。
初回ブラウザで検出した分類由来と重複表示の不具合も修正後に再検証した。失敗履歴を成功扱いに読み替えていない。

## 維持した契約と未検証範囲

OCR provider・prompt・model・OCR schema、crop・受付snapshot・既存mapping/path・出力bindingを変更していない。
AI案は元の物理セルと編集世代へ束縛し、業務保存は既存revision経路を使う。既存OCR contractを維持した。
ただし、この限定検証でOCR安全性を完全には検証できていない。

未実施：frontend/backend全件回帰、複数ページの実画面保存・出力、実帳票でのAI品質・応答速度受入、実マイク、ブラウザ記憶域枯渇や複数タブを含む全故障条件。
既存フォーカスの自動スクロール・アニメーションは再利用した。今回は複数セル同時表示を確認しており、全画面外条件の再受入ではない。
通常`.env`と通常DBは変更せず、通常editing/解析は無効・未移行を維持した。従来の音声試験サーバーを操作していない。
実API追加 **0回**。累計 **225/496、残271**。

次は **フェーズ6：Astra / Highによる独立監査・必要な重い回帰**。
この工程では開始しない。実AI品質・速度と必要時の実マイク受入はフェーズ7。
