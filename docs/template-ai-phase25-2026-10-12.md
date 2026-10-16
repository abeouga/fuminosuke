# AIテンプレート作成フェーズ25：実API比較

2026-10-12。**固定5範囲×2反復の10試行を完了。品質・応答時間の採用条件は不合格です。製品へ導入しません。** T1・T2は両反復とも全重点と完全manual意味投影が一致しましたが、品質を満たした4試行すべてが5秒を超えました。T3・H1・H2は品質を満たしませんでした。

対象checkoutは `C:/Users/hanam/Desktop/llm-ocr-deploy`、実行・原応答・監査証拠は `D:/llm-ocr-archive/template-ai/20261012-phase25` です。工程指定はGPT-6.1 Sol / Highであり、チャットの実モデル設定を検証した記録ではありません。実APIモデルは全10回 `gpt-6-luna`、Responsesはlowです。

## 固定条件と結果

審査済みPhase24修正revision2を変更せず使用しました。implementation-lock SHA256は `071bbabedb5502f8b7fa1d1b6527a2029fed655859ca612e45cc0dc2571f54e9`。開始時に前審査451成果物hashを照合し、実行manifestに393ソースと計測器・採点器・5要求テンプレート・条件を固定しました。

Phase24計画とPhase23審査で定義した、T1/T2/T3/H1/H2各2回・最大20送信・1試行最大2送信を維持しました。今回は固定経路が各1送信のため、予定10回・実送信10回です。T3のみDecisions、他4範囲は自由名称を含む候補があるためResponsesへ直接送信しました。warmup・retry・repair・追加診断APIは0回です。経路を失敗後に切り替えていません。

| 範囲 | API | 全重点正解 | 完全manual意味投影一致 | scope-ready実測（秒、反復1 / 2） | 品質付き5秒以内 |
| --- | --- | --- | --- | --- | --- |
| T1 | Responses | 2/2 | 2/2 | 6.341 / 5.639 | 0/2 |
| T2 | Responses | 2/2 | 2/2 | 6.187 / 6.092 | 0/2 |
| T3 | Decisions | 0/2 | 0/2 | 4.387 / 2.009、両方partial_draft | 0/2 |
| H1 | Responses | 0/2 | 0/2 | 5.938 / 5.150、両方held | 0/2 |
| H2 | Responses | 0/2 | 0/2 | 6.062 / 拒否のため完成時刻なし | 0/2 |

全重点正解4/10、完全manual意味投影一致4/10、品質付き5秒以内0/10です。品質成立4試行のscope-ready中央値は **6,139.32ms**。各範囲1/2以上・全体8/10以上の5秒条件、品質成立中央値5秒条件、全重点正解、重点誤結合0の条件をいずれも満たしません。

HTTPは10/10が200、wire形状・所有候補検査を通った結果は9/10です。その9件にはheld2件・partial_draft2件を含みます。構造検査の通過と意味の正解は別です。9有効結果の単純中央値5,938.42msは不正解・保留を含み、採用速度として扱いません。品質不成立を60,000msとする事前のペナルティ指標は中央値60,000msでした。

| 重点指標 | 正解 / 固定分母 |
| --- | --- |
| role分類 | 89/146 |
| 意味結合 | 54/74 |
| 順序付き行 | 6/14 |
| 記号選択肢 | 3/16 |

重点誤結合は4件、重複結合は0件です。有効9結果中の重点結合欠落は12件です。拒否1件も全10試行の正解分母に含め、欠落12件は有効結果だけの集計と区別しています。unknownを保存用ignored初期値として加点せず、実際のrelationと受理claimsを採点しました。表の行から存在しない結合を補っていません。

## 保存応答で確認できた失敗

- **T3:** 各反復7判断中5判断がconfidence 0.90条件を満たさず、既存の確率差条件と併せた固定検査によりneeds_contextとなりました。各反復で受理されたのは2判断です。速い反復2も未完成であり、2.009秒を品質成功と解釈しません。閾値を下げて再採点していません。
- **H1:** 両反復で同じ構造を選び、一方のgenerated nameを「申込者情報」、もう一方の必須nameをnullで返しました。依存component全体が `UNRESOLVED_FACTOR_OR_NAME` で保留されました。自由名称の不足が意味構造全体の未成立につながっています。
- **H2反復1:** 記号欄を通常inputとする表構造・列判断が選ばれました。C6〜C9の結合には列見出しC5だけが残り、必要な各行見出しB6〜B9が欠け、重点誤結合4件となりました。draftとして構造検査を通っても、意味の正解は保証されません。
- **H2反復2:** 選択構造のcolumn factor `f_1140c39b1d12dd705753` に、許可2候補のいずれでもない `a_0fa79c05070b2fee6512` を返しました。固定server検査は `UNISSUED_OR_WRONG_FACTOR_CHOICE` として拒否し、compile・保存へ進めていません。要求schema内の全体enum適合だけでは、選択構造とfactorとchoiceの条件付き整合性を保証できない例です。

以上は `response-diagnostics.json` と原応答に基づく観察です。prompt・候補表現・モデル能力のどれが支配要因かを分離した実験ではなく、根本原因の確定はしていません。

## 時間の解釈

