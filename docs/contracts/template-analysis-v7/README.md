# Template analysis v7：条件付き判断と自由名称の設計

2026-10-13：**Phase34-Eは未完了。資料の候補到達性とT2時間基準が未達のため修正設計へ戻す**。[34-E報告](../../template-ai-phase34e-2026-10-13.md)と[結果JSON](../template-analysis-v8/phase34e-result.json)を現在地とする。
製品外コピーで再生/実送信の分離、HTTP入口の一回許可、固定experiment/追記専用記録、共有予約ID、明示goldラベルを実装。新境界18+21+8+10、pair23+上限6、旧30gold、正例回帰2、旧82応答/24 DTOの歴史再生を確認。ただし全面的な旧335項目の新入口移植、出典/負例意味/画像の独立審査は未完了。
新資料はoracle29/30。V3-09の3行記号フォームが候補不足で、候補生成を変えない34-D境界に達した。T2ローカル中央値は583.22→515.46ms、500ms未達。実応答時間・モデル精度は未検証。候補/正解/分母は合格させるために変更しない。
実API/共有予約0、累計417/496・残79。製品未接続、34-C差戻しを維持。次はAstra/XHigh・API0の修正設計、その後34-E完了と34-F独立審査。35〜38は保留。以下は従来時点の記録。

2026-10-12：**Phase34-D修正設計完了。実装・実APIは未開始、34-Cの差戻しを維持**。[34-D設計](../../template-ai-phase34d-design-2026-10-12.md)と[計画JSON](../template-analysis-v8/phase34d-plan.json)を現在地とする。
R1は再生/実送信の分離とHTTP境界の一回許可、R2は固定experiment ID/記録先/共有予約IDの対応、R3は全資料の出典・派生審査、R4は値の知識不足と意味設定不能の区別で修正する。cohort v3、校正12/評価18、D1〜D12受入条件を定義。候補prototype2/回答v8/GPT-6 Luna/閾値/共通manualは維持。新資料・新動作は未検証。
今回使用/予約0、累計417/496・残79。製品/旧checkout/両DB/通常データ/台帳/原版/旧本文を保持、既存OCR contractを維持。次は34-E=GPT-6.1 Sol/High・API0実装、34-F=Astra/XHigh・API0再審査。指示まで未開始。34-F合格まで35〜38は保留、上限18/24/28の70回・未割当9回を未予約で保持。以下は従来時点の記録。

2026-10-12：**Phase34-C独立審査は差戻し。実比較・製品採用は未承認**。[34-C審査](../../template-ai-phase34c-review-2026-10-12.md)と[結果JSON](../template-analysis-v8/phase34c-result.json)を現在地とする。
API0入口の実HTTP通過、journal root変更による同一trial再実行、校正由来3評価資料、文脈不足gold2件をR1〜R4として指摘。既存335+新pair23+実験41+旧審査5+reader6は再実行通過。33画像の越境0、旧30gold一致、旧30/10/42応答と24 DTO歴史再生、5対象のローカル中央値500ms以下（T2 477.3ms）。これらは新指摘の免責や実応答速度の保証ではない。
今回使用/予約0、累計417/496・残79。製品/旧checkout/両DB/通常データ/台帳/元成果物/旧本文を保持し、既存OCR contractを維持。次工程案34-D=Astra/XHigh・API0修正設計、34-E=GPT-6.1 Sol/High・API0実装、34-F=Astra/XHigh・API0再審査。指示まで未開始。35〜38は保留、上限18/24/28の70回・未割当9回は未予約。以下は従来時点の記録。

2026-10-12：**Phase34-B製品外修正実装・API0検証完了。独立審査待ち、実比較/製品採用は未承認**。[34-B報告](../../template-ai-phase34b-2026-10-12.md)と[結果JSON](../template-analysis-v8/phase34b-result.json)を現在地とする。
R1の記号欄6組を補完して旧30件27/30→30/30。単独欄の固定親候補、文字所属を検査した新30枚＋修正回帰3枚、校正6family×正負2件（全12件Decisions）と評価18件を固定。凍結cohort/選定/trial/runを結合し、入力生成前から永続attemptを消費、重複・再起動・応答混入・予約後/公開前改変を拒否する。
既存境界335、新pair23、新実験境界41、追加5シナリオ、旧rawの旧実装コピー再生が通過。trial照合を含む5対象ローカル中央値は全て500ms以内。実モデル精度・実応答5秒は未検証。独立gold/画像/source family審査と実帳票4family条件は未達、合成結果だけでは採用しない。
今回API/予約0、累計417/496・残79。製品ソース・両DB/通常データ・台帳・原版/過去証拠・旧本文を保持、既存OCR contractを維持。次は34-C=Astra/XHigh・API0。合格まで35〜38は保留、上限18/24/28の70回・未割当9回は未予約。モデル切替後の指示まで次工程を開始しない。以下は従来時点の記録。

