# 音声・Decisions再設計 フェーズ6：統合レビュー・性能調整

対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。フェーズ6の限定修正・計測・実画面確認を完了した。
**次はフェーズ7（GPT-6-Astra / xhigh）。ここで停止し、モデル切替と実行指示を待つ。**
通常editing無効、通常DB未移行、TTS無効を維持した。今回の外部API使用は0、累計156/196、残40。

## 原因と変更境界

製品変更は次のフロントエンド2ファイルだけ。バックエンド、AIモデル、prompt、schema、DB、依存定義は変更しない。

- `frontend/src/editing/workbenchStore.ts`：認証済みSSEで届いた完了eventをjournalへ保持し、その場で待機処理を進める。
  以前は完了eventのpayloadを利用せず、既に始めた250ms間隔のGET照会を待っていた。
  正常接続時はまずeventを待ち、1秒以内に届かなければ同じrequest IDを照会する。
  接続前・切断後は照会から始める。照会失敗を新しい編集指示の再送へ置き換えない。
  owner・event sequence・cursorの検査は保持し、旧ownerの接続を新ownerの正常接続として扱わない。
  遅れて届くACCEPTED応答で完了eventを消さず、再読込後もreceiptから表示だけ復旧できる。
- 同ファイル：contextと保存済みpreviewの取得を並行化する。両方のrevisionとreceiptを照合してから表示する。
  別世代のpreview、別編集によるrevision競合では表示更新を停止し、保存記録と手動draftを保持する。
  後続の自分の保存を読む既存条件は、contextのlastReceiptと最新revisionが一致するときだけ許す。
- 同ファイル：保存済み表示の取得・反映を専用queueで順序付ける。
  指示の処理待ちと表示queueは分け、遅い古い取得結果が新しい表示を上書きする競合を防ぐ。
  先行指示の表示取得失敗はjournalへ記録し、後続指示の表示成功だけで復旧待ちを解除しない。
- `frontend/src/editing/v2client.ts`：SSEのHTTP応答が成立した時点を通知し、event待機と照会復旧を切り分ける。
  最後の画面購読が外れたら接続状態も解除する。本文を実行する処理は追加しない。
- `frontend/src/editing/workbenchStore.test.ts`：上記の待ち時間、表示順序、世代不一致、通知欠落、
  遅い受付応答、表示復旧待ちの保持、接続終了を受入条件として確認する。

補正payload、FieldReading、reviewItems、mappingId、targetPath、sourceRegionId、crop、
revisionの保存方法、receipt／undo、出力bindingは変更しない。読み取り結果の再取得方法と一時UI状態だけの変更で、
**既存OCR contractを維持した。** 通常環境の移行・有効化は実施していない。

## 計測

証拠は `D:/llm-ocr-archive/voice-assistant/20261009-redesign-decisions-phase6`。

同じ制御入力で各5回測定した。完了通知は20ms後、context取得とpreview取得はそれぞれ40msとし、
受付からonSavedによる表示反映までをperformance.nowで測る。接続初期化時間は含めない。

| 指標 | 変更前 | 変更後 |
| --- | --- | --- |
| 制御条件の中央値 | 334ms | 61ms |
| 最小〜最大 | 332〜339ms | 60〜67ms |
| 完了確認の追加GET／指示 | 2回 | 0回 |
| 遅い旧previewを含む表示順 | 3 → 2 | 2 → 3 |

変更前は`frontend-before.log`、変更後は最終`frontend-target.log`。中央値の短縮は約82%。
これは待機と取得順序を比較する画面側の制御測定であり、実AI・実音声の速度、p95、精度を示さない。
初期接続・SSE欠落時には回復用GETを使うため、全操作で追加GETが0になるという保証でもない。
サーバー側SSEの250ms replay間隔、音声の終端判定、外部providerの処理時間は今回変更していない。

