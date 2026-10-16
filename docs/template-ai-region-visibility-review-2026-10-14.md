# 修正6-C：表示・計時修正の独立再審査

2026-10-14 JST。対象checkoutは `C:\Users\hanam\Desktop\llm-ocr-deploy`。
実行モデルはAstra / XHigh、turn `01a138cf-3f35-7643-a06f-4542de3b3723`。
GPT-6.1 Sol / Highの実装後、同じチャットの別モデル工程で再審査した。新しい別エージェントは起動していない。

**判定：R4の修正は合格。6-Dの実API・実画面測定へ進める。実Lunaの品質・5秒条件と改善ゴールは未達成の扱いを維持する。**

対象は[表示・計時修正](template-ai-region-visibility-fix-2026-10-14.md)の製品・契約・テスト9ファイル。
起点の173ファイル封印と現checkoutの12ファイルが一致することを確認した。
前回の[独立再審査](template-ai-region-rereview-2026-10-14.md)で解消を確認したR1〜R3も、変更範囲と既存封印を照合した。
製品コードは変更していない。今回のcheckout変更は本報告とAGENTS/Safetyの現在地追記のみ。

## R4の完了条件との照合

| 条件 | 確認結果 |
| --- | --- |
| 画面外・切り取り・遮蔽を操作可能と数えない | 実DOMの矩形、viewport、祖先のoverflow、CSS、中心hit-testを確認。storeも可視性と要約のguardを再検査する |
| 後着の優先領域を埋めない | serverが実planからdisplayOrderを付与し、frontendが既存ページ順と計画順位で表示する。今回の実Chromeでも最後に公開された優先領域が先頭になった |
| 表示後の再判定と選択時間の分離 | 描画後2フレーム、scroll/resize/toggle等で再判定。画面外だった末尾2領域は表示後だけ記録され、選択後に同じactionableを追加しない |
| 通常順・後着、入力中の表示保護 | 6-B封印の通常先着・1920×600、後着・1920×898を照合。後着の保存済みraw markを再計算。手入力中の値・focus・矩形位置・scroll保持と、表示するまで未記録だった証拠も再計算した |
| 対応関係・共通保存・モデルを維持 | 新しい実ASGIとChromeで54セル/22入力/22保存先を保存・再取得まで確認。全24 reference要求の本文hash・画像・範囲・モデル/推論強度が固定計画と一致 |

`displayOrder` はモデル回答から受け取らず、`VisualRunner.validate` の検証済みplanに束縛される。
公開DTOへ到達し、反映要求、所有範囲、operations/readSet/writeSet、共通保存先の決定には使用しない。
旧案のnullは許容され、同順位・nullの順は安定sortで維持する。負の順位はDTOで拒否する。
HTTPを含む8生成schemaと生成TypeScriptをメモリー内で再生成し、現ファイルと一致した。生成器による製品書込みは行っていない。

`analysisControlVisibility` は可視性を判定し、選択controlの有効性、反映待ちの理由、busy/recovery/storageBlocked/失効/競合はpanelとstoreが判定する。
旧callbackはeffect解除時に取り消され、actionableは現analysisIdと照合する。
実ユーザー入力後は自動表示を抑制する。今回も詳細を開く操作後のscrollY=400を維持し、末尾領域を表示できた。

常時表示の要約2件は内容確認の入口である。全詳細は展開可能で、保存する案や品質分母を2件へ切り詰めていない。
画面のactionableは意味精度の証明ではない。6-Dでは**同じ事前固定領域全体の正しさ**と突合して5秒を判定する。

## 今回の新規検証

新規証拠先は `D:\llm-ocr-archive\template-ai\20261014-region-visibility-review`。
専用8088/5288を許可済み起動操作で起動し、PID・health・ロードした373 backendソースのhashを照合した。
通常DBや過去の試験DBは使用せず、provider HTTPだけを制御し、外向きsocket接続を拒否した。

