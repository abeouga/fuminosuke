# Template analysis v8 意味判断と転送形式の修正設計

2026-10-13：**Phase34-Eは未完了。資料の候補到達性とT2時間基準が未達のため修正設計へ戻す**。[34-E報告](../../template-ai-phase34e-2026-10-13.md)と[結果JSON](phase34e-result.json)を現在地とする。
製品外コピーで再生/実送信の分離、HTTP入口の一回許可、固定experiment/追記専用記録、共有予約ID、明示goldラベルを実装。新境界18+21+8+10、pair23+上限6、旧30gold、正例回帰2、旧82応答/24 DTOの歴史再生を確認。ただし全面的な旧335項目の新入口移植、出典/負例意味/画像の独立審査は未完了。
新資料はoracle29/30。V3-09の3行記号フォームが候補不足で、候補生成を変えない34-D境界に達した。T2ローカル中央値は583.22→515.46ms、500ms未達。実応答時間・モデル精度は未検証。候補/正解/分母は合格させるために変更しない。
実API/共有予約0、累計417/496・残79。製品未接続、34-C差戻しを維持。次はAstra/XHigh・API0の修正設計、その後34-E完了と34-F独立審査。35〜38は保留。以下は従来時点の記録。

2026-10-12：**Phase34-D修正設計完了。実装・実APIは未開始、34-Cの差戻しを維持**。[34-D設計](../../template-ai-phase34d-design-2026-10-12.md)と[計画JSON](phase34d-plan.json)を現在地とする。
R1は再生/実送信の分離とHTTP境界の一回許可、R2は固定experiment ID/記録先/共有予約IDの対応、R3は全資料の出典・派生審査、R4は値の知識不足と意味設定不能の区別で修正する。cohort v3、校正12/評価18、D1〜D12受入条件を定義。候補prototype2/回答v8/GPT-6 Luna/閾値/共通manualは維持。新資料・新動作は未検証。
今回使用/予約0、累計417/496・残79。製品/旧checkout/両DB/通常データ/台帳/原版/旧本文を保持、既存OCR contractを維持。次は34-E=GPT-6.1 Sol/High・API0実装、34-F=Astra/XHigh・API0再審査。指示まで未開始。34-F合格まで35〜38は保留、上限18/24/28の70回・未割当9回を未予約で保持。以下は従来時点の記録。

2026-10-12：**Phase34-C独立審査は差戻し。実比較・製品採用は未承認**。[34-C審査](../../template-ai-phase34c-review-2026-10-12.md)と[結果JSON](phase34c-result.json)を現在地とする。
API0入口の実HTTP通過、journal root変更による同一trial再実行、校正由来3評価資料、文脈不足gold2件をR1〜R4として指摘。既存335+新pair23+実験41+旧審査5+reader6は再実行通過。33画像の越境0、旧30gold一致、旧30/10/42応答と24 DTO歴史再生、5対象のローカル中央値500ms以下（T2 477.3ms）。これらは新指摘の免責や実応答速度の保証ではない。
今回使用/予約0、累計417/496・残79。製品/旧checkout/両DB/通常データ/台帳/元成果物/旧本文を保持し、既存OCR contractを維持。次工程案34-D=Astra/XHigh・API0修正設計、34-E=GPT-6.1 Sol/High・API0実装、34-F=Astra/XHigh・API0再審査。指示まで未開始。35〜38は保留、上限18/24/28の70回・未割当9回は未予約。以下は従来時点の記録。

2026-10-12：**Phase34-B製品外修正実装・API0検証完了。独立審査待ち、実比較/製品採用は未承認**。[34-B報告](../../template-ai-phase34b-2026-10-12.md)と[結果JSON](phase34b-result.json)を現在地とする。
R1の記号欄6組を補完して旧30件27/30→30/30。単独欄の固定親候補、文字所属を検査した新30枚＋修正回帰3枚、校正6family×正負2件（全12件Decisions）と評価18件を固定。凍結cohort/選定/trial/runを結合し、入力生成前から永続attemptを消費、重複・再起動・応答混入・予約後/公開前改変を拒否する。
既存境界335、新pair23、新実験境界41、追加5シナリオ、旧rawの旧実装コピー再生が通過。trial照合を含む5対象ローカル中央値は全て500ms以内。実モデル精度・実応答5秒は未検証。独立gold/画像/source family審査と実帳票4family条件は未達、合成結果だけでは採用しない。
今回API/予約0、累計417/496・残79。製品ソース・両DB/通常データ・台帳・原版/過去証拠・旧本文を保持、既存OCR contractを維持。次は34-C=Astra/XHigh・API0。合格まで35〜38は保留、上限18/24/28の70回・未割当9回は未予約。モデル切替後の指示まで次工程を開始しない。以下は従来時点の記録。