2026-10-12：**Phase34-A修正設計完了。実装・実APIは未開始、Phase34の差戻しを維持**。[34-A設計](../../template-ai-phase34a-design-2026-10-12.md)と[計画JSON](../template-analysis-v8/phase34a-plan.json)を現在地とする。
R1〜R4を、欄内記号凡例の有限候補、単独1項目/1階層の固定親候補、文字越境の検査、凍結cohortとtrial/runの結合で修正する設計。候補policyをprototype2へ改訂し、旧receiptを転用しない。回答v8・全claims・物理/現在性/同値手動編集・共通manualは維持する。
校正v2は6元family×正負2件の12件、全件Decisions適格を受入条件とする。旧30件は原版を保持して開発/回帰へ移し、最終評価18件は別の元資料で準備。新規実帳票4family条件は未達、合成比較の成功だけでは製品採用しない。
比較割付は正確なID/split/family/hash/順序に固定し、予約前後・公開前までtrialを照合する。35/36の結果と選定成果物なしに後続計画を発行しない。一trial一attempt、retry/返金0、ローカル5scope中央値500ms以下と実応答5秒の条件を維持。
今回API0、累計417/496・残79。製品コード・実装原版・両DB/台帳・通常データ・過去証拠・旧本文を保持、既存OCR contractを維持。次は34-B=GPT-6.1 Sol/High・API0の製品外修正実装、続いて34-C=Astra/XHigh・API0の独立再審査。合格まで35〜38は保留、上限18/24/28の70回・未割当9回は未予約。モデル切替後の指示まで次工程を開始しない。以下は従来時点の記録。

2026-10-12：**Phase34独立審査完了。Phase33は差戻し、実API比較へ進めない**。[Phase34審査](../../template-ai-phase34-review-2026-10-12.md)と[審査JSON](../template-analysis-v8/phase34-review.json)を現在地とする。
R1：V08/V09/V10の6記号pairが候補不足。R2：K02/K04/K06の6ラベルが値欄へ26px越境。R3：比較割付が同一scopeの重複を拒否しない。R4：校正に記号・親子対応・親競合・範囲端文脈欠落・画像/文字矛盾の被覆が不足。原版・goldは保持。
既存境界335項目＋独立5シナリオ通過、保存42試行=41受理/1同一拒否。独立gold内部照合30/30、期待DTOのmanual再生30/30、有限候補からの到達27/30。原画像30枚を確認したがデータ受入は不合格。T2ローカル中央値400.38ms、全5scopeで500ms以下。実通信5秒・モデル精度の改善証拠ではない。
製品ソース・審査対象runtime・両DB全表/台帳・通常データ・過去証拠・旧本文を保持、既存OCR contractを維持。今回API0、累計417/496・残79。製品不採用・未接続を維持。
次工程案は34-A=Astra/XHighの修正設計、34-B=GPT-6.1 Sol/Highの製品外修正、34-C=Astra/XHighの独立再審査（すべてAPI0）。合格まで35〜38は保留、計画上限70回・未割当9回は未予約。次工程はモデル切替後の指示まで開始しない。以下は従来時点の記録。

2026-10-12：**Phase33の製品外実装・境界検証を実施。データ受入未達のため実API比較は停止**。[Phase33報告](../../template-ai-phase33-2026-10-12.md)と[結果JSON](../template-analysis-v8/phase33-result.json)を現在地とする。
候補の設定結果を示す説明、固定されたDecisions採用policy、設定/receiptの所有検査、比較割付を実装。候補集合・v8回答schema・全claims・共通manual・物理/現在性/同値手動編集を維持。製品コードと通常UIへの接続はない。
境界335項目通過、旧42試行=41受理/1同一拒否を再現。全5scopeのローカル中央値500ms以下、T2=403.17ms。HTTP/実台帳/証拠保存を含まず、実応答5秒やモデル精度の改善証拠ではない。
新規合成30範囲の期待投影は27/30。V08/V09/V10の単独記号pairを既存有限候補で表現できず、期待値は保持。校正の単独記号被覆と新規実帳票4familyも未達。独立gold審査前、製品不採用を維持。
API今回0、累計417/496・残79。製品ソース・両DB全表/台帳・通常データ・過去証拠・旧本文を保持し、既存OCR contractを維持。次は34=Astra/XHigh・API0で実装/期待値/候補被覆不足の審査。データ受入未達を解消せず35〜37の実送信へ進めない。次工程はモデル切替後の指示まで開始しない。以下は従来時点の記録。

2026-10-12：**Phase32修正設計完了。Phase31の製品不採用を維持し、runtime修正・製品接続は未開始**。[Phase32設計](../../template-ai-phase32-design-2026-10-12.md)と[計画JSON](../template-analysis-v8/phase32-plan.json)を現在地とする。
候補を選んだ設定結果とname所属を示す入力表現を設計。回答v8/schema・候補集合・全claims・共通compiler/manual・物理/現在性/同値手動編集を維持する。指示だけ→候補説明だけを変える18回の比較後、独立したcontext質問とchoiceの24回校正を行う計画。一律閾値解除・保留の強制補修はしない。
既知5scopeとE12の6説明例をAPI0で照合。製品精度・応答速度の改善証拠ではない。新規校正12/評価18はfamilyで分離し、既知E01〜E12・旧校正を最終評価へ流用しない。次は33=GPT-6.1 Sol/High・API0の製品外実装、34=Astra/XHighの審査、35=Sol/High最大18、36=Astra/XHigh最大24、37=Sol/High最大28、38=Astra/XHigh採否。各段階の不合格で後続送信を止める。
今回API0、累計417/496・残79。将来比較の計画上限70・未割当9、今は未予約。製品ソース・DB・通常データ・過去証拠を保持し、既存OCR contractを維持。次工程はモデル切替後の指示まで開始しない。以下は従来時点の記録。

