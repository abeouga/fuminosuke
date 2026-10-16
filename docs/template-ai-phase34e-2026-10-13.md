# Phase34-E 修正実装・中間検証 — 2026-10-13

**34-Eは未完了です。送信・試行管理の修正を製品外コピーへ実装しましたが、資料の候補到達性とT2の処理時間が受入条件を満たしていません。修正設計へ戻します。** 34-F受入、実API比較35〜37、製品採用は開始しません。

対象checkoutは `C:/Users/hanam/Desktop/llm-ocr-deploy`、実装は `D:/llm-ocr-archive/template-ai/20261012-phase34e` です。12日に作業を開始し、13日に検証結果を記録しました。実験IDと保存先の日付は変更していません。[34-D設計](template-ai-phase34d-design-2026-10-12.md)と[結果JSON](contracts/template-analysis-v8/phase34e-result.json)を参照してください。

## 変更した範囲

| 製品外ファイル | 変更と責任 |
| --- | --- |
| `experiment_session.py` / `run_transport.py` | Sessionが入力・設定・Runを発行。任意callback、任意ledger、任意transportを受ける通常入口を廃止 |
| `replay_transport.py` / `transport.py` | 再生はwire照合付きの不変応答bytesだけを受理。属性転送と現行の旧2段階callback Runを廃止 |
| `live_transport.py` | 固定OpenAI宛先とモデル要求。HTTP入口でSession発行の一回許可・所有者・要求SHA・取消/期限を検査 |
| `attempt_store.py` / `experiment_ledger.py` | 固定experiment ID、追記専用claim/intent/reservation/dispatch/terminal、共有ApiBudget予約ID、70試行の20文字purpose |
| `evidence_sink.py` | 実行可能callbackを受けず、製品外に一回だけ証拠を書き込む |
| `experiment_contract.py` / `experiment_runner.py` / `scoring_truth.py` | 34-F専用受入、パス非依存の実装digest、元terminal照合、ID番号によらない正負ラベル |
| `run_guard.py` | 新規モジュールと実行依存をlockへ追加。実装の現在性検査は維持 |

製品のUI・音声・DB schema・OCR・通常保存コードは変更していません。`factor_plan.py` / `pair_candidates.py` / `run_execution.py`、共通runtime、指定された13個の保護rootファイルは原版と同じ内容です。候補prototype2、回答v8、GPT-6 Luna、Responses low、上限32/64/8MiB、P0〜P4、共通manual/compilerを維持しています。

## 設計へ戻す理由

1. **V3-09：3行の記号付きフォームを候補で表現できない。** 画像と正の意味仕様を先に作成し、共通manualで期待DTO・投影は成立しました。しかし `factor_plan.py:74` は値欄が空欄又は□を含む場合だけフォームを生成し、○・×・△が各行に印字されたこの資料ではrecords候補しか残りません。期待投影の到達は30例中29例です。保存した `v309-plan.json` にフォーム候補がないことを確認しています。期待値を保留へ変更せず、分母からも除外していません。
2. **T2：ローカル中央値515.46msで500msを超過。** 新しい耐久記録と許可検査を含めた最初の測定は583.22msでした。各記録で繰り返していた同じ祖先パスの検査を同期境界ごとへ整理し、子ファイルの再解析点と内容SHAの検査を残して短縮しました。なお基準未達です。

34-D §1/§5.2/§8は「候補生成の追加修正は対象外」「候補ロジックの追加変更が必要なら設計へ戻す」と定めています。この境界で停止します。候補生成、現在性検査、共通manualを勝手に広げたり省略したりしていません。

## 資料と正解の状態

暫定v3は既知6・校正12・評価18。校正は全12件Decisions適格、評価負例は送信可能6/Decisions4/局所保留2です。ただし経路適格やoracle再生は意味の正しさを証明しません。負例の意味審査と全30画像の独立目視は未完了です。

既知資料、Phase28/33、34-Bの旧版・現版、34-Cを根拠とする正例回帰、新資料の188件を出典台帳へ記録しました。既知の派生3組は同じ元familyの開発資料として明記しました。名称・平行移動・拡大・記号表記に依存しない類似検出で23件を審査対象に残しています。類似検出通過や異なるSHAを独立性の証明にしていません。`source-review.json` は `accepted=false` です。

