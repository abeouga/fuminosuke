# AIテンプレート作成 Phase30 B 修正実装

2026-10-12。**製品外の修正実装はローカル受入を通過しました。独立再審査は未実施です。** Phase29のR1〜R3を修正し、T2は同時期比較の中央値639.69→389.46ms、39.12%短縮しました。全5scopeが500ms条件を満たしました。Phase30-Dの実API比較は30-Cの受入後です。

対象は`C:/Users/hanam/Desktop/llm-ocr-deploy`、実装は`D:/llm-ocr-archive/template-ai/20261012-phase30b`、修正前のコピーは`D:/llm-ocr-archive/template-ai/20261012-phase30b-baseline`です。[修正設計](template-ai-phase30-readiness-design-2026-10-12.md)、[結果JSON](contracts/template-analysis-v8/phase30b-result.json)、[Phase29審査](template-ai-phase29-review-2026-10-12.md)を併用します。担当指定はGPT-6.1 Sol / High、現在のチャットモデルは未確認です。

## 修正内容

R1は原子的な開始権と一回だけのdispatch権に分離しました。同じrunへの再入は元runのguard・予約・終了処理に触れず拒否します。永続化で待機中の再入を含め、元runは1予約・1送信で成功します。

R2はHTTPだけのworkerを廃止し、一runの準備から終了までを同じ所有threadで実行する構成に変更しました。呼出し元はrun handleから中止を要求できます。初期化中の中止は待機者へ速やかに返し、所有者が終了するまで接続枠を維持します。初期化の解放後にも未送信要求が送られないことを確認しました。

R3は接続枠/初期化待機後、台帳予約後、最終dispatch前に、現在性と実装を新しい同期区間で照合します。予約中の対象変更は予約1・送信0で終了し、予約枠を払い戻しません。実際の要求bytes SHAもdispatch権と照合します。

新しい`run_execution.py`が開始・中止・一回予約/送信・終端を管理します。実行poolと同期入口の両方にプロセス全体で最大2所有者の制限を設けました。guardと画像証拠も所有thread以外からの利用を拒否します。共有HTTPが保持するのは接続資源だけです。

`StateSource`は編集対象に属する状態adapterです。書込みを同じlockへ集約し、同値変更でも世代とepochを進めます。重い内容照合の後、dispatchと成功公開の直前に世代ticketを同じlockで確認します。製品接続時も**同じ編集対象の全writerと後続runで同じadapterを共有する**必要があります。各runが独立した状態コピーを正本として使う構成には接続しません。共有adapterの後続指示により古いrunを拒否し、新しいrunだけが完全manual投影を返す境界も確認しました。

dispatch権の消費が送信開始の線形化点です。その後の中止でネットワーク上の要求を取り消せたとは保証せず、遅延応答を拒否します。実際のbody送出完了は別の観測値です。予約・dispatch・body送出・成功公開を同一計数にはしません。新しい実行artifactの契約は`single-owner-dispatch.v2`です。

## T2の重複計算削減

`run_image_evidence.py`にrun leaseと同期区間へ束縛した画像証拠を追加しました。同じ区間の同じ不変bytesは強参照とobject同一性で再利用し、packのfield・metadata・所有も照合します。区間を閉じるとhashとdecodeの一時情報を破棄し、次の区間で実内容を再検査します。現在状態の確認位置は減らしていません。

元packのcropと転送画像を同じ内部画像factoryから作り、元PNGのdecodeを区間内で共有します。可変Pillow画像は外へ公開せず、区間終了時にcloseします。元crop bytes、元PNG arm、寸法/座標/detail/ref、JPEG設定、小さい場合だけJPEGを採用する規則、codec失敗時の元PNGを維持しました。

全実装内容の照合、schema自身と応答の検査、全claims、共通compiler/manual、gold、候補、意味prompt、閾値は維持しました。前runや待機区間をまたぐ無検査キャッシュを導入していません。

## 検証結果