2026-10-12：**Phase31採否審査完了。30-Dの測定記録と不合格判定を受入、v8の製品不採用を確定**。[Phase31審査](../../template-ai-phase31-review-2026-10-12.md)と[審査JSON](../template-analysis-v8/phase31-review.json)を現在地とする。
791封印成果物・953実行固定hash、41要求/原応答/台帳、82画像を照合。保存42試行=41受理/1同一拒否、別採点と全計測を再現。A品質4/10・5秒以内1/10、B/C品質0/10、評価完全一致1/10・正しい保留2/2を維持。
v8 Responses23件中22件は生の構造回答で保留。B/C T3は第一候補28/28正解でも採用8/28。閾値を外すAPI0診断で5件は正答へ復元するが、保留対象E12にも誤った見出し案が生じる。E09正答.04/.02に対しE12誤答.12/.03（confidence/確率差）で、一律の閾値引下げは採用しない。30-DのT3説明はrow不足ではなくrole/binding/mark不足と訂正、旧証拠は保持。
API今回0、累計417/496・残79。製品ソース・両DB全表/台帳・通常データ・過去証拠を保持し、既存OCR contractを維持。製品接続なし。次はPhase32=Astra/XHigh・API0で判断用入力表現、保留と校正、比較の切り分け、独立評価集合の修正設計を推奨。次の指示まで未開始。以下は従来時点の記録。

2026-10-12：**30-D固定実API比較を完了、修正版v8は採用基準不合格**。現在地は[30-D報告](../../template-ai-phase30d-2026-10-12.md)と[結果JSON](../template-analysis-v8/phase30d-result.json)。既知5scope×2×A/B/C＋評価12の42試行を完了、実送信41（Responses31/Decisions10）、E11は送信前保留0、retry/warmup/補修0。
Aは品質合格4/10・5秒以内1/10・合格例中央値6228.87ms。B/Cは品質合格0/10、各held8/partial2で完全manual投影0/10。CのT2 ready中央値5520.07msも正答時間ではない。評価は完全一致1/10・正しい保留2/2、残る正答対象9件は保留。元画像Bも失敗しており圧縮単独の原因とは断定しない。
953実行固定hash、41要求/原応答/台帳の一致、保存再生42件=41受理/1同一拒否を確認。API今回41、累計417/496・残79。製品ソース・業務データ・過去証拠を保持、通常DBの変更はAPI台帳のみ。既存OCR contractを維持。製品接続なし。
次はPhase31=Astra/XHigh・API0の採否審査。現在のチャットモデル自体は未確認。次の指示まで開始せず、固定結果を追加送信で補修しない。以下は従来時点の記録。

2026-10-12：**30-C独立再審査を通過**。現在地は[30-C審査](../../template-ai-phase30c-review-2026-10-12.md)と[審査JSON](../template-analysis-v8/phase30c-review.json)。製品外30-B実装を固定比較へ進める版として受入。差戻し指摘なし。製品採用・実モデル精度・実通信5秒の合格ではない。
開始時927成果物・401実装を照合。コピーで既存201＋修正51境界、保存40応答の35受理/5同一拒否、合成24scopeと既知5scopeのmanual投影を再現。審査側9条件で最終検査後の中止/同値編集を送信0、8件再入を1予約/1送信へ限定。予約応答喪失、要求bytes変更、書込失敗、proof改変も拒否。
各3反復のT2再測定=395.21ms、全5scope中央値500ms以下。保存された変更前後計測を再計算し一致。5scopeの元crop・転送画像・完全要求本文も変更前後一致。oracle/mockであり実通信時間の証拠ではない。
今回API0、376/496・残120。製品ソース・通常データ・原本を保持し、既存OCR contractを維持。次は30-D=GPT-6.1 Sol/High、GPT-6 Luna最大42送信・Responses low、retry/warmup/補修0。次の指示まで実API比較を開始しない。以下は従来時点の記録。

