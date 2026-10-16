# 音声・Decisions再設計 フェーズ5：画面接続・実画面確認

対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。フェーズ5の実装と限定検証を実施した。
利用者による専用HTTPサーバー起動後、Chromeから実HTTP・実保存を使って画面の受入を完了した。
**フェーズ5完了。次はフェーズ6（GPT-6.1-Sol / high）。モデル切替を待ち、このフェーズで停止する。**
通常editingは無効、通常DBは未移行、TTSは無効。実マイクと重い全体回帰はフェーズ7。

## 実装と変更境界

- `frontend/src/editing/v2client.ts`：所有token付きHTTPとLast-Event-ID付きSSE。本文を実行しない。
- `workbenchStore.ts`：resource別session、手動保存queue、receipt照会、表示更新待ち、質問、focus。
  保存応答喪失では同requestを照会する。手動draftは値だけでなく判定・候補・reviewItemsを保持する。
  音声／文字の自由文は保存journalへ残さず、照会用IDとreceiptだけを保持する。
  expired sessionのreceiptは元ownerで照会し、表示用sessionの再取得と分ける。
- `voiceCapture.ts`：v2 WebSocket、24kHz PCM、VAD、25秒segment、最大180秒の全文manifest。
  連続録音と選択欄の局所録音は同じ取得経路。短い間ではsegmentだけcommitし、全文は未保存のまま保持する。
  不完全な文末は3000ms、通常は1200ms。処理状態はVAD停止時点で先に表示する。
  最終認識待ちの次の発話は上限付きRAMに保持し、前の受付後に送る。切断時に自動再送・自動録音再開をしない。
- `EditingControls.tsx`：開始／停止、局所録音、任意の表、取り消し、保留質問、折り畳みの文字指示。
  表の未選択は既定の明示表→直近表→一意一致を利用する。表scopeをUI選択で暗黙拡張しない。
  手動編集／ページ変更／局所対象・テンプレート選択変更では音声を止め、古いplanをfenceする。
- `AiFocusLayer.tsx`／`aiFocusGeometry.ts`／`focusState.ts`：所有anchorを現在のDOM又は既存canvas投影から解決する。
  候補は点線、解決済みは青、保存済みは緑。全変更セルを同時に囲む。
  単一セル間は250msで補間。複数セルは同時追加・削除、共通セルを保持する。
  previewとcontextのrevision、interactionEpoch、resource、pageが一致しない枠を表示しない。
  可視セルは再中央配置しない。部分欠けは最小移動、画面外は中央寄せ、過大セルは先頭を優先する。
  多セルは収まる集合を一度だけ表示し、過大集合は可視セルを優先する。順番の巡回はしない。
  wheel／pointer／keyboard／IME中は自動移動を抑止し、800ms後に最新の対象だけ再評価する。
  表headerが上へ隠れ、表本体が1px以上見える間だけ表名を重ねる。横方向だけの隠れは条件にしない。
- `ReviewPage.tsx`／`TemplatePage.tsx`／`LayoutEditor.tsx`／`OcrReviewResults.tsx`：新経路への接続と表示用anchor登録。
  手動選択・キーボードfocus・既存warning・業務scopeはAI枠と分離する。手動の描画・修正・確定経路を保持する。

フロントだけでは、v2が持っていなかった「手動OCRの全判定保存」と「新規セルIDの置換応答」を補えないため、
以下の既存保存アダプターを追加した。OCR読取・provider・prompt・schema・mapping生成の変更ではない。

- `manual_draft.py`／`manual_contracts.py`／`manual_ocr_compiler.py`／`service.py`：v1の物理binding検査を共有化。
- `api/v2/editing.py`／`editing_contracts.py`：手動専用`manual-ocr`と`uiBindings`、receiptの`substitutions`。
- `infrastructure/command_repository.py`／`mutation_uow.py`／`sessions.py`：共通command経路で判定・revisionを保存し、既存compilerの置換結果を返す。
- `scripts/generate_editing_v2_contracts.py`と生成Schema／TypeScript：transportを型から生成。AIの22操作とその意味は維持。
  `uiBindings`は所有UIの表示・選択用であり、model tool catalogへ追加しない。

明示補正は既存`correct_in_transaction`・revision・receipt・undoを通す。
保存済みOCRの物理欄集合、sourceRegionId、boundingBoxを手動payloadで差し替えることは拒否する。
**既存OCR contractを維持した。** ただし以下の限定検証でOCR全領域を検証済みとは扱わない。

## 検証と限界

証拠は `D:/llm-ocr-archive/voice-assistant/20261009-redesign-decisions-phase5`。

| 対象 | 結果・証拠 |
| --- | --- |
| 変更前／変更後の共通コピーAPI | 54条件成功。`baseline-api.log`／`acceptance-results.json` |
| 追加した手動API | 14条件成功。全判定・候補・reviewItems、物理対応、再送、undo、新セルID置換。`manual-results.json` |
| 画面・音声取得・保存復旧 | 対象Vitest 26件（editing 18＋LayoutEditor 8）。DOM部品／制御VAD・WebSocketを用い、実ブラウザー・実マイクとは区別 |
| frontend build | `npm run build`成功 |
| 型と契約 | 8schemas／20例、共有22操作のschema同一、domain/application依存境界成功 |
| 対象静的検査 | Ruff成功、`mypy --follow-imports silent`対象9ファイル成功 |
| 依存先も追うMypy | 70エラー。対象外14ファイルが開始時の内容から不変であることを照合。全体成功とは扱わない |
| 実画面 | Chrome・8025/5195・専用DBで下記を確認。解釈だけ固定resolver。`ui-acceptance.json`、画面PNG、DOM記録、`ui-motion.json` |
| 実AI／実音声／速度 | 今回未実施。API使用0。前フェーズのLuna比較はフェーズ4記録を参照。今回のUI試験はAI精度やp95の証拠ではない |

