# 修正6-D：V5の実API測定と停止記録

2026-10-15。Astra / High、turn `01a13ecf-c081-7e72-b67c-d6f20d80f72d`。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。

**V5-G1-01は不合格です。全10入力の位置とラベル接続は一致しましたが、適用可能な案は0件、ASGI終了は11,208.887msでした。後続G1/G2/G3を停止し、再送していません。** 実画面5秒と改善ゴールは未達です。

## 条件と結果

[6-C審査](template-ai-region-scope-review-2026-10-14.md)が承認した `20261014-region-scope-review/experiment-allocation-approved.json` を正本とし、新しい専用DB・4資源・session/snapshot/planを固定しました。実験名は承認済みの `region-v5-20261014`、実施日は10月15日です。現行製品の未変更factoryを使用し、生成schema/scope/request digestと送信直前の全ソース・設定hashを検査しました。

R02 p2の固定範囲 `(963,0,278,911)`、10入力と印刷見出しc2、54可視セル、6画像、GPT-6 Luna / noneを維持。正解、対象範囲、画像、優先順位、品質分母は変更していません。

| 項目 | 実測 |
| --- | --- |
| OpenAIの受入 | 新しい所有範囲付きschemaを受理、completed応答1件 |
| 入力とラベル | c5/c4、c7/c6、c9/c8、c11/c10、c15/c14、c17/c16、c19/c18、c22/c21、c24/c23、c26/c25が固定正解と一致 |
| 範囲外への意味変更指定 | 0件 |
| 矛盾 | 10入力をfieldsに指定しながら、同じ入力をmissing_evidence / ambiguous_binding / manual_requiredにも指定 |
| 見出し | 保護情報のないc2をprotected_existingとambiguous_bindingに指定。正解は印字label |
| 読取方式 | ○・×・⊗のsymbol定義を生成したが、group.reading=nullで未接続。凡例はセル外なのにc53/c54を根拠参照として付けた点も不適切 |
| 適用可能な案 | 0、未解決宣言32件・一意な対象11セル。正しい操作可能機能は0/10 |
| 専用保存状態 | 4版すべてrevision1。案反映・明示保存は未実施 |
| 全ページ | 分母22入力、残り12入力未試験。全ページ完成ではない |

位置一致10/10は、意味・読取方式・保護・操作可能性を含む品質合格を意味しません。1回の結果から正答率や安定した速度は推定しません。

## 時間

| 区間・指標 | ms |
| --- | ---: |
| 公開ASGI受付から終了 | 11,208.887 |
| provider全体 | 10,311.998 |
| HTTP headers到着 | 2,499.059 |
| 最初の回答text | 3,807.686 |
| text完了 | 10,300.788 |
| snapshot確認 | 23.753 |
| evidence準備 | 53.644 |
| model pack | 73.358 |
| request body構築 | 8.854 |

provider内の時刻はその計測開始点からの値です。部分区間は包含関係があり、すべてを加算しません。初回textから完了まで約6.49秒。入力9,269 tokens、出力1,002 tokens、cached input 0です。実画面計時は未実施で、ASGI値を実画面値へ読み替えず、ハーネス時間も差し引いていません。

前回V4の8,399.642msより遅い単発結果ですが、schema変更の因果効果・p95を確定できる比較標本ではありません。ローカル準備だけを速くしても、この実測の5秒超過は解消しません。

## 保存応答での診断

`D:\llm-ocr-archive\template-ai\20261015-region-v5-live` に保存しました。`diagnose.py` はネットワークを禁止し、同じsnapshot・現行compilerで以下を確認しました。

| 入力 | 結果 |
| --- | --- |
| 実応答そのまま | 発行schemaに適合、案0・未解決32 |
| 診断用に保留指定だけを除去 | 案1・未解決1、意味接続10件だがmark.configureは0件。記号欄の完成にはならない |
| 診断用にreadingを接続しc2をlabel化 | 案1・未解決0、mark.configure 10件。ただしモデルの実回答ではなく、根拠参照の誤りも残る対照 |
| 以前から固定した正解対照 | 案1・未解決0、10入力と記号設定を表現可能 |

対照は製品への補正・保存や実測の採点には使っていません。原画像でも凡例がセル外であることを確認しました。誤ったholdを削除して自動適用する修正は採用しません。

次の[修正設計](template-ai-region-decision-repair-design-2026-10-15.md)用の `decision_probe.py` は、欄の判断と読取方式を一つにした製品外試作です。正解対照の10入力・ラベル・名称・親・読取方式の往復一致、矛盾と未使用readingの拒否を確認。1,584文字の正解canonicalを968文字で表現できました。token数・API速度・表/geometry等の全分岐の検証ではありません。

## 開始時の通常DB差分と保持

前回審査の終了snapshotと開始時に差があり、最初のsetupを送信前に停止しました。調査結果は `entry-drift.json` です。

- `alembic_version` が `editing_voice_dialogue_v2` から `template_analysis_v1`。
- 既存移行 `0009_template_analysis_v1.py` と一致する5テーブル・索引が追加。5テーブルはいずれも0行。
- assistant_sessionsは2→3件。元の2行のhashが完全一致。追加の実行主体は未特定。
- その他の業務テーブル、両checkoutのソース、設定、原本artifact、過去証拠、API台帳は一致。

これは今回最初のsnapshotに既に存在した差分です。通常DBを巻き戻さず、その完全な状態を今回の保持基準としました。元の `before.json` と失敗したsetup/logを保持し、再照合後に専用コピーを準備しました。通常DBへ今回追加したものは共有API台帳の1行のみです。

## 実行・変更境界

backendの `.venv\Scripts\python.exe -X utf8` で `entry_drift.py`、修正後`setup.py`、`live.py prepare`、`live.py run V5-G1-01`、`diagnose.py`、`decision_probe.py`、`finalize.py` を実行。準備と診断は実API0、runだけ1回です。後続の実行開始・予約は0。V5の`stop.json`を発行しました。

変更するcheckoutファイルは本書、修正設計、AGENTS.md、Safety Contractの現在地記録の4文書です。製品コード変更0。frontend build、全件unit/E2E、Chrome、保存後実OCR、実マイクは今回実行していません。既存成果を今回再実施した証拠とは扱いません。

実API/通常予約 **1回**、台帳ordinal440、ID `9be61ceb-f06f-45dd-844a-109294b024d7`。累計 **440/924・残484回**。台帳の前439行、通常業務データ、設定、旧llm-ocr、既存差分、過去の封印、最終評価用4帳票は保持します。詳細な終了照合は `preservation.json` と `completion-1/hashes.json` を正本とします。既存の通常OCR contractを維持しました。

6-Dは不合格で終了、6-A修正設計まで実施。次はモデル指定に従い **GPT-6.1 Sol / Highの6-B**、続いてAstra / XHighの6-Cです。起動許可は解決済みで、今回GUI前提のG1を通過しなかったため`start-runtime.ps1`の実行は不要でした。改善ゴールを完了にしていません。