2026-10-12：**30-B修正実装のローカル受入を通過、独立再審査待ち**。現在地は[30-B報告](../../template-ai-phase30b-2026-10-12.md)と[結果JSON](../template-analysis-v8/phase30b-result.json)。実装は製品外`D:/llm-ocr-archive/template-ai/20261012-phase30b`、製品接続・通常移行なし。
R1〜R3を一run一所有thread、原子的な開始/dispatch権、待機/予約後の内容・現在性検査で修正。中止待機者はownerのguardと接続枠を解放しない。対象の共有StateSourceは同値編集でもepochを更新し、後続指示で旧応答を拒否する。画像の同期区間内証拠と元PNG decode共有を追加し、次区間は内容を再検証。
変更前/後各3反復でT2=639.69→389.46ms、全5scope中央値500ms以下。既存201＋追加51境界、保存40応答の35受理/5同一拒否と旧採点、合成24scope期待投影を確認。全claims/schema/実装内容/共通compiler/manualと元cropを維持。401実装を固定。oracle・mockであり実モデル/通信の成功ではない。
今回API0、376/496・残120。既存OCR contractを維持。次は30-C=Astra/XHigh・API0の独立再審査。30-Dの有料比較は再審査受入と次の指示まで開始しない。以下は従来時点の記録。

2026-10-12：Phase30の開始準備として**30-A修正設計を完了**。現在地は[30-A設計](../../template-ai-phase30-readiness-design-2026-10-12.md)と[計画JSON](../template-analysis-v8/phase30-readiness-plan.json)。Phase29の差戻しと実API比較の停止は維持。修正実装は未開始。
R1〜R3は一run一実行所有者、原子的な開始権・一回dispatch権、初期化/予約後の再検査で修正する設計。T2は同期区間内の不変bytes証拠と元画像の一回decodeにより重複計算を減らす。現在性・全実装内容・schema・全claims・共通compiler/manualの検査と500ms条件を維持する。
工程30-A=Astra/XHighの設計、30-B=GPT-6.1 Sol/Highの製品外実装、30-C=Astra/XHighの独立再審査、30-D=当初のSol/High・GPT-6 Luna最大42送信の比較、31=採否審査。各区切りで次の指示を待つ。現在のチャットモデルは未確認。
今回API0、376/496・残120。Phase29の封印476成果物を開始時照合し、未修正Phase28 T2の一回profileで完全manual投影を確認。profile時間は性能受入値ではない。製品ソース・通常データ・旧証拠を保持し、既存OCR contractを維持。以下は従来時点の記録。

2026-10-12：Phase29独立審査は**差戻し**。現在地は[Phase29審査](../../template-ai-phase29-review-2026-10-12.md)と[審査JSON](../template-analysis-v8/phase29-review.json)。Phase30の実API比較へ進めない。
新規3件をネットワーク禁止で再現：R1/P1 同一runの同時実行で2予約・2送信、R2/P2 client初期化中のキャンセル後に未送信要求が送信される、R3/P2 台帳予約中のepoch変更後も古い要求を送信する。R3は応答後の現在性検査でdraft返却を拒否。通常DBの誤適用を再現したものではない。T2は保存中央値625.34msを確認、審査再測定634.55msで500ms未達を継続。
Phase28の634成果物・実装399ファイルを照合。コピー上で既存201境界、保存40応答の旧採点、合成24scopeの期待DTO投影を再現。修正実装・製品接続なし、既存OCR contractを維持。API0、376/496・残120。次は3件＋T2の修正設計・実装・独立再審査。次の指示まで開始しない。以下は従来時点の記録。

2026-10-12：Phase28の製品外修正実装とオフライン検証を実施。**受入不合格：T2ローカル中央値625.34msが500ms上限を超過。Phase30の実API比較へ進めない。** 現在地は[Phase28報告](../../template-ai-phase28-2026-10-12.md)と[結果JSON](../template-analysis-v8/phase28-result.json)。
意味提示/構造別branch schema/所有alias展開/元crop保持の転送pack/最大一送信の共有HTTP・期限・現在性検査を実装。旧runtime400ファイルと候補/全claims/共通compile/manualを保持。既存83＋48件、新v8 70件、旧保存40応答の採点再現、既知5scopeのoracle完全manual投影を確認。校正12＋評価12の合成familyと期待値を固定。実モデル品質・endpoint受理・実通信時間は未確認。
API0、376/496・残120。製品接続・通常データ移行なし。既存OCR contractを維持。次はPhase29=Astra / XHigh・API0の独立審査。次工程の指示まで開始しない。以下は従来時点の記録。

2026-10-12：AIテンプレート作成Phase27の修正設計を完了。現在地は[Phase27設計](../../template-ai-phase27-design-2026-10-12.md)と[v8契約](../template-analysis-v8/README.md)。製品外の設計であり、runtime実装・実API・製品接続は未実施。
独立した有限判断はDecisions一回、構造・親・列用途・名称が連動する判断は条件付きResponses一回へ固定。意味説明と内部所有IDを分離し、構造ごとのfactor/name所属を生成schemaへ反映。既存候補・全claims・共通compiler/manual・現在性・同値手動編集の保護を維持。必須名称のplaceholder補完、ID推測修復、confidence閾値の引下げは行わない。
詳細cropの元PNGを保持し、全体画像だけJPEG quality85へ変換して小さい場合に採用する転送packを設計。コピー上のT2要求は画像変更だけで4.45MB→1.23MB（72.43%減）。これは通信速度・意味精度の改善証拠ではない。5scopeのbranch schema例と別構造choice拒否をオフライン確認。実endpoint受理とruntime検証は未了。
次はPhase28=GPT-6.1 Sol / High・API0の製品外実装、29=Astra / XHigh・API0の審査、30=Sol / High・Luna最大42送信の固定比較、31=Astra / XHighの採否。各工程はモデル切替後の指示まで開始しない。既存5秒/品質条件とPhase25不合格は保持。
今回API0、376/496・残120。製品コード・両DB各37表/台帳376行・schema・設定・旧checkout・業務863ファイル/781 artifact・旧証拠を保持。既存OCR contractを維持、通常未移行/無効を継続。以下は従来時点の記録。

