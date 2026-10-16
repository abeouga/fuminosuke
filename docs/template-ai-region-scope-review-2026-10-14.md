# 修正6-C：所有範囲付き回答schemaの独立工程審査

2026-10-14。Astra / XHigh、turn `01a139d2-122d-7da0-b045-e26a69f16a9c`。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。新規の別エージェントは使用していません。

**6-Bの実装を審査し、6-Dへ進む条件を承認しました。差戻しを要する不具合は確認していません。** 実Lunaの意味精度と実画面5秒は未証明で、改善ゴールは未完了です。次工程は指定どおり **Astra / High** への切替後に実行します。

## 審査対象と判断

[修正設計](template-ai-region-scope-repair-design-2026-10-14.md)、[実装記録](template-ai-region-scope-fix-2026-10-14.md)、製品3ファイルの実差分、生成要求、保存済みV4失敗応答、保存・失効経路を照合しました。開始時に6-Bの159ファイル封印と現ソース、通常データ・設定・台帳・旧checkoutの一致を確認しています。

| 境界 | 判断と根拠 |
| --- | --- |
| 変更先と根拠の分離 | 意味の変更先は所有参照、新規枠の参照。既存枠変更は所有範囲と編集可能集合の積集合。読取専用参照は見出し・根拠・既存親として利用可能。実装のslot表を流用せず、契約から指定した21参照位置×11入力の231条件で確認 |
| 空の対象・枠追加 | 空enumや無制約stringへの退避なし。検出0でもaddが残り、実ASGIの案反映・共通保存・別接続再読込で1枠/1保存先を確認 |
| 応答の再検証 | V4実失敗応答は発行schemaで拒否。未宣言n参照はschemaが許す場合も既存compilerで拒否。いずれも案0・revision1を保持 |
| 既知の逆転 | c15の入力を読取専用c14へ入れ替えた応答を拒否。固定10入力の正解対照は受理可能。これは実Lunaの正答率の証明ではない |
| 要求・契約 | 開発5ページの全24要求を現factoryから再生成し、固定要求の内容/hashと一致。23regionと1非regionを確認。scope/schema digestも実metadataから再照合 |
| 失効と手動保護 | 旧v8の取得・反映・共通保存を拒否。手動同値role編集、geometry競合、unit digest差替え、merge履歴の分岐を拒否。取り消し・再反映と冪等性も確認 |
| 保存対応 | 再読込revision2、54物理セル、22入力と一意な22mapping/targetPath。名称・label接続・記号選択肢・文字3欄/記号19欄を各保存先まで追跡。製品RegionCropperの22cropが元画像の該当sliceと一致 |

生成schemaの上限は公式のStructured Outputs仕様と照合しました。今回の固定23regionは最大73properties・深さ9・文字集合3,803・enum793で実装の上限内です。OpenAIサーバーによるこの新schemaの受入は未測定です。[公式仕様](https://developers.openai.com/api/docs/guides/structured-outputs)

## 実行した検証と限界

証拠は `D:\llm-ocr-archive\template-ai\20261014-region-scope-review` に保存しています。以下はdeployの `backend\.venv\Scripts\python.exe -X utf8`、backendを作業ディレクトリとして実行しました。

| スクリプト | 結果 |
| --- | --- |
| `entry.py` | 前回終了状態と159ファイル封印、製品hash、439/924の共有予算が一致 |
| `review_schema.py` | 全24要求、231参照条件、対象集合の切替、検出0、保存失敗応答・既知逆転の拒否、10入力対照、固定gold/hash・21trial配分が一致 |
| `integration.py` | 新しい専用DBで17条件。provider HTTPだけを制御し、実ASGI・編集service・共通保存・再読込を実行。専用予約4 |
| `provider_cases.py` | 実失敗応答、未宣言n、schema上限の予約前拒否、検出0から追加保存の4条件。専用予約3 |
| `saved_proof.py` / `review_saved_meaning.py` | 54セル・22入力/保存先/cropに加え、22件の意味と読取定義の対応を確認 |
| `approve.py` | 製品・要求・画像・gold・配分を変えず、新しい審査済みmanifestを発行 |
| `finalize.py` | 終了状態、既存差分・通常データ・過去封印の保持、`git diff --check`、専用DBのバックアップと新規封印 |

外部通信を遮断した検証であり、実APIは0回です。ASGIハーネスは前工程の内容を読んで、新規の専用先にコピーして実行しました。生成schemaの条件検証と保存後の意味の追跡は今回別に作成しました。製品へのunit追加はありません。

最初のschema審査実行では、正解対照のc2が`headings`にあるという審査側の仮定で失敗しました。実際の固定対照は`annotations: label`で列見出しを表現しています。元対照・gold・製品を変えず、審査側の確認をその表現へ訂正し、初回script/logを保持しました。列見出しの正しい分類を求める基準は維持し、階層親への変換を追加要件にしていません。

製品コードは変更していません。6-Bの対象3ファイルruff/mypy成功時のソースとの一致を確認しましたが、この審査では静的検査・全件unit/E2E・frontend build・Chromeを再実行していません。実画面の表示/計時コードは変更されていません。実AI精度、5秒、実入力の位置合わせ・手書き精度、実マイクはこの審査の成功範囲に含めません。

## 次工程への承認と予算

正本は本審査archiveの `experiment-allocation-approved.json` と `approval.json` です。6-Bの提案とV4の停止証拠は変更していません。

- V5の21新trial、G1=4・G2=24・G3=60、計88送信を維持。上限96の未割当8回は転用しません。
- Astra / Highへ切替後、新しいresource/session/snapshot/plan、全製品・設定hash、各scope/schema digestと実要求を送信前に固定します。現reference要求は実試行bindingではありません。
- 各G1は固定対象領域の全品質、逆転・混入・保護・identity誤り0、保守的ASGI終了5秒以内を必須とします。一つでも不合格なら後続を停止し、再送・fallback・枠の転用をしません。
- G2/G3は通常画面の操作開始から、固定対象領域の正しい案が見えて操作可能になるまでを測定します。部分JSON・初回token・ASGI値で代用せず、ハーネス時間も差し引きません。全ページ品質を別に採点します。
- R04の145位置/143機能/手動2、R02 p2全22入力、他gold、優先範囲・画面条件を保持。最終未使用4帳票は内容未閲覧のままです。

実API/通常予約 **0回**、共有台帳 **439/924・残485回**。制御HTTPの専用予約は7回です。今回のcheckout変更は本書とAGENTS/Safety Contractの現在地記録だけです。通常業務データ・設定・台帳、旧llm-ocr、既存未コミット差分、過去封印は保持しています。通常OCRのmodel/prompt/schema、物理identity、mapping/crop、revision、保存/出力は変更していないため、**既存OCR contractを維持しました**。

起動許可は解決済みです。この審査ではサーバー起動を要しません。6-Dの必要時には許可された起動経路を使用します。実Luna精度と実画面5秒が未達の間、改善ゴールは未完了です。