2026-10-12：**Phase34-A修正設計完了。実装・実APIは未開始、Phase34の差戻しを維持**。[34-A設計](../../template-ai-phase34a-design-2026-10-12.md)と[計画JSON](phase34a-plan.json)を現在地とする。
R1〜R4を、欄内記号凡例の有限候補、単独1項目/1階層の固定親候補、文字越境の検査、凍結cohortとtrial/runの結合で修正する設計。候補policyをprototype2へ改訂し、旧receiptを転用しない。回答v8・全claims・物理/現在性/同値手動編集・共通manualは維持する。
校正v2は6元family×正負2件の12件、全件Decisions適格を受入条件とする。旧30件は原版を保持して開発/回帰へ移し、最終評価18件は別の元資料で準備。新規実帳票4family条件は未達、合成比較の成功だけでは製品採用しない。
比較割付は正確なID/split/family/hash/順序に固定し、予約前後・公開前までtrialを照合する。35/36の結果と選定成果物なしに後続計画を発行しない。一trial一attempt、retry/返金0、ローカル5scope中央値500ms以下と実応答5秒の条件を維持。
今回API0、累計417/496・残79。製品コード・実装原版・両DB/台帳・通常データ・過去証拠・旧本文を保持、既存OCR contractを維持。次は34-B=GPT-6.1 Sol/High・API0の製品外修正実装、続いて34-C=Astra/XHigh・API0の独立再審査。合格まで35〜38は保留、上限18/24/28の70回・未割当9回は未予約。モデル切替後の指示まで次工程を開始しない。以下は従来時点の記録。

2026-10-12：**Phase34独立審査完了。Phase33は差戻し、実API比較へ進めない**。[Phase34審査](../../template-ai-phase34-review-2026-10-12.md)と[審査JSON](phase34-review.json)を現在地とする。
R1：V08/V09/V10の6記号pairが候補不足。R2：K02/K04/K06の6ラベルが値欄へ26px越境。R3：比較割付が同一scopeの重複を拒否しない。R4：校正に記号・親子対応・親競合・範囲端文脈欠落・画像/文字矛盾の被覆が不足。原版・goldは保持。
既存境界335項目＋独立5シナリオ通過、保存42試行=41受理/1同一拒否。独立gold内部照合30/30、期待DTOのmanual再生30/30、有限候補からの到達27/30。原画像30枚を確認したがデータ受入は不合格。T2ローカル中央値400.38ms、全5scopeで500ms以下。実通信5秒・モデル精度の改善証拠ではない。
製品ソース・審査対象runtime・両DB全表/台帳・通常データ・過去証拠・旧本文を保持、既存OCR contractを維持。今回API0、累計417/496・残79。製品不採用・未接続を維持。
次工程案は34-A=Astra/XHighの修正設計、34-B=GPT-6.1 Sol/Highの製品外修正、34-C=Astra/XHighの独立再審査（すべてAPI0）。合格まで35〜38は保留、計画上限70回・未割当9回は未予約。次工程はモデル切替後の指示まで開始しない。以下は従来時点の記録。

2026-10-12：**Phase33の製品外実装・境界検証を実施。データ受入未達のため実API比較は停止**。[Phase33報告](../../template-ai-phase33-2026-10-12.md)と[結果JSON](phase33-result.json)を現在地とする。
候補の設定結果を示す説明、固定されたDecisions採用policy、設定/receiptの所有検査、比較割付を実装。候補集合・v8回答schema・全claims・共通manual・物理/現在性/同値手動編集を維持。製品コードと通常UIへの接続はない。
境界335項目通過、旧42試行=41受理/1同一拒否を再現。全5scopeのローカル中央値500ms以下、T2=403.17ms。HTTP/実台帳/証拠保存を含まず、実応答5秒やモデル精度の改善証拠ではない。
新規合成30範囲の期待投影は27/30。V08/V09/V10の単独記号pairを既存有限候補で表現できず、期待値は保持。校正の単独記号被覆と新規実帳票4familyも未達。独立gold審査前、製品不採用を維持。
API今回0、累計417/496・残79。製品ソース・両DB全表/台帳・通常データ・過去証拠・旧本文を保持し、既存OCR contractを維持。次は34=Astra/XHigh・API0で実装/期待値/候補被覆不足の審査。データ受入未達を解消せず35〜37の実送信へ進めない。次工程はモデル切替後の指示まで開始しない。以下は従来時点の記録。

2026-10-12：**Phase32修正設計完了。Phase31の製品不採用を維持し、runtime修正・製品接続は未開始**。[Phase32設計](../../template-ai-phase32-design-2026-10-12.md)と[計画JSON](../template-analysis-v8/phase32-plan.json)を現在地とする。
候補を選んだ設定結果とname所属を示す入力表現を設計。回答v8/schema・候補集合・全claims・共通compiler/manual・物理/現在性/同値手動編集を維持する。指示だけ→候補説明だけを変える18回の比較後、独立したcontext質問とchoiceの24回校正を行う計画。一律閾値解除・保留の強制補修はしない。
既知5scopeとE12の6説明例をAPI0で照合。製品精度・応答速度の改善証拠ではない。新規校正12/評価18はfamilyで分離し、既知E01〜E12・旧校正を最終評価へ流用しない。次は33=GPT-6.1 Sol/High・API0の製品外実装、34=Astra/XHighの審査、35=Sol/High最大18、36=Astra/XHigh最大24、37=Sol/High最大28、38=Astra/XHigh採否。各段階の不合格で後続送信を止める。
今回API0、累計417/496・残79。将来比較の計画上限70・未割当9、今は未予約。製品ソース・DB・通常データ・過去証拠を保持し、既存OCR contractを維持。次工程はモデル切替後の指示まで開始しない。以下は従来時点の記録。