K2-11/V2-14は、元画像と旧負例goldを保持し、印字ラベルを隣接text欄へ対応させる新版の正例回帰2件を別保存しました。両方とも候補→共通manualの投影一致、物理対応保持を確認しました。評価用の新規独立資料には数えません。

新規実帳票familyは0、製品採用に必要な4familyは未達です。実モデル応答を使った精度は測定していません。

## 実施した検証

Pythonはdeployのvenvを使用し、製品外ディレクトリで `python -B verify_final.py` を実行しました。全8ジョブはexit 0です。

| 検証 | 確認した結果 |
| --- | --- |
| `fault_acceptance.py` | 再生型、callback/proxy/subclass拒否、単一試行、誤要求、再起動等18項目 |
| `live_fault_acceptance.py` | 許可欠落/偽造/別Run/要求変更/期限/使用済み、取消、送信後編集等21項目。MockTransport送信2・試験DB予約3 |
| `attempt_fault_acceptance.py` | 2process/CWD間の同一trial送信は合計1、DB予約後・ID記録前の終了でも再予約0、各記録段階の再実行拒否等8項目 |
| `selection_fault_acceptance.py` | 明示goldラベル、不足/重複/自己申告/偽のlive結果の拒否、コピー先によらない実装digest等10項目 |
| `pair_acceptance.py` / `supplementary_acceptance.py` | 23項目と6項目 |
| `regression_datasets.py final 30` | 旧30goldの投影・物理対応30/30 |
| `preflight_v2.py` | 名前は旧版を継承。実際はv3 sender 36入力/60要求、32/64/8MiBの境界を検査 |

`historical_replays.py` は406ファイルが原版と同じ旧runtimeで、保存30+10+42応答と24 DTOを再生し全6ジョブexit 0でした。旧receiptを新runtimeへ転用していません。これは新Runの旧335項目全件移植に代わる証拠ではありません。

**旧335項目・契約41・旧独立5・reader6の新入口への全面的な対応付けと再検証は未完了です。** 廃止入口を前提とするコピー済みharnessは歴史資料であり、現行の実行手順ではありません。検証のassertionは削除・弱体化していません。資料受入が失敗したため、これらを済ませたことにせず後続条件として残します。製品全体unit/E2Eと実マイク試験は行っていません。

## 時間測定

独立した新規processを3回使用し、他の検証処理は並走させていません。旧ローカル区間に新claim/予約記録/許可/dispatch/terminalを含め、別途実ReplayRun全体を測りました。許可処理のローカル計測は試験用Sessionで実メソッドを呼び、実接続・共有台帳は使用していません。

| 対象 | ローカル中央値ms | ReplayRun全体中央値ms |
| --- | ---: | ---: |
| T1 | 339.36 | 963.41 |
| T2 | 515.46 | 1228.80 |
| T3 | 269.63 | 975.64 |
| H1 | 283.31 | 940.92 |
| H2 | 261.46 | 926.56 |

500msは左列の既存基準です。右列に新しい500ms基準を作っていません。imports・gold読込・実HTTP/モデル・実台帳・既存証拠保存は左列の外で、admission費用も別記録です。失敗した最初の3processの値とログも保存しています。実応答5秒の成否は未検証です。

## 保存境界と次工程

API使用/共有台帳予約は今回0、累計417/496、残79です。実験用の固定live rootや34-F受入ファイルは作っていません。試験専用の受入・台帳・記録先は製品外の一時ディレクトリだけです。34-Cのrejected結果は変更していません。

リポジトリの変更は本報告・結果JSONと、AGENTS/Safety Contract/v7 README/v8 READMEへの現在地追記の6文書です。旧本文は保存済みです。製品ソース・旧checkout・両DB全表・通常データ863ファイル・781 artifact・共有台帳・原版を開始時snapshotと照合し、変更がないことを確認しました。共通runtime400ファイルと保護root13ファイルも同一でした。保存監査は `preservation.json` に記録し、`git diff --check` はexit 0です。これらの経路を変更していないため、既存OCR contractを維持したと判断します。新機能のOCR安全性全体を検証済みとは扱いません。

次は **Astra / xhigh・API0でV3-09とT2の修正設計**です。その後34-Eの不足実装・資料審査・保護回帰を完了し、34-F独立審査へ進みます。今回の途中成果を受入済みとして実API比較へ進めません。
