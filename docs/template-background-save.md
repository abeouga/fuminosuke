# テンプレートの非ブロッキング自動保存

2026-09-12。対象は `frontend/src/components/LayoutEditor.tsx` と
`frontend/src/pages/TemplatePage.tsx` の一時UI状態と保存応答の反映。
既存の未コミット変更を保持し、backend・DB schema・API payload生成・OCR処理は変更しない。

## 原因と変更

自動保存が再検出と同じbusy状態を使い、fieldsetとcanvasの操作を無効化していた。
さらに保存応答を無条件でlayoutへ反映し、ブラウザー下書きを削除していたため、
無効化だけを外すと保存中の追加編集を失う。

- 自動保存中も区分・意味づけ・削除・結合・親接続等の編集を継続できる。
- 既存の1.2秒の入力待機とAPI経路を維持し、要求を並列送信しない。
  進行中の保存終了後、追加編集があれば次の自動保存で最新layoutを送信する。
- 応答時点のlayoutが送信時と同じ場合だけ正規化済み応答を表示へ反映する。
  異なる場合は編集中のlayoutを保持し、保存済み基準だけを更新する。
- 保存中は未保存扱いとし、元の値へ戻す操作もブラウザー下書きに保持する。
  保存・関連取得が失敗した場合は、サーバー側の書込み済み／未書込みを断定しない。
  未確認の下書きを残し、同じ失敗内容を無限に自動再送しない。
- 自動保存で意味づけのUndo履歴を破棄しない。
- 再検出・文字候補OCR・承認等の競合防止を維持する。
  工程切替は即時に行い、未保存のlayoutは移動先でも裏側で保存を継続する。
  画面終了は最新編集の保存完了を待つ。照合・書類名設定と最終確認の明示保存は排他的に行う。
  別タブとの競合解決やオフライン同期機構を新設したものではない。

既存の区分・結合・親接続・意味づけ処理と保存payload、ページrevision検証は維持する。
mapping identity・physical identity・crop・provider・公開版・過去jobに追加変更はなく、
既存OCR contractを維持した。AGENTS.mdの追加編集は不要。

## 初回修正時の検証（履歴）

- `frontend: npm.cmd test`: 29ファイル、296 passed。
- 追加の `LayoutEditor.autosave.test.tsx`: 遅い保存中の連続編集・直列化、古い親prop応答、
  元の値への復帰、保存失敗・再表示、保存中に削除した矩形の非復活、
  サーバー保存状態が不明な失敗時の下書き保持を5テストで検証。
- `frontend: npm.cmd run build`: 成功。既存の500kB超bundle警告あり。
- `git diff --check`: 通過。

保存応答を遅延／失敗させるテストはmockであり、実Chrome・実DBの連続操作E2Eではない。
今回backend全体・OCR契約テストは再実行していない。OpenAI / Google実API呼出し0回。
staging・commit・pushは行っていない。

## 追加修正：親画面のロックと接続候補の保持（履歴）

初回修正後も、`onBusyChange(busy || saving)` により自動保存が親画面の
`operationBusy` へ伝播して工程ボタン等を無効化していた。工程移動の明示保存は
`setBusy(true)` により編集fieldsetを無効にしていた。
さらに `layout.relations` の配列更新を契機とするeffectが、リンクモード中の
`selectedPath` を空へ戻していた。APIが同じ内容を返した場合も新しい配列となるため、
次の接続用に選んでいた保存先が保存応答で解除されることを変更前テストで再現した。

- 編集ロックの `onBusyChange` と保存中の `onSavingChange` を分離した。
  自動保存だけでは工程ボタンを無効化しない。工程移動を押した場合のみ移動を待機する。
- `saveIfNeeded` は進行中の保存に合流し、追加編集があれば最新状態を直列保存する。
  最後の編集が保存されるまで工程移動・画面終了は完了させない。待機中もステップ1〜3の
  編集は継続可能。重複した移動要求は同時実行しない。
- 次の保存はReactの状態反映後に開始する。XLSXではlayout保存・mapping保存後に
  親画面へ反映された最新printLayout revisionを用い、古いclosureで要求を送らない。
- 保存先の選択解除はリンクモードへ入った場合だけとする。保存応答による配列更新で
  選択を解除しない。保存応答をまたいだ矩形のドラッグも維持する。
- 保存失敗は移動待機を解除して下書きを残す。失敗した同一内容を自動再送し続けず、
  明示的な再試行は可能とする。