2026-10-12：AIテンプレート作成Phase26の独立審査を完了。Phase25の測定記録と不合格判定を受入、製品採用は不合格のまま。
[Phase26審査](../../template-ai-phase26-review-2026-10-12.md)と[審査JSON](phase26-review.json)を現在地とする。Phase25の116成果物を開始時照合し、保存10応答をネットワーク禁止で再生。9受理/1同一拒否、別採点で品質成立4/10・5秒以内0/10・重点誤結合4、完全manual投影・物理/scope・既存全ID no-opを再確認。H1は必須名称nullだけでなくrecords誤選択があり、名称だけ補った診断でも誤結合3。H2も条件付きIDと名称を補ってなお誤結合4。補修での救済を採用しない。T3の保留10判断は生第一候補9正解/1誤りで、閾値校正は未了。
T2中央値6139.32msのうちHTTP5031.79ms、初期化223.83ms。要求4.45MBの99.295%は画像base64で、転送とprovider処理の時間は未分離。ローカル小修正だけで5秒達成としない。次はPhase27=Astra / XHigh・API0の修正設計を提案。モデル向け意味判断・条件付き回答・名称方針・入力packを設計し、共通compiler/manualと所有検査を保持する。設計/実装/追加比較/製品接続は次工程の指示まで未開始。
今回API0、376/496・残120。製品コード・両DB全37表/台帳376行・schema・設定・旧checkout・通常863ファイル/781 artifact・旧証拠を保持。既存OCR contractを維持、通常未移行/無効を継続。以下は従来時点の記録。

2026-10-12：AIテンプレート作成Phase25の固定実API比較を完了。品質・5秒条件は不合格、製品へ導入しない。
[Phase25報告](../../template-ai-phase25-2026-10-12.md)と[結果JSON](phase25-result.json)を現在地とする。審査済みPhase24修正revision2を凍結し、GPT-6 Lunaで固定5範囲×2反復、T3=Decisions・他4範囲=Responses lowの10送信を実施。warmup/retry/repair/追加診断API0。全重点正解・完全manual意味投影は各4/10、品質付き5秒以内0/10、品質成立中央値6139.32ms。重点分類89/146・結合54/74・行6/14・記号3/16、重点誤結合4。T3は部分保留、H1は必須名称nullで保留、H2は誤選択1件と条件外choice拒否1件。HTTP200/構造検査の通過を意味の正解と扱わない。
原応答再生9受理/1同一拒否、別採点9/9、物理/共通manual/既存全ID no-op、実行hashと台帳を確認。製品ソース・業務データ・両schema・設定・旧checkout・旧証拠を保持し、通常DBの変更はdeployの今回ledger10行だけ。既存366行を保持、既存OCR contractを維持、通常未移行/無効を継続。今回API10、376/496・残120。次はPhase26=Astra / XHigh・API0の独立審査。次工程の指示まで審査・再設計・追加比較・製品接続を開始しない。以下は従来時点の記録。

現在地は[T2修正審査](../../template-ai-t2-repair-review-2026-10-12.md)と[審査JSON](t2-repair-review.json)。revision2を製品外試作として受入とした。固定30計測から全5範囲の500ms条件を確認し、同内容の隔離コピーで83＋48境界・旧30応答・5/5完全投影を再現。待機中の同値手動編集/module reloadの追加2条件も通過。次はPhase25（GPT-6.1 Sol / High、GPT-6 Luna、最大20送信）で、次工程の指示まで未開始・未予約。実モデル品質/速度と製品採用は未判定。過去の不合格・修正設計・実装結果は上書きせず保持する。以下は従来時点の記録。

現在地は[T2修正実装の結果](../../template-ai-t2-repair-2026-10-12.md)と[修正結果JSON](t2-repair-result.json)。製品外で検証済み入力共有と同期区間の実装照合を実装し、全5scopeの500ms/完全意味条件と旧83＋新48境界、変更後runtimeで旧30保存応答を通過した。T2は今回の旧751.75ms→新472.96ms。次はAstra / XHigh・API0の修正審査で、有料Phase25はまだ開始しない。元Phase24結果と固定した修正設計はそのまま保持する。以下は従来時点の記録。

追加の[T2修正設計](../../template-ai-t2-revision-design-2026-10-11.md)と[修正計画](t2-revision-plan.json)を次工程の基準とする。内部の検証済み入力共有と同期区間の実装照合を設計した段階で、修正実装は未着手。Phase24のT2時間不合格・Phase25停止を維持する。新規geometry1回、APIなし同期区間の実装全照合2回が設計値。現在性・復元snapshot検査・全claims・共通compile/manualと500ms条件を維持し、API待機をまたいだ無検証再利用は行わない。以下のPhase24/23記述は当時の記録。