2026-10-12：**Phase31採否審査完了。30-Dの測定記録と不合格判定を受入、v8の製品不採用を確定**。[Phase31審査](../../template-ai-phase31-review-2026-10-12.md)と[審査JSON](phase31-review.json)を現在地とする。
791封印成果物・953実行固定hash、41要求/原応答/台帳、82画像を照合。保存42試行=41受理/1同一拒否、別採点と全計測を再現。A品質4/10・5秒以内1/10、B/C品質0/10、評価完全一致1/10・正しい保留2/2を維持。
v8 Responses23件中22件は生の構造回答で保留。B/C T3は第一候補28/28正解でも採用8/28。閾値を外すAPI0診断で5件は正答へ復元するが、保留対象E12にも誤った見出し案が生じる。E09正答.04/.02に対しE12誤答.12/.03（confidence/確率差）で、一律の閾値引下げは採用しない。30-DのT3説明はrow不足ではなくrole/binding/mark不足と訂正、旧証拠は保持。
API今回0、累計417/496・残79。製品ソース・両DB全表/台帳・通常データ・過去証拠を保持し、既存OCR contractを維持。製品接続なし。次はPhase32=Astra/XHigh・API0で判断用入力表現、保留と校正、比較の切り分け、独立評価集合の修正設計を推奨。次の指示まで未開始。以下は従来時点の記録。

2026-10-12：**30-D固定実API比較を完了、修正版v8は採用基準不合格**。現在地は[30-D報告](../../template-ai-phase30d-2026-10-12.md)と[結果JSON](phase30d-result.json)。既知5scope×2×A/B/C＋評価12の42試行を完了、実送信41（Responses31/Decisions10）、E11は送信前保留0、retry/warmup/補修0。
Aは品質合格4/10・5秒以内1/10・合格例中央値6228.87ms。B/Cは品質合格0/10、各held8/partial2で完全manual投影0/10。CのT2 ready中央値5520.07msも正答時間ではない。評価は完全一致1/10・正しい保留2/2、残る正答対象9件は保留。元画像Bも失敗しており圧縮単独の原因とは断定しない。
953実行固定hash、41要求/原応答/台帳の一致、保存再生42件=41受理/1同一拒否を確認。API今回41、累計417/496・残79。製品ソース・業務データ・過去証拠を保持、通常DBの変更はAPI台帳のみ。既存OCR contractを維持。製品接続なし。
次はPhase31=Astra/XHigh・API0の採否審査。現在のチャットモデル自体は未確認。次の指示まで開始せず、固定結果を追加送信で補修しない。以下は従来時点の記録。

2026-10-12：**30-C独立再審査を通過**。現在地は[30-C審査](../../template-ai-phase30c-review-2026-10-12.md)と[審査JSON](../template-analysis-v8/phase30c-review.json)。製品外30-B実装を固定比較へ進める版として受入。差戻し指摘なし。製品採用・実モデル精度・実通信5秒の合格ではない。
開始時927成果物・401実装を照合。コピーで既存201＋修正51境界、保存40応答の35受理/5同一拒否、合成24scopeと既知5scopeのmanual投影を再現。審査側9条件で最終検査後の中止/同値編集を送信0、8件再入を1予約/1送信へ限定。予約応答喪失、要求bytes変更、書込失敗、proof改変も拒否。
各3反復のT2再測定=395.21ms、全5scope中央値500ms以下。保存された変更前後計測を再計算し一致。5scopeの元crop・転送画像・完全要求本文も変更前後一致。oracle/mockであり実通信時間の証拠ではない。
今回API0、376/496・残120。製品ソース・通常データ・原本を保持し、既存OCR contractを維持。次は30-D=GPT-6.1 Sol/High、GPT-6 Luna最大42送信・Responses low、retry/warmup/補修0。次の指示まで実API比較を開始しない。以下は従来時点の記録。

2026-10-12：**30-B修正実装のローカル受入を通過、独立再審査待ち**。現在地は[30-B報告](../../template-ai-phase30b-2026-10-12.md)と[結果JSON](phase30b-result.json)。実装は製品外`D:/llm-ocr-archive/template-ai/20261012-phase30b`、製品接続・通常移行なし。
R1〜R3を一run一所有thread、原子的な開始/dispatch権、待機/予約後の内容・現在性検査で修正。中止待機者はownerのguardと接続枠を解放しない。対象の共有StateSourceは同値編集でもepochを更新し、後続指示で旧応答を拒否する。画像の同期区間内証拠と元PNG decode共有を追加し、次区間は内容を再検証。
変更前/後各3反復でT2=639.69→389.46ms、全5scope中央値500ms以下。既存201＋追加51境界、保存40応答の35受理/5同一拒否と旧採点、合成24scope期待投影を確認。全claims/schema/実装内容/共通compiler/manualと元cropを維持。401実装を固定。oracle・mockであり実モデル/通信の成功ではない。
今回API0、376/496・残120。既存OCR contractを維持。次は30-C=Astra/XHigh・API0の独立再審査。30-Dの有料比較は再審査受入と次の指示まで開始しない。以下は従来時点の記録。