scope-readyは新規HTTP client初期化の直前から、pack・plan・現状/所有/実装検査・共通ledger予約・wire送受信・JSON decode・選択検査・共通compile/manual検査を経てRun.executeが返るまでです。通信中の証拠永続化も含みます。Python import、Settings/ORM初期化、終了後の採点・追加no-op確認は含みません。各試行は新規process・context・HTTP clientで、接続を再利用していません。stream=Falseであり、first textやUI到達は測定していません。

T2の中央値はscope-ready **6,139.32ms**、HTTP送受信 **5,031.79ms**、差分のローカル処理・wire準備・台帳・証拠書込は **1,107.54ms** でした。HTTP区間はnetworkとprovider処理を分離していません。API部分が約5秒を占め、今回の計測ではローカル部分だけの短縮で目標達成を証明できません。

Phase24修正のT2ローカル中央値472.96msはoracleの別計測境界です。今回の1,107.54msにはHTTP初期化、実ledger予約、要求組立・証拠書込などが追加されているため、その差を修正実装の回帰や同条件の性能差とは扱いません。

## 過去比較と限界

| 記録 | role | 結合 | 行 | 記号 | 品質成立 / 10 | 品質付き5秒以内 / 10 |
| --- | --- | --- | --- | --- | --- | --- |
| Phase20 P-low | 67/146 | 15/74 | 0/14 | 8/16 | 3 | 2 |
| Phase25固定混合経路 | 89/146 | 54/74 | 6/14 | 3/16 | 4 | 0 |

結合・行の正解数は過去記録より増えましたが、記号は減りました。これは同時点・同条件で旧方式と新方式を交互に測定した対照実験ではありません。経路・実装・計時境界が異なるため、速度改善率や一般的優位性の証明には使いません。既知の固定5範囲・各2標本であり、未知帳票への精度、p95、連続利用、接続再利用の効果は未確認です。

## 検証と変更境界

追加API0の保存応答再実行で、有効9件の完全意味投影・物理対応・coverage・共通manual投影・既存全ID/pathの完全no-op、および拒否1件の同じ例外を再現しました。凍結済み旧採点関数を別に読み込み、有効9件の全採点・誤結合・欠落・重複・品質成立判定が一致しました。原応答bytes、保存JSON、request bytes/hash、usage、実行manifest、共有ledgerの照合も通過しました。

実行した主な検証は以下です。Pythonはdeployのbackend仮想環境を使い、すべて `-B` で実行しました。

| コマンド（証拠ディレクトリ内） | 結果 |
| --- | --- |
| `audit_state.py before` | 旧451成果物、366既存ledger行、両DB・設定・業務ファイルのbaselineを固定 |
| `worker.py --preflight` | oracle5/5、採点の誤parent/欠落/unknown条件、API0を確認 |
| `benchmark.py --freeze` / `benchmark.py --run` | 固定manifest、10/10試行完了、retry0、実送信10 |
| `analyze.py` | 原応答・request・usage・hash・台帳・固定判定を照合 |
| `replay_verify.py` | 保存応答9受理/1同一拒否、別採点9/9、追加API0 |
| `audit_state.py after` / `git diff --check` | 保持監査通過、差分検査exit0 |

事前preflightの名称表示補助で `name` と実データの `label` を取り違えたKeyErrorを修正し、最終preflightをAPI0で通過してからmanifestを固定しました。初回失敗logも保持しています。送信後の集計器ではhash用JSONのensure_ascii設定をwire実装と一致させました。これは証拠の再照合処理の訂正であり、原応答・計測値・採点器・gold・閾値・試行数・結果を変更していません。

製品ソース、UI、設定、schema、全業務table、旧checkout、863業務ファイル、781artifact、Phase11以降の旧証拠を保持しました。通常DBの変更はdeployの `api_call_ledger` に今回の10行（ordinal367〜376、purpose=`template.phase25`）を追加した分だけです。既存366行を保持し、製品の通常移行・有効化・業務保存をしていません。この境界により**既存OCR contractを維持しました**。文書変更は本報告・結果JSONの新規2件と、AGENTS/Safety Contract/v7 READMEの現在地追記3件に限定しています。

API使用は **10回**（Responses8、Decisions2）。共有台帳 **376/496、残120回**。追加診断や再試行に残枠を使っていません。製品側の変更がないためfrontend build/backend全件回帰は実行していません。製品保存・revision競合・undo・UI・実マイクはこの製品外比較では未受入です。

## 成果物と次工程

- [機械可読結果](contracts/template-analysis-v7/phase25-result.json)
- 証拠: `D:/llm-ocr-archive/template-ai/20261012-phase25/` の `execution-manifest.json`、`protocol.json`、`runs.json`、`sends.json`、`summary.json`、`wire-verification.json`、`saved-response-replay.json`、`response-diagnostics.json`、`preservation.json`、`result-hashes.json`。要求、原応答、headers、compiled、失敗traceも保存しています。秘密鍵は保存していません。
- API仕様参照: [Decisions create](https://developers.openai.com/api/reference/resources/decisions/methods/create)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)。今回のHTTP200と意味不正解を区別し、構造化出力を意味の正しさの保証として扱っていません。

次は **フェーズ26：Astra / XHigh、追加API0の独立審査** です。今回の失敗・計測境界・条件付き選択と名称依存を審査対象にします。フェーズ26、新しい設計、追加有料比較、製品接続は未開始です。
