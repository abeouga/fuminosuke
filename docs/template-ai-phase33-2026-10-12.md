# Template AI Phase33：候補説明・Decisions採用判定の製品外実装

2026-10-12。Phase32設計に基づく製品外実装とローカル検証を実施しました。**実装の境界検証と時間条件は通過しましたが、新規データの受入は未達です。Phase35以降の実API比較には進めません。** 有効な単独記号欄3例を既存の有限候補生成器で表現できません。期待値を変更せず、不足を記録しました。製品採用・製品接続はありません。

対象checkoutは `C:\Users\hanam\Desktop\llm-ocr-deploy`、製品外実装は `D:\llm-ocr-archive\template-ai\20261012-phase33` です。旧 `llm-ocr` は読み取りだけです。工程割当はGPT-6.1 Sol / High、実APIモデルは引き続きGPT-6 Luna、Responses lowです。今回の実API予約・送信は0、累計417/496、残79です。現在のチャットのモデル表示自体は検証していません。

## 1. 実装した境界

| 製品外ファイル | 責任と変更 |
| --- | --- |
| `candidate_effects.py` | 凍結候補から、選択後の欄・行・見出し・読取り方式・name所属を決定的に投影。gold・API・DB・compiler実行に依存しない |
| `experiment_config.py` | X/Y/Z、context有無、P0〜P4をimmutableな設定とし、正規化した設定内容を束縛 |
| `decision_acceptance.py` | choice・predicate・refusalを厳格に検証してから固定policyを適用。誤った数値・質問集合は拒否、有効refusal・同率第一候補・根拠不足は保留 |
| `answer_adapter.py` | 入力表示・prompt・policyをreceipt所有検査へ追加。回答v8と既存compiler/manualへ展開 |
| `run_transport.py` | Xは旧要求、Yは指示だけ、Zはさらに候補説明を変更。context質問を任意に追加し、質問64件超過は送信前に拒否。最大一送信の既存制御を維持 |
| `run_guard.py` | 新規モジュールとpromptを実装hash・読込み所有検査へ追加。設定差替え時の失効を維持 |
| `experiment_runner.py` | 18/24/28試行の固定割付、入力選定、校正policyの固定順位・family除外検査。認証情報・実送信を持たない |

`semantic_view.py`、`factor_plan.py`、`selected_adapter.py`、`branch_schema.py` は30-Dと同じbytesです。候補集合、alias、回答schema、全claims、物理セル・既存ID/path、crop、scope、共通manual、epoch・同値手動編集の保護を変更していません。製品ソース・DB schema・通常の意味付け保存APIは変更していません。今回の変更を通常UIや音声編集に接続していません。