検証コマンドは、backend仮想環境からDの`phase2_regression.py`、`verify_manual.py`、`validate_contracts.py`。
frontendから`npx vitest run src/editing/aiFocusGeometry.test.ts src/editing/AiFocusLayer.test.tsx src/editing/voiceCapture.test.ts src/editing/workbenchStore.test.ts src/components/LayoutEditor.headingHover.test.tsx`と`npm run build`。
追加したテストは値変換実装の複製ではなく、保存応答喪失、表示取得失敗、入力保持、対象世代、
スクロール抑止、全文segment順序、連続発話待機、局所録音の終了境界を確認する。
初回手動試験の`uncertain`で`normalized`を有値にする期待は既存契約と不整合だったため、
正しい`normalized=null`の入力へ修正した。拒否状態は既存の409に対応するCONFLICTを確認した。
製品側の判定正規化や拒否契約をテストに合わせて変更していない。

## 実画面の受入結果

- OCRの2セルへ00789／60000を保存し、全変更セルの緑枠が同時に表示された。手動選択は追加されない。
- 表末尾の欄へ501、先頭の欄へ502を保存。表内スクロールと画面外への自動移動を確認した。
  `ui-motion.json`では移動先の枠に72個の異なる座標を観測した。これは補間とscrollの確認でありAI遅延測定ではない。
- 取り消し、手動で505へ変更、再読込後の保持を確認した。
- 表選択中の表外指示は保存されず、選択解除後の再指示は保存された。
- 表見出しが可視なら重複表示なし。見出しが隠れて本体が見える間だけ表名を表示し、
  本体が上端の可視範囲外になれば消えることを確認。後者は1280×480で確認後、viewportを元に戻した。
- テンプレート記入欄の出力表示名を「受理番号」に変更し、保存済み緑枠を確認。
  その後「受理番号（手動確認）」へ手動編集し、一覧から再度開いて保存値を確認した。
  手動の枠追加・区分・意味付けの操作は保持。実マイクからの名称変更はこの検証に含めない。

実画面で見つかった以下の3件を修正した。

1. `AiFocusLayer.tsx`／`aiFocusGeometry.ts`：表内スクロール枠で見出しの可視性を判定していた。
   見出し自身の可視領域を使い、元見出しが見える間の表名重複を解消した。
2. `workbenchStore.ts`：表外指示を拒否した後も復旧待ちとなり、対象表と入力が無効になっていた。
   未保存が確定しjournalが残らない入力拒否・範囲違反では再操作可能にした。保存不明の停止は保持した。
3. `LayoutEditor.tsx`：保存応答の再表示で選択が解除され、対象世代の変更としてAI枠まで消えた。
   同じIDが残る保存後は選択を保持し、削除されたIDだけ解除する。既存v1の選択方針は変えない。
   回帰テストが修正前に失敗、修正後に成功することを確認した。

HMR中にはuseEffectの依存配列長変更に伴うReact警告が1件あった。ソース再読み込みに伴う開発時警告として記録し、
通常起動時の検証と区別する。更新後の新規ナビゲーションから保存までの警告・例外は0件だった。

## 専用試験の再現手順

起動は過去の自動承認レビューが`blocked by policy`で拒否した操作に該当するため、手動操作を依頼した。

```powershell
& 'D:\llm-ocr-archive\voice-assistant\20261009-redesign-decisions-phase5\start-ui-runtime.ps1'
```

専用コピーをbackend8025／frontend5195で起動する。既存port占有時は停止し、他のprocessを終了しない。
`ui_runtime.py`は実HTTP・実保存を使用し、解釈だけ決められた指示を返す制御resolver。
実AIと音声は無効。通常DB・従来試験コピーへ接続しない。

レビュー入口：`http://127.0.0.1:5195/?job=ac71c656-a25b-46ed-8672-ecabcffa48b3&edit=1`。
文字指示は「先頭2欄を00789と60000に変更」「最後の欄を501に変更」「先頭の欄を502に変更」。
テンプレートは版`14d4911a-c0c3-4ffd-a6a4-fc6ad0438a9d`の手動選択後に「選択した欄を受理番号に変更」。
保存値・全枠・移動・表名・手動操作を確認済みで、snapshot／ログ／スクリーンショットを同D folderへ保存した。
フェーズ6は責務・失効・queue・表示競合と計測に基づく性能調整。外部モデル速度は固定resolverの時間から評価しない。

## データ保持

開始時の通常31テーブル、従来試験36テーブル、業務855ファイルを退避。
最終保持照合は`preservation.json`／`final-preservation.json`と`phase5-only.diff`を正本とする。
通常DBのAPI台帳は156/196のまま、残40。通常DB移行・有効化・Git commit/pushは行わない。
実マイク、重い全体回帰、paged／XLSX全経路、通常環境の切替は未検証であり、OCR安全性を完全には検証できていない。