2026-10-12：Phase30の開始準備として**30-A修正設計を完了**。現在地は[30-A設計](../../template-ai-phase30-readiness-design-2026-10-12.md)と[計画JSON](phase30-readiness-plan.json)。Phase29の差戻しと実API比較の停止は維持。修正実装は未開始。
R1〜R3は一run一実行所有者、原子的な開始権・一回dispatch権、初期化/予約後の再検査で修正する設計。T2は同期区間内の不変bytes証拠と元画像の一回decodeにより重複計算を減らす。現在性・全実装内容・schema・全claims・共通compiler/manualの検査と500ms条件を維持する。
工程30-A=Astra/XHighの設計、30-B=GPT-6.1 Sol/Highの製品外実装、30-C=Astra/XHighの独立再審査、30-D=当初のSol/High・GPT-6 Luna最大42送信の比較、31=採否審査。各区切りで次の指示を待つ。現在のチャットモデルは未確認。
今回API0、376/496・残120。Phase29の封印476成果物を開始時照合し、未修正Phase28 T2の一回profileで完全manual投影を確認。profile時間は性能受入値ではない。製品ソース・通常データ・旧証拠を保持し、既存OCR contractを維持。以下は従来時点の記録。

2026-10-12：Phase29独立審査は**差戻し**。現在地は[Phase29審査](../../template-ai-phase29-review-2026-10-12.md)と[審査JSON](phase29-review.json)。Phase30の実API比較へ進めない。
新規3件をネットワーク禁止で再現：R1/P1 同一runの同時実行で2予約・2送信、R2/P2 client初期化中のキャンセル後に未送信要求が送信される、R3/P2 台帳予約中のepoch変更後も古い要求を送信する。R3は応答後の現在性検査でdraft返却を拒否。通常DBの誤適用を再現したものではない。T2は保存中央値625.34msを確認、審査再測定634.55msで500ms未達を継続。
Phase28の634成果物・実装399ファイルを照合。コピー上で既存201境界、保存40応答の旧採点、合成24scopeの期待DTO投影を再現。修正実装・製品接続なし、既存OCR contractを維持。API0、376/496・残120。次は3件＋T2の修正設計・実装・独立再審査。次の指示まで開始しない。以下は従来時点の記録。

2026-10-12：Phase28の製品外修正実装とオフライン検証を実施。**受入不合格：T2ローカル中央値625.34msが500ms上限を超過。Phase30の実API比較へ進めない。** 現在地は[Phase28報告](../../template-ai-phase28-2026-10-12.md)と[結果JSON](phase28-result.json)。
意味提示/構造別branch schema/所有alias展開/元crop保持の転送pack/最大一送信の共有HTTP・期限・現在性検査を実装。旧runtime400ファイルと候補/全claims/共通compile/manualを保持。既存83＋48件、新v8 70件、旧保存40応答の採点再現、既知5scopeのoracle完全manual投影を確認。校正12＋評価12の合成familyと期待値を固定。実モデル品質・endpoint受理・実通信時間は未確認。
API0、376/496・残120。製品接続・通常データ移行なし。既存OCR contractを維持。次はPhase29=Astra / XHigh・API0の独立審査。次工程の指示まで開始しない。以下は従来時点の記録。

2026-10-12。`template-analysis.semantic-answer.v8.design1`。Phase27で固定した**製品外試作の設計**です。実装・実API受理・精度・5秒条件は未確認です。[設計報告](../../template-ai-phase27-design-2026-10-12.md)、[機械可読計画](phase27-plan.json)、[Phase26審査](../../template-ai-phase26-review-2026-10-12.md)を併用します。

対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。通常製品へ接続せず、Phase28の実装は新しい製品外ディレクトリで行います。v7の構造候補・意味の選択肢・共通compile/manual経路を維持し、モデルへ提示する内容、条件付き回答、解析専用の画像転送形式を変更します。v7原応答・採点・画像・凍結実装を上書きしません。

## 1. 修正目的と採択方式

H1では独立した項目群を繰り返し表と誤解し、H2では行の名称を識別キー、印字済み説明を入力列として選びました。IDが整合しても、この種の意味の誤りは起こります。一方、H2の別構造choiceは、生成時のschemaでも防止できる組合せ違反です。両者を分けて修正します。

| 方式 | 判断精度の機会 | 応答時間と保守上の費用 | 採否 |
| --- | --- | --- | --- |
| 独立した有限選択をDecisionsで一括 | 同じ証拠を使う独立質問に適する | 一往復。先行回答に依存する質問を混ぜられない | 継続 |
| 構造ごとの回答形式を持つResponses | 構造、親、列用途、名称を一度に整合させる | 一往復。schemaが少し大きくなる | 条件付き判断の主経路 |
| Decisions構造選択後、選ばれた質問だけ再送 | 第二段を小さくできる | 必要時二往復。第一段の誤りは後段で救えず、実測上の優位も未確認 | 今回の試作から除外 |

採用経路は**一つのrunにつき最大一送信**です。Decisionsで保留になった後のResponses補完、自動retry、schemaエラー後の別形式再送、裏の並走を設けません。二段方式はv7の過去証拠として残し、v8の運用fallbackにはしません。

送信前にplanだけから次の順で経路を決めます。

