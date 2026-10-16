# 修正6-B：所有範囲付き回答schemaの実装

2026-10-14。GPT-6.1 Sol / High、turn `01a13965-e879-7a50-bded-640524f7f46c`。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。

**[修正設計](template-ai-region-scope-repair-design-2026-10-14.md)を実装し、保存応答と隔離ASGIで検証しました。** 実APIは0回です。Lunaの意味精度・実画面5秒の合格は未証明です。次は6-CのAstra / XHighによる独立工程審査です。

## 原因と変更境界

[V4の実応答](template-ai-region-v4-live-2026-10-14.md)は読取専用34セルへのannotationsと所有10入力の全件保留を返しました。従前の回答schemaは参照の書式だけを制限し、所有違反は回答後に拒否していました。

変更は次の3ファイルです。

| ファイル | 変更 |
| --- | --- |
| `backend/app/template_analysis/region_schema.py` | 新設。RegionAnswerのschemaから、意味の変更先・既存geometry変更先・根拠/関係先・新規参照を分けて限定する純粋関数 |
| `backend/app/template_analysis/region_pack.py` | pack v3。指定領域の完成を先に指示し、周辺は参照専用、annotations/unresolvedは所有対象だけと明示 |
| `backend/app/template_analysis/visual_provider.py` | region経路に生成schemaと応答時の同schema検証を接続。実行contract v9、schema policy/rules digest、要求ごとのschema/scope digestを記録 |

意味上のRegionAnswer、region計画、geometry/compiler、共通保存、通常OCR、frontendは変更していません。非regionのgrid要求1件は従前と完全一致です。意図した変更はregionの生成schema・指示と旧案の失効です。通常DBを移行しません。

## 実装の契約

意味の変更先はwriteRefs又は新規n参照、既存geometryの変更先はgeometryEditable∩writeRefsだけです。根拠、label、既存親、重複除去のsurvivorsは読取専用参照も扱えます。n参照の宣言・幾何・所有・意味検査は既存compilerに残します。

参照位置を21slotとして分類し、元モデルの定義集合と参照位置が変われば送信前に拒否します。未知slotへ無制約stringを使うfallbackはありません。対象集合は共有$defsへまとめ、空geometry集合では既存枠の4変更分岐を除外してaddを保持します。検出0セルでも追加又はbounds付き保留を表現できます。

property数・深さ・schema文字集合・enum総数/大きなenumの文字数を送信前に検査します。上限超過時は対象を切り捨てず、予約前に `REGION_SCHEMA_LIMIT` で停止します。開発23regionの最大値は73properties・深さ9・文字集合3,803・enum793でした。将来の大きな対象が上限を超えた場合にも同じ停止条件が適用されます。

意味のキー順はgroups/tables/readings/headings/annotations/unresolvedで固定しました。追加の合成例は入れていません。実帳票の正解名称・ID・選択肢をpromptへ埋め込んでいません。部分JSONを反映可能な案として公開しません。

static contractはschema policy・規則digest・元回答schema・prompt digestを持ちます。対象依存の最終schema/scope digestと全文要求digestは実要求のmetricsへ記録します。旧v8案は既存のGET/反映/共通保存の失効経路で拒否します。

## 実行した検証

証拠は `D:\llm-ocr-archive\template-ai\20261014-region-scope-fix` に保存しています。

| 検証 | 結果 |
| --- | --- |
| `verify_schema.py` | 開発5ページ・全24要求。23regionでschema生成/検査、1非region完全一致。画像bytes・全セル/候補/geometry等はpolicy以外同一 |
| 同・保存応答 | V4実失敗応答を旧schemaが許し新schemaが拒否。固定10入力対照は許可。既知c14/c15逆転の変更先を拒否 |
| 同・境界 | 18変更/根拠slot、新規3slotの分類を確認。既存geometry4種を表現可能、空集合では4種拒否。検出0のadd/保留、未知slot、上限超過、所有重複を確認 |
| `integration.py` | 実ASGI17条件。解析途中反映、手動同値編集保護、geometry競合、取り消し/再反映、v8取得/反映/保存拒否、明示保存・別接続再読込 |
| `saved_proof.py` | 保存revision2、54物理セルと22入力、22unique mapping/targetPath。製品RegionCropperの22cropが元白紙画像の該当sliceと一致 |
| `provider_cases.py` | 実ASGI4条件。実失敗応答のcatalogだけを新試行へ束縛して拒否、未宣言n拒否、schema上限で予約/送信0、検出0からadd＋意味＋共通保存/別接続再読込 |
| ruff/mypy | 製品3ファイルとも終了0。mypyは `--follow-imports=silent` の対象検査であり全backend検査ではない |

providerを制御した検証では外部通信を禁止しました。検出0の正常対照は、合成画像の枠内印字 `Note` と `in_cell_caption` の根拠を使用しています。白紙に意味を推測して付けた実AI結果ではありません。

初期の追加検証は、Windowsイベントループ作成前の通信遮断、fixtureの未定義property、free_entryに自己参照しかない無効な根拠で失敗しました。検証器・fixtureを修正し、失敗ログと専用DBを保持しています。製品の保存検査を弱めて通していません。

全件unit/E2E、frontend build/Chromeは再実行していません。frontend/計時/画像/共通保存のコードは不変であり、ここでは変更した生成・検査・保存境界を短い検証で確認しています。実Lunaが正しい10入力を生成すること、複合欄混入の実精度、実画面5秒、実入力の位置合わせ/手書き精度は未検証です。

## 予算、保持、審査への引渡し

実API/通常予約は **0回**、共有台帳は **439/924、残485回**。制御HTTPの専用予約は失敗分も含め12回で、通常台帳へ追加していません。通常業務データ・設定、旧checkout、既存差分、V4を含む過去封印、R04の145位置/143機能/手動2領域、最終未使用4帳票の内容未閲覧を保持します。確認結果は `preservation.json`、封印は `completion-1/hashes.json` を正本とします。

旧通常OCRのmodel/prompt/schema、physical ID、mapping/targetPath、crop、revision、保存・出力契約は変更していないため、既存OCR contractを維持しました。region生成contractの意図した変更は上記のとおりです。

次の測定提案は `experiment-allocation-proposal.json` のV5です。新しい21trial、G1の4対象・G2の24送信・G3の60送信、計88・上限96・未割当8転用不可を継承しました。新しい製品hashと24reference要求を固定しましたが、6-C審査前であり、実resource/session/snapshot bindingは未発行です。V4は停止したまま保持し、再送しません。

6-Bを終了し、次は **Astra / XHighへ切替後に6-C** です。審査合格後の6-DはAstra / High、製品APIモデルはGPT-6 Lunaを維持します。新しい別エージェントは使用していません。改善ゴールは未完了です。
