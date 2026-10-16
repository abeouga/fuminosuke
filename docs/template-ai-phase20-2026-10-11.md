# AIテンプレート作成 フェーズ20：focused v5の固定30試行

2026-10-11。固定した比較を30/30試行まで実行した。**F-low、P-low、P-noneの全方式で品質・5秒条件が不合格。製品接続・有効化へ進めない。** 局所packで入力tokenとprovider終了時間は減ったが、表の意味付けと親見出しの欠落を解消できなかった。推論noneは応答が短く速くても、利用可能な案をほとんど作れていない。

## 1. 作業範囲と固定条件

作業対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。旧 `C:\Users\hanam\Desktop\llm-ocr` は変更しない。実行役の指定はGPT-6.1 Sol / High、実APIモデルは全30件 **gpt-6-luna**。

測定・採点・監査スクリプトと応答は製品外の `D:\llm-ocr-archive\template-ai\20261011-phase20` に置いた。Phase19のruntimeを読み取りで使用し、共通normalizer/compiler/manualやv5生成契約を変更していない。

[Phase18設計](template-ai-phase18-design-2026-10-11.md)、[Phase19受入](template-ai-phase19-2026-10-11.md)、[固定protocol](contracts/template-analysis-v5/phase20-protocol.json)を維持した。protocol SHA-256は `6b5b1a96bf542e277abd46b14f301668b7dfaaef171593827dd24d2c66f8c961`。

| 方式 | 入力 | reasoning effort |
| --- | --- | --- |
| F-low | 全ページのcell情報＋全体画像high | low |
| P-low | 固定した局所cell/context pack＋crop high＋全体画像low | low |
| P-none | P-lowと同じpack・schema・prompt | none |

5既知scope×2反復×3方式。write pool、global refs、元座標、全体物理snapshot、候補、派生goldは共通。並列1、出力上限24576、timeout60秒、追加診断・warmup・retryは0。Phase19の411 source/evidence hash、環境、30件のwire request/scope/pack hashを送信前と終了時に照合した。

共有台帳は開始336/496、残160を確認した。すべて既存 `ApiBudget` の送信前commitで予約し、実wire bodyのhashもrequest hookで照合した。完了後は既存336行の全列が不変、新しいordinal337〜366の30行が今回のsend記録と順序まで一致し、全行RESPONSEだった。APIキーは証拠へ保存していない。

起動の初回は作業ディレクトリが旧checkoutだったため、設定のDATABASE_URLがDATA_DIR境界検証に拒否された。送信・予約前のローカル起動エラーでAPI0回。deploy/backendを作業ディレクトリに指定して起動し、コード・設定・固定requestを変更せず実行した。

## 2. 品質結果

各方式の必須条件は重点分類146/146、結合74/74、順序付き行14/14、記号16/16、重点誤結合0、試行ごとの全重点条件通過。bindingは実compileされたrelationだけから採点し、table行からbindingを補う処理は使用しない。

| 方式 | 分類 | 結合 | 行 | 記号 | 誤結合 | 欠落結合 | 全重点条件を満たす試行 | 判定 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| F-low | 46/146 | 11/74 | 0/14 | 4/16 | 8 | 55 | 3/10 | 不合格 |
| P-low | 67/146 | 15/74 | 0/14 | 8/16 | 12 | 47 | 3/10 | 不合格 |
| P-none | 30/146 | 4/74 | 0/14 | 0/16 | 4 | 66 | 0/10 | 不合格 |

欠落には、guardで全案を拒否した試行の重点結合も含めた。失敗試行を分母から外していない。schema形状の受理は30/30、共通guardまでの受理は26/30で、うち意味単位を持つartifactはF-low 5/10、P-low 7/10、P-none 2/10。形状の正しさ・guard受理・意味正解を区別する。

重点外に受理された追加binding・追加の意味roleは0件だった。受理された名前・alias・親・役割はPhase19で送信前に固定した手作成の期待layoutと照合した。8試行に差分があるが、いずれも重点品質がすでに不合格。重点品質を満たす6試行にはこの照合差分もなかった。原画像T1/H2も目視し、T1の別々の親見出し、H2の行ラベル・判定記号列・説明列の違いを確認した。名前が読みやすいという理由で誤bindingや欠けた表構造を正解にしない。