1. 全componentが保留なら送信0、coverageを返す。
2. 有効な全構造が、自由名称も先行選択に依存するfactorも持たず、独立した固定質問で完結する場合はDecisions一回。
3. それ以外の有限planはResponses一回。componentの経路が混在しても全体をこの一回にまとめる。
4. schema・入力・候補数の上限外は送信前保留。順番による候補削除や自動分割をしない。

case名、oracle、前回の正解率、生成後の答えを経路選択に使いません。T1/T2/H1/H2がResponses、T3がDecisionsになるのは現在のplanからの結果であり、帳票名による分岐ではありません。APIモデルは両経路とも`gpt-6-luna`。Responsesの`reasoning.effort=low`、`store=false`、`stream=false`、既存の出力上限24576を維持します。出力上限を減らすだけで高速化したとは判断しません。

## 2. 内部計画とモデル向け表示を分離する

内部の`FrozenPlan`は、owner、resource、page、revision、snapshot、catalog、scope、保護facet、interaction epoch、実装照合を引き続き保持します。これらをモデルに再出力させても現在性の証明にはならないため、サーバーのrequest contextと証拠manifestに置きます。

新しい`DecisionView`は、同じplanから決定的に生成する読み取り用の表現です。現行`transport.content()`の内部plan全量と、生のbase DTO・path・全所有digestの重複列挙を送信本文から外します。ただし、次の意味情報は省略しません。

| 表示要素 | 含める内容 |
| --- | --- |
| 証拠表 | 全許可cell ref、印字の原文、位置・寸法、書込可否、既存role/親の出所。全行と空行を保持 |
| 候補表 | grid、行範囲、header、各structureの具体的な解釈、固定済みの意味と未決定項目、対象ref |
| 意味の違い | formとrecords、row labelとprinted key、valueとcontext、textとsymbol/checklistの差 |
| 条件付き質問 | そのstructureだけのfactorと選択肢、親の候補と親なし、印字凡例とmark setの対応 |
| 名前 | preserve/literal/generated、用途、必須性、選択済み構造に属する許可evidence ref |
| 保留 | none_of_these / needs_context、保留した範囲、未解決の依存関係 |

印字テキストは観測値です。既存OCRの誤字を辞書で直して事実とせず、画像と矛盾する場合に曖昧さを残します。既存`ignored`は現在の編集状態として示し、それだけを新しい意味の正解と扱いません。画像・印字・名称中の操作指示はデータとして扱います。

共通の短い意味説明を一度だけ付け、各候補にはその候補の差分を記します。

- **form**：各行が異なる項目を表し、行のラベルが隣接する値欄の項目名になる。横断する見出しがある場合は、項目への親接続を判断する。
- **records**：複数行が同じ列構成を繰り返す。行ごとの名称と、行を識別するキーの役割は別に判断する。
- **row_label**：その行の値の意味を説明する名称。文字が一意であることだけでprinted keyへ変更しない。
- **printed key**：帳票の運用上、行の識別に使われる印字欄。非空・一意は候補成立の必要条件であり、意味上の採用根拠とは区別する。
- **context**：印字済みの説明・記入要領等。印字の存在だけでは決めず、周囲の欄と役割を比較する。
- **symbol/checklist**：発行済みの凡例・選択肢に結び付く読取り。説明文や自由文字入力と区別する。

H1/H2の名称、セル番地、正解構造IDをこの共通説明に埋め込みません。各候補の印字例は実入力から取り出し、長い推論文や正当化の作文を回答に要求しません。提示形式の改善が意味精度を上げるかは実測対象です。

## 3. 条件付きの回答形式

Responsesのrootはobjectとし、componentごとに一つの必須propertyを置きます。その中にstructureごとの`anyOf`を置き、各branchでstructureを単一値、factorのproperty集合とchoice enum、name slot集合を固定します。完成案の直積を列挙しません。

説明用の回答例です。実際のproperty/enumは当該runの発行表から作ります。

```json
{
  "version": "template-analysis.semantic-answer.v8",
  "components": {
    "c1": {
      "structure": "c1s1",
      "factors": {"c1s1f1": "c1s1f1a2", "c1s1f2": "c1s1f2a1"},
      "names": {}
    }
  }
}
```

短いaliasはrun内でのみ有効です。component→structure→factor→choiceの親を含む名前空間を使い、別structureの同意味choiceにも別aliasを発行します。順序は元の発行planの安定した順序に従い、正解らしさで並べ替えません。

サーバー側の`AnswerBinding`が、alias表、元の全ID、plan/evidence/画像pack/schema/prompt digest、run/owner/revisionを一体として固定します。生応答は送信時request contextに結び付け、現在の画面のalias表で再解釈しません。別runの`c1s1`を再利用しても所有証明にはなりません。モデルから受け取るものは選択と名称だけで、座標、write scope、path、永続IDの生成を許しません。

構造全体を保留するbranchは`structure`だけを持ち、子回答を禁止します。選択済みfactorの保留、必要名称のnullは従来どおり依存componentを保留します。すべてのobjectに`additionalProperties=false`と全propertyの`required`を設定します。nameのtextはnullable、evidenceは当該slotの有限ref配列です。空・重複・範囲外ref、空白だけの名前、100文字超過等はサーバーでも検査します。

