# AIテンプレート作成 Phase30 D 固定実API比較

2026-10-12。**固定比較を完了し、修正版v8の採用基準は不合格です。** 修正版の既知5範囲・各2試行は、元画像B・圧縮画像Cとも品質合格0/10、5秒以内の品質合格0/10でした。評価集合は完全一致1/10、正しい保留2/2です。通常製品へ接続していません。

開発対象は`C:/Users/hanam/Desktop/llm-ocr-deploy`、実験・証拠は`D:/llm-ocr-archive/template-ai/20261012-phase30d`です。[30-C審査](template-ai-phase30c-review-2026-10-12.md)、[固定計画](contracts/template-analysis-v8/phase27-plan.json)、[機械可読結果](contracts/template-analysis-v8/phase30d-result.json)を併用します。担当指定はGPT-6.1 Sol / High、現在のチャットモデル自体は未確認です。

## 条件と完了範囲

| 条件 | 固定実装・入力 | 試行数 |
| --- | --- | ---: |
| A | Phase24修正版v7、元画像 | 既知5範囲×2=10 |
| B | Phase30-B修正版v8、元画像 | 既知5範囲×2=10 |
| C | 同じv8、解析専用の圧縮画像 | 既知5範囲×2=10 |
| 評価 | C、Phase28で固定した別familyの合成入力 | 正答10＋保留2=12 |