現在地は[Phase24結果](../../template-ai-phase24-2026-10-11.md)と[試作実装境界](phase24-implementation.md)。製品外の新生成器・選択adapter・mock runnerは実装済みだが、T2のローカル時間716.81msにより総合gateは不合格。完全manual投影5/5と83境界はoracle/offlineの確認であり、実モデル・製品接続の成功ではない。Phase25有料測定は開始しない。現在の機械可読結果は[phase24-result.json](phase24-result.json)。以下はPhase23で固定した設計本文であり、当時の「未実装」は履歴として保持する。

2026-10-11。`template-analysis.factor-plan.v7.design1`。**設計のみ。runtime・実API・製品接続は未実装。** [Phase23審査](../../template-ai-phase23-review-2026-10-11.md)を参照する。Phase22の[v6試作](../template-analysis-v6/README.md)と4/5不合格は保持する。

## 1. 決定した変更

v6は表の用途・列の読取・行識別・親・名称を含む完成案を全組合せ列挙し、案ごとにcompileした。v7は**構造と、その構造の下で選ぶ判断を分けて保持し、実際に選ばれた組合せだけを共通compilerへ渡す**。

構造候補を作るのはserverであり、モデルが自由なセル集合・座標・pathを生成する方式へ戻さない。列判断も発行済みの意味差分IDから選ぶ。生成が必要な名称だけは専用のname slotへ入れる。名称から既存のphysical identity、relation ID、mapping、targetPathを再解決しない。

この契約は次の製品外試作の設計正本。通常製品、過去のv5/v6応答、旧artifactをv7へ読み替えない。新入口・version・request digestを分離し、旧証拠は再生専用とする。DB migrationはこの工程に含まない。

## 2. サーバー側の計画表現

一つのfocused requestを一つの`AnalysisPlan`へ固定する。公開HTTP仕様ではない。

| 要素 | 必須内容 |
| --- | --- |
| binding | owner/resource/page/revision、snapshot・catalog・scope・画像/pack・保護role/facetの各digest、interaction epoch、plan/prompt/schema/compilerの版 |
| component | 全write refsの重複しない所有範囲、読取依存、必要な意味facet、状態と未解決理由 |
| structure choice | 既存grid/ref、行区間、header位置、form/records等の解釈、formのlabel/value列、position又は一つの適格printed-key列、空行保持、固定claims |
| conditional factor | 先行structure ID、対象列又はparent等の固定slot、有限の意味差分候補、適用条件、全claims、証拠ref |
| name slot | server所有の対象slot ID、必要となるstructure ID、`preserve` / `literal` / `generated`、印字evidence refs、長さ等の制限、既存保護状態 |
| coverage | requested refs/facets、解決済み、unknown、unsupported、競合、保留理由。未選択をignoredにしない |

`structure`の選択で、その配下の質問集合を確定する。各候補IDは内容と親structureへ束縛する。順序変更や別構造の同じ列番号を同じ回答として扱わない。

列の用途と読取方法は一つの小さな質問にする。例：`value/text`、`value/symbol(m1)`、`row_label`、`context`、保留2択。用途を選んでから読取を聞く3段目を必要としない。printed keyの選択はstructureに含め、列質問から二重に選ばせない。全行で非空・一意であることは選択肢を作る前の必要条件であり、数字らしさだけで採用しない。

parent、読取、名称source等のslotは、その答えが別の質問の選択肢や意味を変えない場合だけ同一requestへまとめる。共有する画像を見て判断することと、回答順に依存することを区別する。複数の回答が互いに独立に生成されても、合成時の整合検査は必須。

表とformの競合、複数の親候補、既存保護facetは全候補に保持する。数を減らすために「印字があるからcontext」「左列だから行ラベル」などと意味を先に確定しない。生成器はcase ID・gold・旧応答・採点・帳票固有辞書にアクセスしない。

## 3. 候補数と計算量の上限

以下はアプリケーションの試作上限であり、OpenAIのAPI上限ではない。

- 一つの質問は`none_of_these` / `needs_context`を含め最大32択。候補順で切り捨てない。
- 一つのrequestで有効な質問は最大64。全structure配下を含むplan定義は最大512質問・8192選択肢。
- 一つのfocused analysisは最大2段・2送信。処理途中の自動分割、3段目、自動再試行、方式を切り替える再送は設けない。
- 行列候補・schema・既存保存容量の上限も維持する。形状候補そのものが32を超える場合は`PLAN_BOUND_EXCEEDED`として対象componentを保留する。Responsesにも無制限の構造生成を許さない。
- runtimeは列の全直積を生成・compileしない。計画構築は候補の和、選択後compileは選択された案の大きさに比例する設計とする。境界検査で直積列挙の混入を確認する。

上限を超えた事実と全対象をcoverageへ残す。解析中に保留を成功へ再分類しない。利用者は通常の範囲指定や手動編集で続けられる。

## 4. API経路は送信前に固定する