schemaが防ぐのは組合せ違反であり、合法な誤解釈は防げません。受信後にも元の`project()`相当の厳密な集合・所属・名前検査、scope/currentness、全claims、依存componentの閉包、共通compile、manual投影を維持します。同じ値の別IDへ補修したり、次点候補へ置換したりしません。

今回のローカル設計例は5scopeのoracle形状を受理し、別構造choiceと余分なpropertyを拒否しました。これは生成器・runtime adapter・実endpointの完成確認ではありません。実schema受理はPhase30の通常の第一試行で確認し、失敗も予定回数に含めます。

## 4. 名称と保留の契約

表題はグループの表示名、項目名は値の意味・結合を表す名前として、別のslotと説明を与えます。ただし現行compilerが要求する名前をv8で任意化しません。表題も完全manual投影の比較対象に残します。

- 既存の手動名はpreserve。literalは選択済みの印字見出しとそのrefを利用する。
- generatedだけモデルへ生成を要求する。別structureの未選択slotの回答は要求せず、返答も拒否する。
- 必須名がnullなら依存componentをheldにする。「入力値」「表1」等の自動placeholderで通さない。
- 未確定の構造を診断表示する場合は「未確定・適用不可」とし、`operations=[]`。完成draftの件数や5秒成功に加算しない。
- 名称の言い換えだけの不一致は別項目として集計するが、元の全意味一致・誤結合・mark等の分母を変更しない。

T1/T2/H1は正解構造にgenerated nameを必要としないものの、選択前にそれを仮定して候補を捨てません。H1のrecords誤選択に名称だけを追加しても直らない、というPhase26の診断を回帰条件にします。

## 5. 入力画像pack

試作する一つのpackを`scope-original-overview-jpeg85-if-smaller.v1`とします。対象欄の詳細cropは**元のPNG bytesそのまま**、`detail=high`。全体画像は寸法・向き・範囲を保持し、RGBの場合にだけJPEG quality85、4:4:4（subsampling=0）、optimize=false、progressive=falseで一度符号化します。元PNGより小さい場合だけ採用し、それ以外は元PNGを送ります。非RGBやcodec利用不能時は元PNGを選び、理由を記録します。

画像数2、全体の`detail=low`、許可read/write refs、canonical座標、geometry、providerの読取り粒度を維持します。縮小、二値化、色変換、cropの移動、追加のOCR/AI、参照範囲の増加はしません。これはテンプレート解析の**転送用複製**であり、製品のOCR cropや原本・fieldPreviewを更新する処理ではありません。

新packのmanifestには、原画像SHA、送信bytes SHA、MIME、寸法、detail、codecとlibrary版・全設定、元pack digest、ref→原画像座標の対応、可逆/非可逆の別を保持します。JPEGの全体画像は画素が変わるため「同一画像」と扱いません。caption/mark根拠は元の許可refと結び、画像を小さくできたことを読取り正解の証明にしません。checksum・decode・MIME・寸法に不整合があれば送信前停止です。

Phase27では原画像を変えないコピーによる一回のcodec試算を行いました。

| scope | 元要求bytes | 全体画像だけを変更した要求bytes | 選択 | 削減率 |
| --- | ---: | ---: | --- | ---: |
| T1 | 331078 | 331078 | 元PNG | 0% |
| T2 | 4447867 | 1226196 | JPEG | 72.43% |
| T3 | 336736 | 336736 | 元PNG | 0% |
| H1 | 256978 | 256978 | 元PNG | 0% |
| H2 | 242234 | 242234 | 元PNG | 0% |

T2の全体画像は2871232→454978bytes、符号化だけの一回値は7.65ms。decode・検査・pack・送信を含む時間ではありません。可逆WebPの同じ全体画像は2064590bytes、符号化586.58msであり、他の3scopeでは元PNGより大きくなりました。このため可逆WebPの一律導入を採りません。

実装後にcrop bytes完全一致、全画像の寸法・範囲・ref対応、細い線・小さい印字・凡例・親見出しを照合します。元PNGを使うv8と新packを使うv8の別armで品質を比較し、圧縮armだけの誤結合/mark欠落があればpackは不採択です。圧縮率から待ち時間改善率を推定しません。

## 6. 実装責務と上限

次のモジュール名はPhase28の製品外試作先です。既存ファイルへ巨大な分岐を追加せず、純粋な変換と送信/計時を分離します。

| モジュール | 入力と出力 | 禁止する責務 |
| --- | --- | --- |
| `semantic_view.py` | FrozenPlan＋許可証拠→DecisionView＋alias manifest | gold、DB、HTTP、印字補正、候補削除 |
| `branch_schema.py` | 同じ発行表→branch schema＋サイズ検査 | 完成案直積、意味上の正解判定 |
| `answer_adapter.py` | 応答＋送信時binding→元ID付き選択→既存finish境界 | ID推測、別runのalias受理、直接保存 |
| `image_transport_pack.py` | 検証済み画像→送信用bytes＋変換manifest | geometry変更、原本更新、OCR実行 |
| `run_transport.py` | 固定route/一送信/台帳/キャンセル/時計 | 意味補修、再試行、状態をまたぐ無検証再利用 |