### 採点側の訂正

初期runnerは分類点をcompile後のcell roleから計算した。しかし保存用DTOでは未解決cellも中立の `ignored` になり、canonical proposalでは `unknown` のままである。この初期値を意図的なignored分類として加点する欠落が、H2の3試行で見つかった。

固定promptの「omitted cells remain unresolved」に従い、分類だけを保存canonical proposalから別実装で再採点した。F-low 50→46、P-low 71→67、P-none 34→30に訂正。元の `runs.json`、runner、実応答、gold、分母、計測時刻は変更せず、`scoring-corrections.json` と `scored-runs.json` に分けて保存した。全重点条件を満たす試行・品質付き時刻・採否は変わらない。初期値を含む途中集計は最終結果として使用しない。

## 3. 応答時間

単位は秒。開始はpack準備前。最初の文字とprovider終了は全10試行、意味artifactの中央値はartifactが得られた試行だけの参考値で、品質成功の中央値ではない。

| 方式 | 最初の文字・中央値 | provider終了・中央値 | 意味artifact・観測中央値 | 品質付きで5秒以内 | 品質付き中央値・失敗penalty込み |
| --- | ---: | ---: | ---: | ---: | ---: |
| F-low | 5.658 | 6.927 | 5.526（5件） | 2/10 | 60.000 |
| P-low | 4.664 | 5.537 | 5.608（7件） | 2/10 | 60.000 |
| P-none | 1.714 | 2.930 | 3.041（2件） | 0/10 | 60.000 |

60秒は品質未達・artifactなしに適用した集計用penaltyで、実timeoutではない。実timeoutは0。速度基準は品質付き中央値5秒以下、5秒以内8/10以上、各帳票最低1/2。全方式が未達である。

品質付き5秒以内はF-low/P-lowのH1各2件だけ。P-lowのT1-r2は5.931秒、F-lowのT3-r2は5.627秒で、品質は満たすが時間を超過した。両low方式がともに品質を満たす対はH1の2対だけで、F/Pは3.564/3.568秒、4.107/4.693秒だった。P-noneとの品質を揃えた速度比較は0対なので成立しない。

P-lowのprovider終了中央値はF-lowより20.07%短い。ただしF/P比較は画像とcell情報を同時に変えた入力pack全体の比較であり、画像だけの効果や一般的な速度向上とは言えない。品質未達を含む数字で、採用根拠にはしない。Phase15の旧v4との同時対照もないため、v4→v5の改善率は算出しない。

pack準備中央値はF/P-low/P-noneが44.336/70.648/83.347ms。artifactのある試行のprovider終了後ローカル処理中央値は208.702/169.675/206.826ms。今回の秒単位の待ち時間をローカル処理だけで解消できるという根拠はない。

HTTP headers、first event、first text、provider終了、prepare、decode、resolver、compile、manual投影の時間を保存した。**schema検証はresolver時間に含まれ、schemaとresolverを個別には計測していない。** scope-readyは完全応答・共通compile・manual意味投影・artifact保存を通過した時刻。品質付き時刻は全重点正解・誤結合0の場合だけその時刻を採用し、オフライン採点時間を加算しない。実UI到達・描画時間は未測定。