両経路のAPIモデルは **`gpt-6-luna`**。Responsesの推論強度は`low`に固定し、DecisionsへResponses用のreasoning設定を流用しない。開発用モデルと混同しない。

| 条件 | 経路 | 送信数 |
| --- | --- | ---: |
| 発行候補で意味と名前が完結する、先行回答不要の単体欄等 | Decisionsで必要な独立質問をまとめる | 1 |
| 自由名称不要で、構造選択に続く条件付き質問が必要 | Decisionsで構造選択→選択結果に束縛した列/parent等の質問 | 最大2 |
| 候補に自由名称が必要な構造を含む、又は2段以内の独立質問へ表せないが下記の有限planに収まる | Responsesへ直接、構造ID・factor IDと必要な名称を一つのstrict出力で生成 | 1 |
| plan自体が上限外／対応する構造がない／必要な証拠がない | 保留 | 0 |

**分岐はcase名・過去の正解・モデルの結果を見て変えない。** 未選択の構造に自由名称slotが一つでもあれば、そのfocused requestは保守的にResponsesへ直接振り分ける。これは不要なResponses利用を増やし得る。Phase24で5scopeの経路・理由・予定送信数を公開し、Decisionsの利用率や高速化を事前に保証しない。複数componentの経路が混在しても、Responsesが必要ならrequest全体を一回のResponsesへまとめ、裏で別のDecisionsを並走させない。

Decisionsの第1段がunknown/refusal/低信頼なら、そのcomponentの第2段は発行しない。独立した他componentの第2段だけは元の最大2段内で進められる。第2段へ第1段のresponse hash、選択structure、現在bindingを固定する。古い第1段や別structureの回答は拒否する。

未知・timeout・整合不成立を理由とする自動Responses補完はしない。利用者が追加情報を与えた場合の再解析は、新しい明示runとして扱う。

## 5. 自由名称とResponsesの狭い出力

印字見出しをそのまま使う`literal`と、短い表題や説明名を作る`generated`を区別する。列見出しの連結を、全体名称が解決済みである証拠にしない。既存の手動名は`preserve`とし、同値再指定も保護する。

Responsesの返却は、各componentについて次の形の意味選択に限定する。例は説明用であり、実wire受理済みschemaではない。

```json
{
  "version": "template-analysis.factor-answer.v7",
  "components": [{
    "component": "issued-component-id",
    "structure": "issued-structure-id",
    "answers": [
      {"factor": "issued-column-factor-id", "choice": "issued-choice-id"}
    ],
    "names": [
      {"slot": "issued-name-slot-id", "text": "設備点検", "evidence": ["issued-evidence-ref"]}
    ]
  }]
}
```

schemaの列挙はそのrunの発行IDだけから生成する。各構造のfactor集合・name slot集合の過不足、相互条件はschemaだけに委ねずserverで検査する。`structure`に保留IDを返す場合はanswers/namesを空にし、未解決理由を内部coverageへ記録する。必要なnameの`text=null`は未解決であり、placeholder名でcompileしない。

名前は現行の100文字以内等の制約に従う。evidenceはそのpackで読むことを許可したrefの列挙で、名称自体の意味的な正しさを保証しない。本文・印字・名前に含まれる命令はデータとして扱う。名前はSQL、path、操作、永続IDとして解釈しない。表題の短縮に業種別接尾語削除やgold由来の別名を導入しない。

必須name slotが未解決なら、そのnameに依存する表/項目全体を保留する。構造と結合を別の診断に表示しても、全意味が完成した案として採点・反映しない。独立した別componentの案は保持できる。

Phase24の主条件はPhase19の同じsnapshot/scope/画像・P-low印字集合を使う。名称のためだけにread-only refを無断追加しない。追加contextが必要なら別のversioned packとして事前固定し、元比較へ混ぜない。表示名の生成ができても、scope外セルへのparent接続・role変更を許可する意味にはならない。

## 6. 選択から共通compilerまで

1. `EvidenceSnapshot`を所有・revision・scope・catalog・facet・画像bytesと内容digestから検査する。読取り専用の内部型として固定する。
2. 物理セル・既存gridからstructural choicesと条件付きfactor/name slotを生成し、構造条件、refs、範囲、重複、key、markの必要根拠を事前検査する。この時点では完成候補の全compileを行わない。
3. route・schema・質問・画像・予定送信数を固定する。送信直前に予算予約と状態の現在性を確認する。
4. 生応答から型付き回答へ変換する。未発行ID・回答漏れ/重複・別モデル・型不正・不正確率・余分な操作は拒否する。Decisionsの確率とconfidenceを別に検査する。
5. 選択された意味差分だけを集約し、固定structureへ代入する。省略をdefaultへ補わず、保留を明示する。
6. 全claimsを保持した共通競合検査→v5 DTOへの明示変換→共通resolver/compiler→manual投影を一回通す。名前・alias・共有・親・mark・表・空行を保持する。
7. `draft` / `partial_draft` / `held`とcoverageを返す。ここでは業務保存をしない。