v7 `factor_plan.generate()`の候補集合、`assemble()`、resolver/compiler/manualの意味を維持します。新packを既存EvidenceSnapshotへ後から差し込まず、v8の生成時に原packと転送packの両方を固定し、旧proofを新bytesの証明に流用しません。実装pinに新モジュール・codec依存版を含め、実装照合を省略しません。v8の回答と新packをv7として再生しないよう、providerContractとartifact種別を分離します。

アプリ上限は32択（保留を含む）、有効64質問、plan全体512質問/8192選択肢を維持します。v8 schemaは最大4000 properties、900 enum値、対象文字総数100000、object/arrayの深さ8、UTF-8 128KiB、本文とschemaの合計256KiBに制限します。入力画像を含む実JSONは最大8MiB。これらは実API上限より狭い**試作制限**です。超過時は理由をcoverageに残して送信0。未処理セルを落として成功にしません。空の候補集合や空evidenceの必須nameも保留です。

選択後の唯一の経路は、alias厳密展開→選択検査→全claimsの競合検査→既存v5 DTOへの明示変換→resolver/compiler→manual投影です。保存段階は将来も`manual-authoring → MutationCore → TemplateAuthoringService`とし、AI専用の保存口を増やしません。

## 7. 接続と計時

HTTP clientは同じbackendプロセスの寿命で共有できる形にし、認証や設定の更新時に交換します。共有するのは接続poolだけです。runの可変plan、response、owner、DB session、guardをclientに持たせません。同一編集対象は一runを有効とし、新しい指示でinteraction epochが変われば古い応答を適用しません。別対象の同時実行は最大2、pool待ちは計時に含めます。

送信前にcurrentnessと実装を照合し、共通台帳へ一送信分を原子的に予約・commitします。予約後の通信失敗・中断を払い戻しません。接続切断時のSDK/HTTP自動retryは0。応答待ちの間は既存のguard契約どおりに解放し、受信後に再取得・再検証します。キャンセル後の遅延応答、同値の手動再編集、元値へ戻した変更も保護します。

主時計は、依頼を受けて準備を開始してから、検査済み案と証拠の永続化が完了するまでの`scopeReadyMs`です。起動/importは別表示とし、ユーザー要求後に発生するclient初期化、codec、schema、pool待ち、台帳予約、HTTP、compile、必要な証拠書込を除外しません。

| 区間 | 記録 |
| --- | --- |
| 入力準備 | snapshot検査、元pack、転送pack、plan、view/schema、各bytes |
| 送信前 | 実装/currentness、pool待ち、台帳commit、client初期化 |
| HTTP | send開始、request body送出完了、response header受領、body読了。観測不能ならnullと理由 |
| 応答後 | decode、schema/所属、共通compile/manual、最終現在性、証拠永続化 |

body送出完了はローカルtransportの観測であり、OpenAIが受け取り終えた時刻ではありません。TTFBを純粋な推論時間と呼ばず、ネットワークとproviderの合計として扱います。初回と接続再利用可能なrunを分け、実際の接続再利用を観測できなければ「warm確定」と記録しません。prompt cacheの命中を前提とせず、実usageだけを記録します。streamの先頭tokenや最初の構造選択を完成時刻にしません。

全体deadlineは準備開始から60秒とし、pool待ち・予約後も残り時間を引き継ぎます。時間切れや失敗は未完成とし、失敗を含む集計では従来の60000ms penaltyを維持します。5秒は成功の目標であり、5秒で打ち切って遅い例を集計から除く設定にはしません。

## 8. 信頼度と評価データ

今回のv8ではDecisionsのconfidence≥0.90かつ第一/第二候補差≥0.15を維持します。T3の14判断を使って閾値を下げません。全確率の形状・有限値・範囲・分布、最高候補との一致、refusal・欠落・重複も既存どおり検査します。Responsesへ同じconfidence値を仮設しません。

既知のT1/T2/T3/H1/H2は開発回帰集合です。未知帳票の保証にはしません。Phase28で次の**別ファイル・別template family**の分割manifestと期待値を固定します。実装と質問生成器は期待値を読みません。

- 校正用12scope：独立欄/見出し/記号の判断を含む。閾値変更の将来検討用。Phase30では送信しない。
- 評価用12scope：別familyに分け、個別欄・繰り返し・行ラベル対key・説明列・記号・親を含む。うち10は一意の全意味正解を持ち、2は必要証拠不足のため保留が正解。名称を含む期待値と採点を実API前に固定する。

同じ原本のcrop、回転、色変更だけを別familyと数えません。新規の実帳票が足りない部分は合成と明示し、未見実帳票の精度と呼びません。入力不足を埋めるため業務ファイルを無断編集しません。将来閾値を変更する場合は校正集合で候補を選び、凍結後に未使用の評価集合へ一回適用します。失敗した評価集合へ合わせ直して同じ結果を独立検証と呼びません。12scopeだけで母集団誤り率やp95の保証はしません。

## 9. 実装と審査の受入条件

Phase28の最初に期待条件を固定し、短い保存応答/例による確認から始めます。重複unit追加や無関係な全件E2Eを既定にしません。