ストリーミングは[OpenAI公式資料](https://developers.openai.com/api/docs/guides/streaming-responses)のtyped eventを使用し、delta、terminal response、失敗を区別した。SSE全体とterminal JSONは30件すべて保存し、deltaを再結合したJSONとの一致も確認した。部分JSONは編集案へ適用していない。

## 4. tokenとcache

guard拒否4件も含む、実30応答のusageで集計した。

| 方式 | input tokens | cached tokens | output tokens | reasoning tokens | cacheあり/なし |
| --- | ---: | ---: | ---: | ---: | ---: |
| F-low | 114516 | 66175 | 5159 | 3114 | 9/1 |
| P-low | 58928 | 40614 | 4605 | 2657 | 10/0 |
| P-none | 58928 | 38381 | 2171 | 0 | 9/1 |

P-lowのinput tokenはF-lowより48.54%少ない。cacheありは部分hitと全hitを含み、方式間で共通promptのcacheも共有される。反復番号をcold/warmと見なさず、各応答のcached/cache_write tokenを記録し、hit有無別の時間も `summary.json` に残した。P-lowのcache missは0件なので、cold条件を揃えた方式比較はできない。有料warmupは実行していない。

## 5. 保存応答から確認した失敗

- **T1**：親見出しをneeds_contextとして落とす、親をfield labelへ混ぜる、親labelへ分類する出力があった。P-lowの2反復目だけ全重点正解になった。
- **T2**：全6試行で重点binding・行が0。unknown列、空行を含む印字row key、独立formへの誤分類とrole重複で表案が保留された。guardを弱めて採用していない。
- **T3**：F-lowの2反復目だけ全重点正解。P-lowの2反復目は分類/結合が合っても記号設定が0で不合格。P-noneも記号を満たさない。
- **H1**：両low方式の2反復とも正解かつ5秒以内。P-noneは親/結合を失った。
- **H2**：P-lowは記号選択肢4/4を得たが、繰り返し表を独立fieldとして出し、列＋行の結合と表構造を失った。記号の正解だけでは受け入れられない。

guardの全体拒否4件は `UNUSED_MARK_DEFINITION`。30件ともHTTP200・completed・指定モデル・v5のstrict shapeを確認したため、endpointのschema不受理ではない。範囲外変更・物理変更・hash/台帳不整合による即時停止は発生しなかった。意味誤りを記録したまま固定scheduleを完走した。

## 6. 検証・保持監査

主要実行は製品外の `audit_state.py before/after`、`benchmark.py --freeze`、`benchmark.py`、`analyze.py`、`finalize_evidence.py` と `git diff --check`。送信前に30件の固定requestと正解案を実compile/manualへ通し、誤parent・実relation欠落を検出する採点条件を確認した。

保存26案は再decode/resolve/compileし、canonical意味、名前/alias/共有/mark/親/表/行、manual投影が一致した。既存graphを入力したmanual no-opは26/26で全ID/pathを含め完全一致。残4件は拒否理由とSSE/応答を保持した。台帳・30件のresponse/request ID・usage・schema・frozen hashを独立に再照合した。

終了監査で両checkoutのHEAD、各37表の構成とDB schema、設定、863通常ファイル、781 artifact SHA、Phase11〜19の過去証拠を保持した。旧llm-ocrは全体不変。deployの通常DBは今回のAPI台帳30行の追加だけで、他36表と既存台帳336行は不変。repositoryの今回変更は本書追加とAGENTS/Safety Contractへの現在地追記だけ。staging/commit/pushはしていない。

製品OCR/provider/prompt、physical identity、mapping/crop、受付snapshot、補正revision、確定/出力実装は変更しておらず、**既存OCR contractを維持した。** 全件unit/E2E・frontend build・通常サーバー起動・実マイク試験は実施していない。製品ソースを変更しない測定フェーズであり、実製品の保存/HTTP/UI受入の代替とはしない。

実行証拠は `D:\llm-ocr-archive\template-ai\20261011-phase20`。主要ファイルは `execution-manifest.json`、`runs.json`、`scored-runs.json`、`scoring-corrections.json`、`sends.json`、`summary.json`、`saved-response-review.json`、`additional-writes-review.json`、`human-evidence-review.json`、`provider-shape-and-ledger-review.json`、`preservation.json`、`artifact-validation.json`、`sse/`、`raw/`、`compiled/`。

## 7. 次の区切り

フェーズ20の固定比較を完了し、ここで停止する。API使用は**今回30回、累計366/496、残130回**。追加送信・自動再設計・製品接続は開始しない。

次は**フェーズ21：Astra / XHigh、原則API0回で採否と次の設計方針を審査**。審査対象は、低遅延を優先しつつLunaへ任せる意味判断の範囲、既存候補から決定可能な構造の扱い、unknownを保存初期値へ混同しない評価境界。今回の表・親・記号の失敗を根拠に判断する。固定5scopeの結果を全ページ・未知帳票・矩形生成・音声UXの成功へ拡張しない。