- `integration.py`：新しい専用DBで14条件通過。途中反映、digest差替え拒否、同値手修正保護、geometry変更拒否、receipt/取り消し/再反映、54セル/22入力、明示保存・別接続再取得を含む。専用予約4回。
- `npm run test -- src/editing/TemplateAnalysisPanel.test.tsx src/editing/templateAnalysisStore.test.ts`：31件通過。
- `contract-check.json`：8生成schema、生成TypeScript、旧null/0/負順位の境界を確認。
- `verify_allocation.py`：5ページの24 reference要求を再生成し、固定hashと一致。予約・外部送信0回。
- Chrome：実解析クリック→3領域公開→制御していた先頭領域の後着→詳細展開→末尾へのscroll→全4案選択→案反映→明示保存→実ブラウザーreload→管理画面から再オープン。専用予約4回。
- `review_proofs.py`：上記のraw mark、操作順、専用DB、別HTTP取得、再オープン画面を照合。revision1→2、receipt1、54セルのID/矩形、期待22入力、相異なる22mapping/targetPath、mapping矩形と元セルの対応、全54セルの選択肢が一致した。
- browser内で実可視性関数を呼び、viewport外、祖先の切り取り、非表示、透明化、遮蔽、非接続、復帰など9条件を確認した。試験DOMはその場で除去した。
- Chromeの保存・再読込前後にconsole errorなし。`git diff --check`通過。

実装工程のfrontend161件/buildとbackend対象静的検査は、封印したsource・ログとの一致を根拠に継承する。
今回は製品変更がなく、全件テストとbuildを繰り返していない。全backend/E2E、新しい記入済みOCR、実マイクは未実施。

## 計測の区分と限界

6-Bの後着試験は、固定優先領域 `(0,0,963,911)` のクリック→actionable **4807.7ms**をraw観測から再確認した。
mark時の選択control、実変更要約、反映controlはviewport内で、切り取りを受けずhit-testが通る。
この数値は制御応答であり、実Lunaの速度実績ではない。

今回の後着試験は優先応答を手動解放まで保留したため、**7857.7ms**だった。
表示順位と計時条件の試験であり、5秒達成や速度比較に使用しない。早く到着した別領域の時刻を固定優先領域の代わりに採用していない。

Chromeの`select_page`と同じcontextの`new_page`を使用しても、対象documentのvisibilityStateはvisibleのままだった。
したがって実際の非表示タブ→復帰は今回未検証である。非表示documentの拒否は、実可視性関数への同期的なproperty模擬で確認し、直後に復元した。
この模擬結果を実タブ切替の成功とは扱わない。固定測定はvisible documentで行い、非表示となった試行は正常な速度成功へ混入させない。
準備上のファイル名誤認と、この非表示再現の不成立は `preparation-notes.txt` に保存した。

## 次工程と保護

次は **6-D / Astra / High**。工程別の指定に従い、現在のXHighのまま実API測定へ進まない。
`region-v2-20261014` のG1=4、G2=24、G3=60、合計88送信・上限96・未配分8を維持する。
この承認はコードと実験配分の審査であり、6-Dの実trial binding作成・送信前検証を済ませたという意味ではない。
各trialのresource/session、snapshot/plan/region ID、設定・本文hashを新規固定し、初回4試行の品質・保護・対応・速度条件を評価する。
不合格なら後続送信を停止する。払い戻し、無断再試行、low fallback、旧未送信23試行の再利用は行わない。

共有台帳は **436/924・残488**。今回の実API・通常予約は**0回**、専用の制御予約は**8回**。
R04の145位置/143機能/手動2、開発gold、最終未使用4帳票を保持し、最終4帳票の内容は閲覧していない。
通常データ・設定・旧checkout・既存差分・過去封印の最終照合は `preservation.json`、今回の固定ソース・証拠は `completion-1/hashes.json` を正本とする。
provider/model/prompt/schema要求、物理ID/mapping/targetPath/crop/revision/出力、通常OCRと共通保存を変更せず、**既存OCR contractを維持した**。
実Lunaの意味精度、実画面5秒、全ページ品質の達成証拠はまだなく、改善ゴールはactiveのままである。