1. 5scopeで元plan候補・coverage・全claimsがv7と一致し、oracle選択＋必要名称を与えた場合、全重点・完全manual投影5/5、既存全ID/pathのno-opが成立する。
2. 新しい回答形式でH2の別構造choice、別run/scope/ownerのalias、余分/欠落factor/name、子回答付き保留を拒否する。合法だが意味を誤るH1/H2を「schemaが直した」としない。
3. 必須名null、親なし/親競合、重複/空key、mark証拠欠落、保留componentへの侵入、列順変更、同名ラベル、説明列追加、上限超過を確認する。既存83＋48境界と待機中同値編集/実装reloadの2条件は再利用し、新形式で新たに生じる欠落だけ追加する。
4. 元30応答とPhase25の10応答は旧adapterで再生し、旧採点・拒否を保持する。v8へ意味を推測して変換しない。
5. 画像packの元bytes/送信bytes/寸法/detail/ref対応、crop完全一致、JPEG非可逆の明示、codec失敗、サイズ超過、manifest改変を確認する。
6. mock transportで最大一送信、送信前予約、予約競合、timeout/cancel/遅延応答、現在性変化、共有clientの対象混線を確認する。mock成功をendpoint受理としない。
7. キャッシュなしの各scope3反復で、準備＋新pack/view/schema＋検査/compileのローカル合計中央値500ms以下。候補の全直積や案ごとのcompileを作らない。500ms未達ならPhase30へ進めない。

Phase29で別の視点から上記証拠・実装・採点の独立審査を行い、合格した実装manifestを固定します。API送信開始は次の明示された工程です。

## 10. 実API比較と終了条件

Phase30の計画上限は**42送信**、自動retry/warmup/修復/追加診断0。現在は予約しません。共有残枠120以内ですが、開始時に残枠を再確認します。

主比較は同じ5scope×2反復×3armの30試行です。各scope/反復内でA/B/Cの順番を循環させた予定表を送信前に固定します。

- A：凍結v7、元PNG pack、元判断形式。現時点の対照。
- B：v8の意味提示＋条件付き回答、元PNG pack。Aとの差は判断契約一式として評価。
- C：v8の意味提示＋条件付き回答、新画像pack。Bとの差は画像転送形式として評価。

主比較は各試行のclientを新規作成し、同じcold条件・時計・証拠書込で測ります。Aの凍結コードを変更せず、製品外harnessから同条件で呼べない場合は送信前審査へ戻します。3armすべてLuna/同じreasoning、同じ候補・文字証拠・scope・採点とし、JPEG bytesだけは意図した差です。中間結果でpromptや閾値を変更しません。

残る12試行は固定評価用12scopeへCを一回ずつ適用します。一つの長寿命clientを自然に利用し、初回と後続を区分します。この12件の時間はcoldの5秒主判定へ混ぜません。人工的なwarmupはしません。校正用12scopeは未送信で保持します。

既知5scopeについてCの従来条件を維持します。全重点role146/146、結合74/74、行14/14、mark16/16、重点誤結合0、品質成立中央値≤5000ms、品質付き5秒以内8/10以上かつ各scope1/2以上。名前・alias・親・共有を含む完全manual投影と名称だけの手直し量を別記し、不一致を分母から削りません。Aが旧結果と違っても当日の結果を保持し、都合のよい試行を選びません。

評価用12scopeは、一意な10件で全重点・完全manual投影が一致し、意図した保留2件ではoperationsが空であることを要求します。誤ったdraftや範囲外適用0。保持すべきデータの破壊、予算・現在性・所有の違反は残試行を停止します。通常の意味不正解/保留/timeoutも分母に残し、修復せず固定scheduleを完了します。開始時点でschema/codec/endpointが利用不能なら残試行を止め、失敗の一送信を計上します。

Phase31で採否を審査します。合格しても自動的には製品接続・通常データ移行を行いません。製品接続時にはAI案の「案を反映」→「確認して保存」、DRAFT/revision/receipt/undo、手動同値変更の保護、局所録音と手動編集の維持を別途検証します。今回の修正対象はテンプレート解析であり、既存の連続音声UXや矩形検出の変更を同時に行いません。

## 11. 公式仕様の照合

2026-10-12確認。Decisionsは画像/文字に対する有限の選択等を返し、独立質問をまとめ、先行回答に依存する質問は別requestに分ける仕様です。アプリ独自JSONにはResponsesのStructured Outputsを使用します。[Decisions公式ガイド](https://developers.openai.com/api/docs/guides/decisions)

Structured Outputsはroot object、全property必須、additionalProperties=falseを要求し、入れ子のanyOfを扱えます。公開上限は5000 properties、深さ10、enum総数1000、対象文字総数120000です。今回のアプリ上限はそれより狭くします。[Structured Outputs公式ガイド](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)

LunaのResponsesはlowを含む推論強度を扱います。[GPT-6 Lunaモデル仕様](https://developers.openai.com/api/docs/models/gpt-6-luna) 画像入力の一般ガイドはPNG/JPEG/WebPを列挙していますが、本アカウント・当該endpointのv8入力受理をオフライン検査では証明できません。[画像入力ガイド](https://developers.openai.com/api/docs/guides/images-vision)

公式の速度説明を文之助の改善率へ転用せず、最終判断は上記の同時期比較によって行います。
