# Phase34-B 製品外修正実装報告 — 2026-10-12

Phase34-Aに沿った製品外修正実装とAPI0検証を完了しました。**独立審査・製品採用・実API比較は未承認**です。次はPhase34-C（Astra / XHigh、API0）です。

対象checkoutは `C:\Users\hanam\Desktop\llm-ocr-deploy`、実装・資料・証拠は `D:\llm-ocr-archive\template-ai\20261012-phase34b` です。製品側はこの報告・結果JSON・現在地4文書の追記だけを変更しました。旧 `llm-ocr`、製品ソース、通常DB・通常データ、実装原版、過去証拠と台帳を保持します。既存OCR contractを維持しました。今回の処理は既存の物理対応・全claims・revision/epoch・同値手動編集・共通resolver/compiler/manualを通ります。

## 修正結果

| 指摘 | 実装・確認結果 |
| --- | --- |
| R1 単独記号欄の欠落 | `pair_candidates.py` を純粋な検出器として分離。欄内全体が相異なる2記号以上の凡例の場合に追加し、順序・字形・textと保留候補を維持。V08/V09/V10の6組を含む旧30件は、正解を変えず27/30→30/30 |
| R2 文字越境 | 固定Meiryo 17pxの全文を事前計測し、余白内のglyph bboxと重なりを確認。旧K02/K04/K06の修正版3枚と新30枚、計33枚で越境・clip・偶発重複0。旧画像・intent・goldは不変 |
| R3 割付・試行境界 | `FrozenCohort`、選定成果物、trial/runを結合。ID/split/family/順序/hashと結果全件を照合。入力生成前から永続attemptを確保し、予約前後・送信前・公開前に照合。重複・再起動・不確かな終了を再実行しない |
| R4 校正・保留例 | 校正v2は6family×正負2件、全12件Decisions。単独欄の親0〜2と読取方式を最大9構造＋2保留候補へ固定。親はpairの左右端を許容差内で一致させ、上方2欄高以内・非重複に限定。共有親・3親以上・所有衝突は全claimを保留 |

`factor-plan.v7.prototype2` に改訂しました。回答v8、既存gridの意味、候補上限32・有効質問64・送信8MiB、prompt・policy P0〜P4・画像圧縮は変更していません。全体の直積展開は行いません。単独pairの順序は従来のラベル順を保ち、既知5対象のX対照要求は旧保存要求と同一です。

## 資料と正解の分離

`sender-cohort.json` は既知6・校正12・評価18の正確な36入力を固定し、相対パス・入力SHA・寸法・描画/フォント来歴だけを送信側へ渡します。intent・期待DTO/投影・正負・カテゴリは `scoring-gold.json` と別資料に隔離しました。送信側preflightはgoldを読まず60要求を構築しました。

正例の期待設定は、候補から作らず先に記述したintentから共通manualで構築します。校正正例6/6・負例6/6、評価正例10/10・負例8/8と修正回帰3件が期待投影と一致しました。負例は範囲全体が操作0です。評価負例8件中6件は送信可能で全6件Decisions、2件はlocal heldです。これはoracleによる到達性の検証であり、モデルの正答率ではありません。

初版のV2-01等では2行をgridとして意味指定していましたが、既存grid契約は最小3行でした。候補ロジックを合わせず、初版全体を `datasets-revision1-development` に退避し、構造を作り直したr2を固定しました。初版の正解を新実装へ合わせて書き換えていません。旧30件は開発・回帰用で、未知精度の評価には使いません。

33枚の文字所属を機械検査し、6枚の一覧画像を実装担当が目視しました。**独立した画像・正解・元familyの審査は34-Cに残ります。** 合成生成器は共有されており、実帳票familyの資格は0、製品採用に必要な4familyは未達です。

## 実験の境界

`experiment_contract.py` は重複JSONキー、重複/欠落/未知ID、順序・split・familyの差、root escape、Windows junction/reparse、内容SHA不一致を拒否します。最初に全内容を検証して不変bytesを所有し、各trialは選んだ入力の所有証明だけを照合します。