修正前のコピーでPhase29の3欠落を再現してから修正しました。最終ソースで既存83＋48＋70＝201境界と追加51境界を通過しました。追加境界は同時再入、初期化/pool/予約中の中止・期限・対象変更、dispatchとの順序、遅延応答、保存中の変更、異なる対象の同時実行、第三runの待機、proof偽造/交換/別run、区間更新とthread所有です。

旧30保存応答は26受理/4拒否、Phase25の10応答は9受理/1同一拒否で、旧採点を維持しました。固定された合成24scopeの期待DTO/投影を読み取りで再生し、期待値を再生成していません。既知5scopeはoracle選択による完全manual投影が一致しました。これは実モデルの意味精度の確認ではありません。

| scope | 修正前中央値ms | 修正後中央値ms | 500ms条件 |
| --- | ---: | ---: | --- |
| T1 | 290.23 | 269.08 | 合格 |
| T2 | 639.69 | 389.46 | 合格 |
| T3 | 222.68 | 195.09 | 合格 |
| H1 | 237.21 | 200.04 | 合格 |
| H2 | 213.37 | 191.09 | 合格 |

変更前/後の順序を交互にし、新規processで各3反復しました。起動/importは従来どおり別扱い、準備・新pack/view/schema・wire・oracle計算・検査/compileを含みます。通信・台帳commit・証拠書込を含む実応答時間とは区別します。元の500ms条件と分母は変更していません。

別途、共有HTTPX MockTransportとmock台帳、実際のローカル証拠ファイル書込みで一回ずつ統合時計を測りました。追加する待機境界・client初期化・予約stub・応答検査・保存後検査・oracle準備も時計内です。

| scope | mock scopeReadyMs |
| --- | ---: |
| T1 | 843.75 |
| T2 | 1054.76 |
| T3 | 747.67 |
| H1 | 790.34 |
| H2 | 740.71 |

mock時計はprovider・実DB commit・実ネットワーク・UIを測っていません。各一標本であり、実通信の5秒条件や接続再利用を証明しません。実際の接続再利用は未観測として保持しました。

実行はdeployのPythonに`-B`を付け、`verify_all.py`で短い各境界・保存応答・固定期待値を順に検証し、最後に時間比較を行いました。`mock_integration.py`で共有対象と統合時計を確認しました。全子processでsocketを禁止しました。ログ、比較6組、原修正前の再現、実装差分は製品外の本ディレクトリに保存しています。全件unit/E2Eと実マイクは実行していません。

途中の検証準備で、mock用oracleをschema上限検査より先に用意する呼出し順と、旧replay用planファイルのコピー漏れを修正しました。受入assertion・原応答・期待値は弱めていません。旧replayの停止ログを保持し、修正後の検証を最終結果としています。

## 変更範囲と保全

元の399実装ファイルのうち変更は`evidence_context.py`、`run_guard.py`、`image_transport_pack.py`、`run_transport.py`、コピーの`runtime/evidence_pack.py`の5ファイル、新規は`run_execution.py`と`run_image_evidence.py`です。残394ファイルを同内容で保持し、401ファイルと依存版を実装lockへ固定しました。v8検証の呼出し元は新factory/handle契約へ移行し、70条件を保持しました。旧v7の対照arm A原本は変更していません。

製品側で更新したのは本報告、結果JSON、AGENTS/Safety Contract/v7・v8 READMEの現在地だけです。製品ソース、両DB各37表、schema/設定、通常データ、旧checkout、Phase28/29/30-Aの旧証拠を保持します。確認結果は`preservation.json`と`diff-check.log`へ保存します。製品のOCR/mapping/crop/補正/出力を変更せず、既存OCR contractを維持しました。

今回API0、累計376/496・残120。製品接続と通常データ移行は未実施です。次は**30-C：Astra / XHigh・API0の独立再審査**です。再審査と次工程の指示が成立するまで、30-Dの実API比較を開始しません。