### 今回の変更ファイル

- `frontend/src/components/LayoutEditor.tsx`
- `frontend/src/pages/TemplatePage.tsx`
- `frontend/src/components/LayoutEditor.autosave.test.tsx`
- `frontend/src/pages/TemplatePage.test.tsx`
- `frontend/src/components/LayoutEditor.authoring.test.tsx`
- `frontend/src/components/LayoutEditor.pages.test.tsx`
- この文書

### 今回の検証結果

- 変更前に追加した2ケースは、接続候補の解除と進行中保存への移動要求で失敗を再現。
- `frontend: npm.cmd test`: 29ファイル、322 passed。
- `LayoutEditor.autosave.test.tsx`: 10 passed。明示保存中の追加編集、失敗時の移動中止・
  再試行、保存応答をまたぐドラッグを含む。
- 親画面テストはlegacy・XLSXページの両方で、遅いlayout保存・mapping再取得中の
  編集継続と、最新編集まで保存してから工程移動することを固定。
  XLSXの2回目の要求は最初のmapping保存後のrevisionを使用する。
- 既存authoring/pagesテストは、Reactのeffectを待つ保存へ変更したことに合わせ、
  `act`内で保存完了Promiseを待たず状態反映後に結果を検証する手順へ変更。
  保存件数・内容・失敗時保持・座標・再読込のassertionは維持。
- `frontend: npm.cmd run build`: 成功。既存の500kB超bundle警告あり。
- `git diff --check`: 通過。

実Chromeでの時間計測・実DB連続操作は未検証。フリーズ時間がゼロになったとする
実測結果ではない。今回の変更はフロントエンドの操作状態と保存待機に限定し、
保存payload・API経路・backend・OCR生成処理・page geometry gateは変更していない。
既存OCR contractを維持した。backend全体・OCR契約テストは今回再実行していない。
OpenAI / Google実API呼出し0回。既存差分を保持し、staging・commit・pushは未実施。

## ステップ切替時の保存待ちを廃止

前回の修正でも `TemplateReview.moveLayoutWorkflow` は `saveEditorBeforeLeaving` の
完了後にステップを変更していた。編集を続けると保存待ちが延長され、保存・関連取得の
間は工程ボタンが無効になる。今回、遅延した保存応答を返す前にステップ2が表示される
期待を追加し、legacyとXLSXページの両方で変更前の失敗を確認した。

- 同一エディター内のステップ1〜5は表示状態だけを即時変更する。エディターを
  再マウントせず、layout・ブラウザー下書き・保存キューを保持する。
- ステップ4・5へ移動した場合も未保存layoutの自動保存を継続する。1.2秒の待機、
  直列化、失敗した同一内容の無限再送防止は維持する。保存開始自体を工程移動の条件にしない。
- 保存応答の照合条件・書類名を表示へ反映する際は、送信時の設定と現在値を比較する。
  移動先で新たに編集された設定は古い応答で置換しない。設定の明示保存経路は維持する。
- ステップ切替を保存成功・検証完了とは扱わない。未保存・保存失敗の警告と承認拒否、
  一覧へ戻る際の保存待ち・失敗時の画面保持は維持する。
- 再検出等の排他的操作中、照合等の最終設定が不足する確認ステップへの移動は従来どおり制限する。

変更ファイルは `TemplatePage.tsx`、`LayoutEditor.tsx`、`TemplatePage.test.tsx`、
この文書と `ocr-safety-contract.md`。backend・API payload形式・mapping生成・geometry・
OCR処理は変更しない。既存OCR contractを維持した。

今回の検証は、遅い保存中のステップ1〜5切替、追加編集と最新revisionの直列保存、
移動先の照合条件・書類名の保持、保存失敗後の切替継続と下書き保持、一覧終了の失敗保護。
旧「工程移動は保存を待つ」テストは今回要求された即時切替の仕様へ更新し、
保存失敗時に離脱しない境界は「一覧へ戻る」で検証する。
実Chromeの描画時間・実DB連続操作は未計測であり、CPU起因の停止がゼロという測定結果ではない。

検証結果：`frontend: npm.cmd test` は29ファイル・323 passed、
`npm.cmd run build` 成功（既存の500kB超bundle警告あり）、`git diff --check` 通過。
backend全体・OCR契約テストは今回再実行していない。OpenAI / Google実API呼出し0回。
既存差分を保持し、staging・commit・pushは行っていない。