APIモデルは全送信`gpt-6-luna`、Responsesは`reasoning.effort=low`、`store=false`、`stream=false`、既定の出力上限24576です。モデル名・usageは実応答でも照合しました。モデル仕様は[公式モデル資料](https://developers.openai.com/api/docs/models/gpt-6-luna)、Decisionsの仕様は[公式資料](https://developers.openai.com/api/docs/guides/decisions)を参照します。

開始前に30-C封印497成果物と共有台帳376行を照合しました。v8の401実装ファイルは内容を維持して新ディレクトリへコピーし、lockの絶対パスだけ移しました。実装・要求生成・画像・schema・prompt・gold・固定順・実験adapter等の953ファイルを送信前に固定しました。A/B/Cの既知入力とC評価を合わせた27要求テンプレートは、API0のoracle/manual投影検査を通過しました。正解情報は送信本文へ追加していません。

順序は範囲・反復ごとにABC/BCA/CABを巡回する固定順です。実行開始後の条件変更、回答補修、retry、warmup、追加送信はありません。42試行を終了し、情報不足E11は送信前保留でAPI0、残り41試行が各1予約・1HTTP送信でした。空いた枠は使っていません。固定の停止条件による中断はありません。

## 品質と応答時間

| 指標 | A 旧方式 | B 修正版・元画像 | C 修正版・圧縮画像 |
| --- | ---: | ---: | ---: |
| 品質合格 | 4/10 | 0/10 | 0/10 |
| 品質合格かつ5秒以内 | 1/10 | 0/10 | 0/10 |
| 品質合格例だけの中央値 | 6228.87ms | 算出不能 | 算出不能 |
| 完全manual投影一致 | 4/10 | 0/10 | 0/10 |
| role正答 | 97/146 | 8/146 | 8/146 |
| binding正答 | 54/74 | 4/74 | 4/74 |
| row正答 | 10/14 | 0/14 | 0/14 |
| mark正答 | 6/16 | 4/16 | 4/16 |
| 誤binding / 未出力binding | 4 / 12 | 0 / 70 | 0 / 70 |
| 受理結果の状態 | draft5 / partial2 / held2 / 拒否1 | held8 / partial2 | held8 / partial2 |

必要条件は全品質分母の一致、誤binding0、品質合格中央値5000ms以下、品質合格かつ5000ms以内8/10以上、各範囲で少なくとも1/2の品質合格かつ5000ms以内です。分母は拒否・保留を含む10試行のままです。Aも基準未達であり、Aへの採用・復帰を承認した結果ではありません。

| 範囲 | A ready中央値ms | B ready中央値ms | C ready中央値ms | 品質合格 A/B/C |
| --- | ---: | ---: | ---: | --- |
| T1 | 5116.02 | 4054.98 | 4206.58 | 2/0/0 |
| T2 | 6812.54 | 7605.28 | 5520.07 | 2/0/0 |
| T3 | 1502.25 | 1556.81 | 3657.77 | 0/0/0 |
| H1 | 6490.08 | 3621.68 | 4934.43 | 0/0/0 |
| H2 | 8762.15※ | 5187.02 | 7303.61 | 0/0/0 |

※A/H2は1件拒否のため、数値は受理1件だけです。拒否は品質分母から除外していません。上表のreadyは保留・部分案を含みます。B全体4170.46ms、C全体4934.43msという中央値だけで、編集速度の合格とは判定できません。C/T2の5520.07msも正しい変更案が完成した時間ではありません。

ready時計は入力取得・画像/plan/要求準備・client初期化・実DB予約commit・HTTP往復・応答検査・共通compiler/manual・証拠保存・保存後の現在性検査を含みます。Python起動/import、Settings/ORM初期化、採点は除外しました。各既知試行は新しいprocessとHTTP clientを使っています。過去のoracle/mockローカル時計とは分母が違い、30-CのT2=395.21msと実通信時間を直接比較しません。実験のdurability処理も含むため、製品UIの体感速度を測った結果ではありません。

## 保存応答で確認できる失敗

BのResponses8件はcomponent構造で`none_of_these`5件、`needs_context`3件を選択しました。CのResponses8件は同じく6件・2件でした。両者とも、この8件は操作0の保留です。残るT3のDecisions各2件はpartial draftとなり、role4・binding2・mark2までは各試行で正答しましたが、必要なrowとmark全体を満たしませんでした。

Aは4件の完全一致のほか、部分的または誤った非空案3件、保留2件、`ValueError:CONDITIONAL_FACTOR_SET`による拒否1件でした。B/Cにも品質不足の非空部分案が各2件あります。「誤binding0」は大部分を出力していない結果であり、十分な精度の証拠にはなりません。

元画像Bと圧縮画像Cの両方で保留が増えたことは観測事実です。画像圧縮だけが原因、schemaだけが原因、Lunaの能力不足、という因果関係まではこの比較で確定できません。応答は解析可能な構造で受理されており、通信エラーや単純なJSON破損として説明できる失敗でもありません。原応答を修正せず、`response-diagnostics.json`へ選択肢とcoverage理由を保存しました。

## 固定評価集合と接続再利用

完全一致は**E02のみ1/10**です。残る正答対象9件は保留で、保留対象E11/E12は2/2で正しく保留しました。評価全12件の状態はdraft1・held11です。保留対象への非空案0、正答対象への誤った非空案0ですが、正答対象の9割で必要な案を作れていません。

E01は同じ評価processの初回で1283.07ms、後続の実送信10件の中央値は3403.95msでした。E11のローカル保留224.13msは後続送信中央値から分離しています。E02〜E10とE12はtrace上でTCP再接続がなく、HTTP接続再利用を確認しました。この評価入力は既知5範囲と異なるため、初回・後続の差を接続再利用の効果として推定しません。

評価集合はPhase28の合成family分割です。未見の実帳票に対する精度の証明ではありません。校正12範囲にはAPI送信せず、結果を見て評価集合を差し替えていません。

## 検証とAPI台帳

41送信すべてで要求本文SHAと予約manifest・実送信bytesの一致、応答raw bytesとJSON/hash・モデル・usage、台帳IDの一致を確認しました。HTTPは41件すべて200、Responses31・Decisions10です。ネットワーク禁止の保存再生42件は受理41・同一拒否1を再現し、物理セル、manual投影、既存graphの完全no-op、操作、旧採点の一致を確認しました。ここでの「受理」は形状・guard・compilerの受理であり、正答を意味しません。

実行入口はdeployのPython `-B`による`experiment.py --run`、`analyze.py`、`diagnostics.py`、`replay_verify.py A/B/C`、`audit_state.py after`です。結果は実験ディレクトリの`runs.json`、`sends.json`、`requests/`、`raw/`、`raw-bytes/`、`headers/`、`compiled/`、`receipts/`、`wire-verification.json`、`replay-*.json`、`summary.json`、`preservation.json`、`result-hashes.json`へ保存します。

保存再生helperの初回は、新規meaning/relationにcompilerが割り当てるUUIDまで同値比較して停止しました。既存のmanual投影契約に従い、新規UUIDは意味・共有関係の投影で比較し、既存ID/pathは完全graph no-op、物理セルと操作は一致を要求して再実行しました。送信条件・実装・gold・採点を変更したものではありません。準備時の監査完了前参照と作業ディレクトリ指定の失敗も送信前であり、API消費・再送はありません。

APIは**今回41回、累計417/496、残79回**です。376行の旧台帳は保持し、`template.phase30d`のordinal377〜417だけを追加しました。retry/warmup/補修送信0です。usage集計はinput220533・output8164・total228697、cached63087・cache write111008・reasoning5664 tokenでした。既知試行のclientはcoldでもprovider側のprompt cacheはcoldと保証できず、固定順のcache影響は除外できません。

## 変更境界・未検証範囲・次工程

製品ソース変更はありません。repo更新は本報告、結果JSON、AGENTS.md、OCR Safety Contract、v7/v8 READMEの現在地6文書です。4文書の旧本文は保持しました。通常DBで変更するのはAPI台帳だけで、業務テーブル、schema、設定、原本、通常863ファイル・781artifact、旧checkout、過去証拠を保全監査で照合します。既存OCR contractを維持しました。今回の実験は既存OCR経路・mapping・crop・revision・保存/出力対応を変更していません。

製品の全件unit/E2E、UI、通常DBへの案反映/保存/undo、実マイク、未知実帳票の精度は未検証です。本比較の失敗を局所最適化や追加API試行で補修せず、固定結果のまま引き渡します。

次は**Phase31：Astra / XHigh、API0の採否審査**です。今回のCは採用条件を満たしておらず、製品への接続は行いません。Phase31では証拠と採点の妥当性、過剰保留の切り分け、次に設計を変更する範囲を審査します。次の指示まで開始しません。