独立した取得の並行化と不要な往復の削減は、[OpenAI公式の遅延最適化ガイド](https://developers.openai.com/api/docs/guides/latency-optimization)
とも整合する。今回の数値の根拠は上記ローカル測定である。

## 統合レビューの結果

- plannerは既に最大4poolをDecisions 1要求で選び、必要時のみResponses最大1要求へ渡す。
  余分なmodel loopの再導入は確認しなかった。既定のDecisions優先を維持する。
- PlanningCatalog cacheは32件に制限され、scopeContext・lastReceipt・recentTargetHandles・instructionDialogueのdigestで失効する。
  revision／scope変更後の古い候補を再利用するための例外や、別のcache層は追加しない。
  失効検査を省く性能改善は実施していない。今回は画面待機に再現可能な差があったため、その箇所を修正した。
- Luna直接との実API比較は[フェーズ4](voice-editing-redesign-phase4-2026-10-09.md)を参照する。
  本工程はproviderを変更しておらず、過去比較を新しい実測として報告しない。
- 音声／文字／手動は既存の共通command・revision・receipt経路を維持する。
  新しい保存APIや画面独自の書き込み経路を追加しない。

## 検証と限界

| 検証 | 結果 |
| --- | --- |
| 修正前の受入条件 | 7件中2件失敗。余分な照会と古い表示による上書きを再現 |
| 表示復旧待ち | 先行取得失敗＋後続成功で停止解除される欠落を再現、修正後成功。`pending-before.log` |
| 接続終了 | 終了後も正常接続扱いとなる待機を修正過程で検出、修正後成功。`disconnect-before.log` |
| 対象frontend検証 | 5ファイル33件成功。workbench 11、voiceCapture 4、focus 10、LayoutEditor 8 |
| frontend build | TypeScript／Vite build成功。`build.log` |
| 契約・依存境界 | 8schemas／20例、共有22操作schemaの同一性、domain／applicationの依存境界成功。`contract-validation.json` |
| 実画面 | 専用8025／5195・Chrome・実HTTP／実保存。変更、取り消し、再読込後の保存値、緑枠を確認 |
| 実画面の通信 | 最初の保存では完了確認GETが0。contextとpreviewは約0.23ms差で開始。再読込直後の保存では復旧用GETが1回。`ui-network.json` |
| 実画面の警告・例外 | 記録時0件。`ui-network.json`／`ui-final.png` |
| テンプレート実画面 | 記入欄10の出力名を「受理番号」へ変更して保存表示を確認。取り消しで元の「受理番号（手動確認）」へ戻る。`ui-template-saved.png`／`ui-acceptance.json` |
| データ保持 | `final-preservation.json`と`phase6-only.diff`を正本とする |

実行コマンド（frontendから）：

```text
npx vitest run src/editing/aiFocusGeometry.test.ts src/editing/AiFocusLayer.test.tsx src/editing/voiceCapture.test.ts src/editing/workbenchStore.test.ts src/components/LayoutEditor.headingHover.test.tsx
npm run build
```

変更前に失敗する挙動を固定し、成功させるためのassertion弱体化・skipは行わない。
並行取得で異なるrevisionが戻る場合は表示せず、元の手動fields／reviewItemsを保持する条件も確認した。
実画面ではフェーズ5で利用者が起動した専用環境を継続使用し、backendの起動・停止・再起動は行っていない。
固定resolverが約350msで解釈する環境であり、実AIの理解精度や長文・音声の受入を示すものではない。
API使用は0。通常31テーブル、従来試験36テーブル、855業務ファイル、開始時の未コミット差分を保持する。
このUI試験の専用コピーには保存・undo履歴が追加される。通常DBと従来試験DBとは別である。

## 次の工程

フェーズ7はGPT-6-Astra / xhigh。全22操作と依存境界、必要な重い回帰、保存response／crop／paged／XLSX、
実音声による誤認・長文・連続訂正・質問回答、速度／精度を検証する。実マイク試験時は利用者を呼ぶ。
共有上限196回・残40を守り、WS、音声、Decisions、Responsesの実送信をそれぞれ計上する。
通常移行と旧write撤去は、それらの結果と実入口を確認してから行う。
現時点でOCR安全性を完全には検証できていない。実音声の速度目標達成や通常切替完了とは扱わない。
