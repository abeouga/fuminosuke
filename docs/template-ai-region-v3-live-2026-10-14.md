# 修正6-D：region-v3診断Aの実測と停止

2026-10-14。Astra / High、turn `01a1392d-04ab-71c1-851a-6c6097ce54e3`。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。

**旧指示・旧metadataの診断Aで、読み取り専用15セルへの変更指定が出たため実験を停止しました。製品側は拒否し、保存はされていません。現行製品の条件Dは未送信です。** 品質改善・実画面5秒は未達成の扱いを維持します。

起点は [6-C審査](template-ai-region-request-review-2026-10-14.md) と、その `experiment-allocation-approved.json` です。審査済み製品3ファイルのhash、封印72件、通常データと予算437/924を照合しました。今回の実API・通常台帳予約は1回、終了時438/924・残486回です。製品コード変更は0です。

## 実行条件

4条件とも新しい専用resource/sessionを作成し、snapshot・plan・領域・本文を送信前に固定しました。画像・schema・モデル・推論量・出力上限を承認済みreferenceと照合し、旧trialのbindingと異なることを確認しました。

診断A/Bでは製品外の測定器から旧packを使用し、C/Dでは現packを使う構成です。形式に合わせた指示は6-Cで固定した本文です。製品ファイルは編集せず、実ASGI・既存provider parser・compiler・保存経路を使用しています。

試験データは `D:\llm-ocr-archive\template-ai\20261014-region-v3-live\runtime` の専用DBです。通常DBは共有API台帳だけを使用します。実行開始/送信を一度だけ記録する排他的marker、本文hash、コード/設定hash、台帳増分を送信前に検査します。再送は行っていません。

実行した試行は `V3-G0-A` のみです。モデルはGPT-6 Luna、reasoning=none、region回答schema、最大8192出力tokens、画像6枚です。対象はR02 p2の領域 `(963,0,278,911)`、入力10セル＋見出し1セルです。比較準備のためのresource作成時間は計時外で、計時は実ASGI解析受付からです。コードhash照合・応答保存の測定器負荷を含む保守的な診断値であり、通常GUIの5秒判定には使用しません。

## 結果

| 項目 | 実測・判定 |
| --- | --- |
| 実API | 1送信、HTTP成功、response.completed |
| モデル出力 | 所有11セルすべてmissing_evidence。入力案0 |
| 不正な変更指定 | readOnlyRefsの15セルをannotations/labelへ指定 |
| schema / catalogDigest | schema妥当、発行digestと一致 |
| 製品compiler | `VISUAL_ANSWER_REFERENCE`で拒否 |
| 公開結果 | failed、案0、未解決11 |
| 対象領域品質 | 入力0/10、未解決10。見出しも未解決 |
| 全ページ分母 | 22入力を維持。領域外12入力は未評価 |
| 実ASGI終了 | 5,831.501 ms |
| provider時間 | 5,103.701 ms |
| 初回output text | 2,777.256 ms。操作可能な案の時刻ではない |
| 最初の正しい操作可能案 | なし |
| 実画面計測・明示保存 | 未実施。案がないため保存しない |
| 専用DBのrevision | 1のまま、初期layoutと一致 |
| tokens | input 11,334、cached 0、output 389 |

15件はc1/c3/c4/c6/c8/c10/c12/c13/c14/c16/c18/c20/c21/c23/c25です。いずれもwriteRefs外・readOnlyRefs内でした。これはモデルが禁止された変更を提案した証拠であり、製品が保護を迂回して保存したという意味ではありません。

## 保存応答による切り分け

ネットワーク禁止下で、送信済み本文・snapshot・実応答を再利用して同じ拒否を再現しました。raw schemaとcatalogDigestは正常で、旧応答の取り違えではありません。

診断用コピーから15件のannotationsだけを除くと、案0・未解決11になりました。読み取り専用指定が拒否の原因ですが、その指定を消しても10入力は保留のままです。このコピーは原因確認だけに使用し、製品parserを変更したり、実測結果を成功へ置換したりしていません。

同じsnapshotに対する既知の正解対照は、10入力を持つ反映可能な1領域を生成しました。現在のschema/compilerで正解を表現できる確認であり、実Lunaの精度ではありません。通常データへの保存は行っていません。

根拠は同archiveの `diagnosis.json`、`V3-G0-A/parsed-raw-answer.json`、`assessment.json`、`timing.json`、`transport-metrics.json`、`request.json`、`completed-response.json`、`raw-response.sse` です。`diagnose.py`は終了0で、追加実APIは0回です。

## 停止と残課題

承認済み停止条件は、診断でも保護違反を観測した時点で未送信分を停止する規則です。そのためB/C/D、G1/G2/G3は未送信です。Aは不合格、残りは未評価として扱います。現行Dの精度・速度や、圧縮の効果を今回から推定しません。旧条件を先に置く比較計画は、現行製品の受入評価へ到達しない結果になりました。

次の [測定再設計](template-ai-region-production-evaluation-plan-2026-10-14.md) では、変更済みの製品条件を最初に測定します。新実験案は別IDで、旧実験の停止・未送信を保持します。製品コードや保護規則を変える根拠は今回の旧条件だけでは不足しているため、追加の推測修正は行いません。

## 保持確認

checkoutの変更は本書、次の測定計画、AGENTS、Safety Contractの案内だけです。製品コードを変更していないため、既存OCR contractを維持しました。通常DBはapi_call_ledgerの新規1行だけを許容し、それ以前の437行、他の業務テーブル、ファイル・設定・旧checkout・過去封印を照合します。結果は `preservation.json`、封印は `completion-1/hashes.json` を正本とします。

新規別エージェントは使用していません。最終未使用4帳票の内容は未閲覧です。新たな通常サーバー起動、Chrome、実マイク、全件unit/E2Eは実行していません。既存の起動許可は有効で、今回の停止理由ではありません。改善ゴールは未完了です。