`experiment_runner.py` は任意行を受け付けず、発行済みcohortから35の18試行を作ります。35の正確な結果全件からInputSelectionを発行して初めて36の24試行を作り、36の全結果・6family holdoutからPolicySelectionを発行して37の28試行を作ります。入力・設定・選定成果物・実装・evidence pin・answer binding・要求SHAを予約と応答へ結合します。

`experiment_session.py` / `Run` は専用input factoryと一ownerの実行を維持します。attemptは入力生成前に排他的作成とflush/fsyncで固定し、local heldでも消費します。shared ledgerの予約に失敗してもattemptを戻しません。別trial応答、予約後改変、公開前改変、並行/再起動による重複を実Runで拒否しました。mockは明示的なOfflineTransport/MockLedgerへ分け、mock cohortではlive経路を開きません。

実比較の許可は、固定の `docs/contracts/template-analysis-v8/phase34c-result.json` の `template-analysis.phase34c-acceptance.v1` を読み、phase/status、sender manifestの実bytes SHA、implementation lock SHA、scoring manifest SHA、独立review sealへの収録を照合する設計です。この承認は未配置です。`accepted=true` や任意の承認ファイル指定で開放しません。

## 検証と時間

既存境界335件、新pair23件、新実験境界41件、旧独立審査の5シナリオが通過しました。既存テストのmock構築には明示的OfflineTransportを追加し、旧任意行schedulerの件数検査を新しい発行済み割付の証拠へ置換しました。校正IDはv2へ更新しました。失敗を隠すassertion弱体化・skipはありません。

旧raw responseは原版と同じ406実装ファイルを持つ別コピーで再生しました。legacy30件・phase25の10件・phase30Dの42件は従来の受理/拒否を再現し、旧DTO24件も再検証しました。旧receiptを新policyへ持ち込んでいません。共通runtimeと保護対象10ファイルのbytesも原版と一致します。

追加したtrial処理を含む3新規processの中央値です。

| 対象 | ローカル中央値ms | 条件 |
| --- | ---: | --- |
| T1 | 311.4 | ≤500ms |
| T2 | 452.4 | ≤500ms |
| T3 | 227.6 | ≤500ms |
| H1 | 235.0 | ≤500ms |
| H2 | 242.7 | ≤500ms |

初回T2は533ms、所有bytesの重複検査を除いた段階でも519msでした。現在状態を各回deepcopyする走査も費用となっていたため、`StateSource.__call__` をJSON契約の新しい入れ子コピーへ変更しました。毎回その時点の状態を読み、画像の不変bytesだけを共有します。状態・epochを省略するcacheではありません。変更後に状態競合・取消・同値手動保護などの関連検証を再実行しました。設計の変更対象一覧に無かったこの1ファイルも、34-Cの審査対象として明記します。失敗した2回の測定は保存しました。

時間には入力生成・freeze・要求構築・oracle・compile/manualと、新trialの入力照合・永続attempt・予約/公開照合を含みます。Python import、goldの読込み、実ledger・HTTP/model・証拠保存、Run全体の時間は含みません。cohort admissionとmock選定/Session準備の時間は各timing JSONの `admissionMs` に別記しました。**実応答5秒の条件は未検証**です。

実行コマンドは共通の専用Pythonから `verify_phase34b.py`（12 job）、`historical_replays.py`（6 job）、`run_timing.py`（3新規process）、`acceptance_reader_checks.py`（6条件）です。承認readerは正しいhash結合、status/cohort/実装/scoringの不一致とseal欠落を試験用registryだけで確認し、本物の承認registryは作成していません。networkは明示的に拒否しました。ログ・要求hash・各結果は証拠rootに保存しました。製品ソース変更がないため、製品のunit全件/E2E・実マイク試験は実行していません。

今回のAPI使用・予約は0、共有台帳は417/496、残79です。未来の35/36/37の上限18/24/28、計70回と未割当9回を維持し、未予約です。次の34-C=Astra/XHigh・API0で実装/正解/画像/元資料分離と時間を独立審査し、合格までは実比較・製品採用へ進みません。

結果JSON: [phase34b-result.json](contracts/template-analysis-v8/phase34b-result.json)
