# 修正6-C：現行製品先行測定の手順審査

2026-10-14。Astra / XHigh、turn `01a13946-d9bc-7701-acc7-ecf9bb1d56c9`。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。

**region-v4の測定手順を承認しました。製品コードは変更していません。** 実Lunaの品質・実画面5秒を合格にしたものではありません。次は6-DのAstra / Highへ切り替え、現行製品によるV4-G1-01の新しい実試行を固定してから測定します。

## 審査の対象と判断

対象は [測定再設計](template-ai-region-production-evaluation-plan-2026-10-14.md) と `20261014-region-v3-live/experiment-allocation-v4-proposal.json` です。

旧v3の診断Aは、従来指示・従来metadataを使用して読み取り専用セルへの変更指定を返しました。停止markerと1送信の証拠が残り、B/C/Dの実行開始・送信記録はありません。現行製品のDはAの結果を見る前から実装・審査されていた条件です。新計画は未測定の現行製品を評価するもので、失敗したAを同じ条件で再送するものではありません。

現行製品に対する未知の不具合を仮定してprompt/schema/guardを再変更する根拠はありません。測定の順番を変え、品質・実画面時間・保存受入へ進む設計は妥当です。ただし圧縮単独の因果効果を示す比較にはならず、その主張は行いません。

## 照合結果

| 審査項目 | 結果 |
| --- | --- |
| 製品3ファイルと契約 | 6-B/6-Cの承認済みhashと一致 |
| 前工程の保存証拠 | 59件の封印一致、v3の停止を保持 |
| 新trial | 21個のV4 IDが固有、旧trialとの重複なし |
| G1 | 4対象領域・目的・セル範囲を維持 |
| G2 | 開発5ページ、計24送信を維持 |
| G3 | 固定12回、計60送信、優先領域・画面条件を維持 |
| 全配分 | 4＋24＋60＝88、上限96、未割当8の転用不可 |
| 原画像・検出結果・gold | 5ページのhash、定義/承認hashが一致 |
| 最初の全領域 | 10入力＋所有見出しの11セル。R02 p2全ページ22入力の分母を維持 |
| R04 | 145位置・143機能・手動2領域を維持 |

ネットワーク禁止下で、未送信Dのsnapshotから現在の製品`request_body`を直接呼び出しました。出力本文は当時固定したDと完全一致し、Luna/none、8192出力上限、6画像、回答schema、54セル・61候補、圧縮metadata 7,019文字を確認しました。旧packへの差し替えや測定器のfactoryを利用した比較ではありません。

これはreferenceの再現です。V4用のresource/sessionや実試行bindingは今回作成していません。次工程で旧Dのresourceや本文をそのまま送信することは承認していません。

## 承認条件と次工程

承認の正本は `D:\llm-ocr-archive\template-ai\20261014-region-v4-protocol-review\experiment-allocation-approved.json` と `approval.json` です。

1. v3は停止を維持し、V4は新resource/session/snapshot/plan/対象領域で固定する。現行製品のfactoryから実際の要求を生成し、送信前に本文・設定・原画像・コードhashを照合する。
2. 最初はV4-G1-01の10入力＋見出し全体を評価する。品質、逆転、混入、保護/identity、保守的なASGI計測5秒のいずれかが不合格なら未送信分を停止する。
3. 実画面5秒はG2/G3の通常GUIで、クリックから正しい固定領域が表示され実際に操作可能になるまでを測定する。初回tokenやASGI時刻で代用しない。測定後に観測負荷を差し引いて合格へ変えない。
4. 共通反映・明示保存・再読込で物理セル、意味、mappingId/targetPath、crop、revision、手動修正を確認する。欠落・未解決・手動対象を分母から除かない。
5. 予約・失敗も計上し、再送・fallback・未割当枠転用を行わない。現行製品の同一条件が失敗した場合、実験名だけを変えて繰り返さず、保存応答の診断と根拠のある審査済み変更を先に置く。

製品の6-B修正は不要です。次の6-DはAstra / Highです。現在はAstra / XHighのため、実API送信へ先行しません。

## 実施範囲と保持

新たに実行した検証は `review.py` と `approve.py` で、いずれも終了0です。証拠は `protocol-review.json`、`current-production-reference.json` に保存しました。同一コードに対する既済17条件・frontend build・全件テスト・Chromeは今回再実行していません。API/通常予約/専用予約はいずれも0、共有438/924・残486回です。

checkoutの変更は本書とAGENTS/Safety Contractの案内追記だけです。製品コード、通常データ、設定、旧checkout、過去の証拠を変更していないため、既存OCR contractを維持しました。保持結果は `preservation.json`、封印は `completion-1/hashes.json` を正本とします。新規別エージェントは使用せず、最終未使用4帳票の内容未閲覧を維持しています。

独立審査は、設計担当のAstra / Highとは別のAstra / XHigh工程で行いました。別エージェントの審査とは扱いません。実精度、実画面5秒、改善ゴールは引き続き未完了です。