確率を掛け合わせて未選択の組合せを採る、整合する次点へ選び直す、先勝ちで競合を消す方法は禁止する。確率分布をjoint probabilityとみなさない。

形状不正や未発行参照などのプロトコル違反は応答全体を拒否する。意味上の競合では関係する全componentと依存先を保留し、保留した案のclaimsも競合検査へ残す。受理済みの独立componentを保持できるのは、当該保留範囲を所有/参照していないことを証明できる場合だけ。

confidence 0.90・次点差0.15はv6の未校正値として履歴保存する。v7のoffline例には明示した試作値を使えるが、実測前の安全保証にしない。第1段と第2段それぞれの低信頼・誤選択・保留率を測り、同じ評価データを見て閾値を調整して合格へ変えない。

## 7. ローカル待ち時間と保存境界

Phase22 T3の13具体案では`check_pack/build_pack`が40回、`compile_v5`が13回呼ばれた。パック検査が画像のcrop/PNG作成を繰り返す構造である。v7は同一run内で検証済みの不変bytes・metadataを再利用し、選択前の全案compileを廃止する。

変更のないことを時刻/サイズ/任意のbooleanだけで判定しない。検証済み型を外部JSONから復元せず、専用factory以外で生成しない。owner/page/revision、手動の同値再指定、画像/scope/catalog、prompt/schema・compiler依存の実装fingerprintが変わったら失効する。第2段・案反映・保存直前では現在状態を再確認し、古い不変objectを現在のDB状態の証拠にしない。

この最適化は製品OCRのcropや画像処理へ拡張しない。AIテンプレート解析に渡す画像パックの生成と検証だけが対象。

製品接続時は既存の`manual-authoring → MutationCore → TemplateAuthoringService`を使い、「案を反映」→「確認して保存」、DRAFT、revision、receipt、undo、同値手動意図を維持する。providerは保存を行わない。physical identity、既存mapping/path、受付snapshot、OCR結果・確定・出力は変更しない。音声の値補正・局所録音・表任意選択も本フェーズでは変更しない。

## 8. 次の実装で先に固定する合否

Phase24はAPI0回の製品外試作。Phase23の分解診断からv7生成器が成立したとは扱わない。

- gold非参照の新plan生成から、固定5scopeすべてで正解選択＋必要名称を評価側から供給した場合に、**全重点と従来の完全なmanual意味投影を5/5で再現**する。名前を比較から外さない。
- H2の52案を列挙してからfactorへ分ける実装は不合格。geometry/evidenceから直接factorを作る。保持した旧案の再構成52/52は設計の証拠にとどめる。
- 未発行/別構造factor、古い第1段、段間のrevision・同値facet変更、全claims競合、空/重複key、mark欠落、unknown、名前未解決、保留範囲への別案侵入を短い境界検証で拒否する。
- 既存graphの全ID/path込みno-op、physical identity、完全manual投影、旧保存応答の再生を確認する。旧v5/v6 source・応答・採点は変更しない。
- H2の列順入替/追加context列、同名見出し、複数凡例、親競合、上限超過などを別の変形例で検査する。これを未見実帳票の精度と混同しない。
- 固定環境・キャッシュなし各3回で、plan/pack構築＋選択後検査/compileのローカル合計を測る。**各scopeの中央値500ms以下**を次の試作目標として事前固定する。API時間を含まない目標であり、未達なら有料比較を開始しない。
- mock transportで実wire形式・画像data URL・raw projection・予算予約・中断・最大2送信を確認し、送信前manifestへroute/全入力/実装/gold/閾値/測定時計を固定する。mock成功はendpoint受理の証明ではない。

Phase25はPhase24合格後、固定5scope×2反復、**最大20送信**の実測を提案する。各trialは事前routeの1又は最大2送信で終わり、warmup/retry/修復/追加診断は0。第1段で保留したtrialも失敗を含め分母へ残す。予算は送信ごとに共有台帳へ予約し、失敗でも返却しない。いま予約・送信はしない。

採点は従来の全重点、誤結合、行・mark、完全manual投影を保持する。準備開始から完成した検証済み案までを計時し、段1/段2/Responses、pack、検査、compile、全送信数、未解決と拒否を個別記録する。最初の構造回答を完成時刻にしない。全重点が成立して5秒以内のtrialを別集計する。[Phase18の局所条件](../../template-ai-phase18-design-2026-10-11.md)を継承し、**全重点正解・重点誤結合0、品質成立した案の中央値5秒以下、5秒以内8/10以上かつ各scope最低1/2**を要求する。名前・alias・親・共有等を含む完全manual投影の一致率も別記し、名称だけの不一致を構造誤結合と混同しない。Phase24のoracle成立性5/5は、実モデルが参照どおりの自由名称を生成する保証ではない。完全投影が一致しない案を完全再現と呼ばず、名称候補の実用性と手直し量はPhase26の判断へ残す。

新しい測定とPhase20の保存時間は同時期の対照実験ではない。相対速度の改善率を確証として出さず、未見帳票・利用者の手直し時間・実UI/保存は別受入とする。失敗したら26の審査まで停止し、自動で次の再設計を始めない。