Decisionsのchoiceのconfidenceと候補確率、predicateのprobabilityを別に扱います。context質問は同じ証拠から作成し、選択回答を受け取ってから追加しません。ただし同じモデルによる誤りの相関はあり得ます。質問を追加すれば精度が上がるという保証はありません。[Decisions公式ガイド](https://developers.openai.com/api/docs/guides/decisions)に照合しました。実endpointの受理は未確認です。

## 2. 検証結果

Pythonはdeployの `backend\.venv\Scripts\python.exe -B` を使用し、検証processのsocket接続を禁止しました。実行script・ログ・結果はすべて上記Phase33フォルダーへ保存しています。

| 実行・成果物 | 結果 |
| --- | --- |
| `run_verification.py` → `verification.json` | 11ジョブ成功、実装406ファイルのhash維持 |
| 旧境界4script | 83＋48＋70＋50＝251項目通過 |
| `new_boundary_acceptance.py` | 78項目通過。全5scope×X/Y/Zの完全manual投影、Xの旧要求完全一致、schema/alias/画像不変、P0〜P4の採用境界、不正回答・設定変更・receipt混用の拒否 |
| `supplementary_acceptance.py` | 6項目通過。32component＝64質問、33component拒否、family除外での選定失敗、適格policyなし・誤非空案の拒否 |
| `replay_phase30d.py A/B/C` | 保存42試行＝41受理・1同一拒否、要求・操作・完全投影・物理セル・既存graphのno-op・送信/予約数を再現 |
| `legacy_replay.py` / `phase25_replay.py` | 旧30件＝26受理/4拒否、旧10件＝9受理/1拒否を再現 |
| `dataset_review.py` | 旧24範囲の凍結期待DTOを再生。新規goldの独立審査ではない |
| `preflight.py` | 60構成の要求をAPI0で生成。送信側はintent/期待DTO/期待投影を読み込まない。将来設定は暫定、実行許可なし |
| `coverage_audit.py` | V08/V09/V10の期待記号欄と有限候補の不足を保存。候補生成4モジュールの旧hash一致を確認 |

境界検証は合計335項目です。これをモデル精度335例や独立帳票335枚とは数えません。旧42試行の受理は正答42件という意味でもありません。旧採否の不合格を維持します。

検証コピーの旧資料2点の補完と、旧A経路の検証factory呼出しの修正が必要でした。最初の失敗ログを保存し、成功済みジョブは再実行せず残りを再開しました。assertionの削除・弱体化、gold変更、テストskipはしていません。

## 3. ローカル時間

`run_timing.py` が3つの新規processで `measure_worker.py` を実行しました。最も説明量の多いZ＋context＋P3構成です。Phase30-Bと同じローカル区間を使い、入力具現化・候補固定・view/schema/画像pack・wire生成・期待回答による検証/compile/manualを含みます。測定用oracleの処理費も含みます。Python import、gold読込み、実台帳、HTTP/モデル、証拠永続化、完全なRun orchestrationは含みません。

| scope | 3回の中央値ms | 条件500ms以下 |
| --- | ---: | --- |
| T1 | 264.46 | 通過 |
| T2 | 403.17 | 通過 |
| T3 | 197.58 | 通過 |
| H1 | 205.53 | 通過 |
| H2 | 190.61 | 通過 |

`local-performance.json` に全標本と区間別時間を保存しました。実通信の5秒条件や利用者の体感速度を証明する測定ではありません。候補説明により要求は増え、T2のXは1,227,195bytes、Zは1,237,249bytesです。入力圧縮による高速化とは主張しません。

## 4. データ受入未達と停止条件

新規校正12範囲（正例6/負例6）と評価18範囲（正例10/負例8）を作成しました。期待DTOと完全manual投影は、人が記述した意味仕様からAPI0で生成し、全30範囲で送信前のSHAを固定しました。モデル回答からgoldを作っていません。画像と意味の整合性の独立審査はPhase34で未実施です。

校正は6元設計×2変種であり、12独立familyではありません。評価の18元設計とfamilyを分離しましたが、全30範囲は同じ生成器による合成資料です。familyラベル分離だけで未知の実帳票への一般化を証明しません。

期待回答での完全投影は **27/30** です。校正12/12、評価正例7/10、評価負例8/8を再現しました。負例8のうち7は送信可能、そのうち6はDecisions適格、1はlocal保留です。負例の比率条件を満たしても、正例の候補被覆条件を満たしていません。

V08/V09/V10は、左右のラベルと印字済み記号凡例から単独記号欄を意味付けする正例です。既存 `factor_plan.generate()` の単独pair生成（128〜142行）は右欄が空欄またはチェック欄の場合だけpairを作成し、単独の「○・×」等はunpairedになります。手動仕様では有効な欄を作成できますが、issued候補ではそのreading/bindingを選べません。`NO_ORACLE_CHOICE` と期待投影を保持しました。これは今回の説明投影による回帰ではなく、変更していない有限候補生成の欠落です。

校正集合にはチェック欄を含みますが、独立した単独記号pairの被覆も不足しています。Phase32が求める記号の校正を済んだものとは扱いません。モデルの指示文・confidence閾値・context質問では、この候補不足を修復できません。候補生成を変更する場合は、影響する全claims・schema/alias・manualへの対応を別の修正設計で定義する必要があります。

既存uploadsのXLSXは5ファイル/内容SHA4種類です。既知開発資料を除いても、新規実帳票4familyは確保・資格確認できていません。現在の新規実帳票資格数は0です。合成実験が将来通過しても、この状態で製品採用には進めません。

## 5. 変更範囲と次工程

repo内の更新は本報告、[結果JSON](contracts/template-analysis-v8/phase33-result.json)と、AGENTS.md・OCR Safety Contract・v7/v8 READMEへの現在地追記です。旧本文と既存未コミット差分を保持しました。製品外コード・検証・合成資料はPhase33フォルダー内だけです。終了時の `audit_state.py after` で両repoの製品ソース、両DB全表、417件のAPI台帳、設定/schema、通常863ファイル・781artifact、過去証拠の不変を確認しました。`git diff --check` は終了コード0です。既存OCR contractを維持しました。

製品を変更していないため製品build・全件unit/E2E・通常UI・実マイク試験は実行していません。新候補説明の意味精度、context質問の識別力、実endpoint、実通信5秒、未知実帳票の品質は未検証です。現在のpolicy採用は未選定です。

次は **Phase34：Astra / XHigh、API0** で、製品外実装・期待意味・データ分割・候補被覆不足を審査します。Phase33のデータ受入未達を解消せず、35の18回比較、36の24回校正、37の28回評価には進めません。計画上限70回・未割当9回は未予約のままです。モデル切替後の指示まで次工程を開始しません。
