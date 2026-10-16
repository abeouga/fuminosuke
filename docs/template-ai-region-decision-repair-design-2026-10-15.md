# 修正6-A：排他的な判断と読取方式を接続する回答設計

2026-10-15。Astra / High。製品未実装。[V5実測](template-ai-region-v5-live-2026-10-15.md)の保存応答を根拠とします。対象はllm-ocr-deploy。LunaのAPIモデル、全領域品質、実画面5秒という目標を維持します。

## 判断

専門家A（契約設計の観点を模擬）：所有参照の制限はV5で機能しました。しかしgroupsとunresolvedを別々に生成でき、同じ入力に相反する指示を載せられます。生成形式に単一の判断集合を設け、決定的な検査とcanonical展開を分離します。保留を削除して正解を推定する方式は採用しません。

専門家B（応答時間の観点を模擬）：11.21秒のうちproviderは10.31秒です。矛盾の拒否だけでは待ち時間も成功率も改善しません。読取定義の前方参照と繰り返し出力を減らす必要があります。一方、初回textだけで3.81秒の単発結果なので、出力短縮だけで5秒になる保証はありません。

専門家C（保守性・検証の観点を模擬）：共通compiler・保存・物理identityまで作り直す必要はありません。モデル向けの伝送形式と既存RegionAnswerを明確な変換境界で分けます。新旧の似た業務モデルを二重管理せず、新形式は伝送専用とし、既存canonicalへ展開できない内容を拒否します。

客観的まとめ：単にpromptへ禁止文を追加する案は変更量が小さい一方、未接続readingと重複分類を形式上許す問題が残ります。全面的なセル別固定property方式は生成時の重複排除に強い一方、表・共有親・新規geometryを複雑にします。今回は**排他的な判断配列＋inline reading＋決定的adapter**を採用します。生成時に重複が絶対に生じないとは言わず、adapterが矛盾を拒否します。最適性・5秒達成は実装と測定で判定します。

## 確認済みの原因と未確定の仮説

- 実応答の10 input/label/axisは固定正解に一致。画像から欄の関係を全く読めていないわけではありません。
- 同じ10入力に3種類の保留、c2に2種類の保留を併記し、合計32件。現行schemaはこの意味矛盾を許します。
- `readings.inspection_result`を生成しているのにgroup.reading=null。現行のnullは文字欄を意味するため、定義を後付けしても接続されません。
- c2はroleLocked=false、protectedFacets=[]なのにprotected_existing。未固定の既存roleと手動保護を誤解した可能性があります。因果は1応答では確定しません。
- 実応答は1002 output tokens。正解対照の伝送試作は1584→968文字、UTF-8で1946→1330 bytes。token数や速度の比例関係は未測定です。

## 変更境界と責任

| 要素 | 責任・変更候補 |
| --- | --- |
| 新 `region_decisions.py` | Pydanticで伝送型を定義。ownership・重複・reading等を検査する純粋adapter。DB、通信、gold、保存へ依存しない。大きくなる場合だけ型とadapterを2ファイルへ分離 |
| `region_schema.py` | 伝送型を元に発行schemaを作る。既存の参照集合制約・$defs共有・上限検査を維持し、slot変更を明示検知 |
| `region_pack.py` | 新形式を説明し、未固定roleと保護を区別。例は一般的な合成例だけ。実帳票の正解をpromptへ埋めない |
| `visual_provider.py` | regionだけ新形式を受信し、発行schema→伝送型→adapter→RegionAnswerの順で検証。非regionは不変。契約version更新 |
| 既存compiler / save / UI | 正常canonicalを従前どおり処理。修正・推測・fallback用の新経路を追加しない |

旧設計の「同じRegionAnswer schemaを直接生成する」境界は意図的に変更します。新wireは業務状態の正本ではなく、読取方式を直接接続するための伝送表現です。RegionAnswer、geometry意味モデル、共通保存、DB schema、mapping/targetPath、crop、revision、通常OCRは維持します。製品への実装前後でSafety Contract §8/§10の検証を行います。

## 新しい伝送形式

候補versionは `template-analysis.region-decisions.v1`。rootは `version / catalogDigest / geometry / decisions` のobjectです。catalogDigestの照合、正規IDをモデルに生成させない方針を維持します。geometryは現行の型と制約を再使用します。

decisionsの各要素はkindで分かれるanyOfです。不要な枝の空配列をモデルに反復させません。

| kind | データとcanonicalへの展開 |
| --- | --- |
| `fields` | parent、inline reading、axis、fields。各fieldはinput/labels/name。group共通のaxis/reading/parentを一度だけ出力。異なるaxis/reading/parentは別group。binding.kind=labelledを決定的に復元 |
| `unlabelled` | parent、inline reading、cell/name/evidenceを持つ項目集合。現行の根拠要件を保持し、labelledの候補がないだけで自動選択しない |
| `table` | 現行InputTableのkey/name/parent/rows/row_key_strategy/include_empty_rows、columnsのreadingをinline化。列bindingと物理行/空行/row keyを省略せず、元のtableへ展開 |
| `heading` | 現行のcell/parent/nameを保持。セルの印字labelとの違いを維持 |
| `annotation` | 同じroleのcells配列。現行の4roleを維持し、1セルずつのcanonical annotationへ展開 |
| `hold` | cell指定とbounds指定を別の閉じた枝とする。cell指定は同じreasonのcells、bounds指定はbounds/reason。1対象への理由の反復をしない |

inline readingはnull（文字）又は`kind/options/evidenceRefs/evidenceText`です。独立したreadings配列と自由なreading keyの参照は生成させません。adapterは同じ定義を正規化して共有keyを付与し、各group/columnへ接続します。新しい定義に対するkeyはcanonical内容から決定的に作り、衝突・異なる定義の同一keyを検査します。既存保存markのIDやkeyを再採番しません。

凡例がセル外ならevidenceRefs=[]とし、実画像のevidenceTextを返します。近くの入力欄の参照を「凡例の位置」として代入させません。名前・選択肢・親の意味をサーバーで推測生成しません。

rootはobject、全property required、nullableを明示、additionalProperties=false、anyOfは内側に置きます。上限検査と未対応拒否を維持します。[OpenAI Structured Outputs公式仕様](https://developers.openai.com/api/docs/guides/structured-outputs)を2026-10-15に照合しました。新schemaのOpenAI受入は未確認で、ローカル検証と区別します。

## adapterの不変条件

1. 各c/n参照の主たる意味の判断は一つです。fieldのinput、table内の入力/行key等の変更対象、heading.cell、annotation.cells、hold.cellsを主対象として集計します。同じtableの列定義と実行列が同一セルを指す等、現行table内部の正当な重なりは一つのtable判断にまとめ、既存table検査を通します。別判断同士の競合・同じfieldの重複・異なるtableの所有競合は拒否します。
2. labels、parent、readingのevidenceRefsは根拠参照であり主対象に数えません。複数入力から同じlabel/親/凡例を読むこと、readOnly参照を使うことを許します。根拠参照があるだけでそのセルを変更しません。
3. geometryは独立したfacetです。resize後の同じ入力への意味付けを重複とは扱いません。split/merge/remove後に存在しない旧セルへの判断、未宣言n、別geometryの二重生成は現行のgeometry検査とadapterの参照検査で拒否します。geometry依存と意味は原子的に反映します。
4. fieldとholdの競合は`REGION_DECISION_CONFLICT`等の明確な形式エラーとし、片方を削除して成功へ変換しません。重複holdも1件へ勝手にまとめません。全応答を拒否し、送信済み予約は保持します。
5. `protected_existing`は実snapshotに対象の手動保護がある場合だけ有効です。空欄、textHintなし、未固定existingRoleは保護の証拠ではありません。保護の有無をモデルの申告で変更しません。実際の保護変更・同値手動意図は後段の既存guardでも検査します。
6. 省略は完成としません。所有セルの未処理、未検出領域のholdを既存の未解決経路へ残し、全領域品質の分母から除外しません。未処理を自動label/inputにしません。未検出位置は従前どおりgeometryのaddかbounds付きholdで表現します。
7. adapterに入力されるのは新wireだけです。旧V5失敗応答を新wireへ修復して再利用しません。明示的な対照変換は開発証拠に限定します。
8. completed応答のschema・adapter・compiler・live state検証後にだけ案を公開します。途中JSON、最初のfield、最初のtokenを5秒達成に使いません。

## 要求側の整理と速度

最初の修正では原画像6枚、54セル、全候補、geometry/保護/所有範囲を維持します。対象を小さくして5秒に合わせません。metadataは既存の可逆圧縮を保ち、未固定existingRoleは「保護ではない暫定値」、roleLocked/protectedFacetsは保存済みの制約と明示します。検出roleを入力/見出しの正解として固定しません。

名称やinput/label判断は残し、構文の反復、未使用readings、複数の保留理由列挙を減らします。promptだけの禁則追加、max_output_tokensによる途中打切り、外部APIへの自動再送は採用しません。今回の文字数減は31.7%のbyte減/38.9%の文字減に相当しますが、token数・5秒・正答率の改善率ではありません。

5秒未達の可能性を明示します。特に初回textまでの時間はこの形式変更だけでは保証できません。次の測定でも不合格なら、同じ条件の再送をせず、画像入力の重複や生成量・通信の内訳を証拠で見直します。画像削減、並列化、別モデルへの変更はこの6-Bへ黙って混ぜません。

## 製品外試作の証拠と限界

`D:\llm-ocr-archive\template-ai\20261015-region-v5-live/decision_probe.py` はfields/annotationだけの概念実装です。固定正解の10入力/labels/name/parent/readingは往復一致。実V5応答の判断競合、保留だけを除いた対照の未使用reading、重複decisionを拒否しました。

表、unlabelled、見出し階層、hold、全geometry、schema上限、OpenAI受入、共通保存はこの試作で未検証です。試作をそのまま製品へコピーしません。実応答の成功を装うために変更した正解ではなく、以前からの正解対照を別表現へ写した表現力の検査です。

## 6-Bの受入条件

- V5実応答の相反判断を再現し、未使用readingや誤ったprotected_existingも別の失敗入力として検証する。保留削除で通過するテストは作らない。
- 全kind、null/文字、symbol/checklist、混在する読取方式、同じlabel共有、親共有、tableの列/行/空行/row key、検出0、add/resize/split/merge/remove_duplicate、新規nと依存を検証。新形式が既存canonicalで表現できる機能を落とさない。
- schemaの全変更先slotを列挙し、writeRefs/geometryEditable/readOnly、空集合、schema上限を確認。表の内部参照を単純な重複判定で拒否しない。
- 開発5ページの24要求を再生成。対象/画像bytes/候補/保護情報が同じこと、非region要求が不変であること、新schemaが上限内であることを固定する。
- 正解対照と新wire展開を、正規IDの割当差を除いた意味・物理セル・読取定義・保存先対応で照合。54セル・22入力/保存先/crop、R04複合欄、既知逆転・混入、手動同値保護を維持。
- 実ASGIで案反映・共通明示保存・別接続再読込・競合/取消/失効を確認。必要なcontract検証、関連静的検査、backend full regressionは実装後半にまとめる。frontend変更なしなら不要な全件E2Eを加えない。
- 実行contractをv9から新versionへ進め、新wire/schema/adapter/promptの規則をdigestへ含める。旧v9の取得・反映・保存を既存の失効経路で拒否。通常DB一括移行や古い案への無言の変換はしない。

## 工程と再測定

| 工程 | モデル | 完了条件 |
| --- | --- | --- |
| 今回6-A | Astra / High | 保存応答の診断、変更境界、限定試作、受入条件の固定 |
| 次6-B | GPT-6.1 Sol / High | 上記全kind・契約・保存保持の実装検証。実API0回 |
| 6-C | Astra / XHigh | 新形式・変換・保護・計測条件を独立工程審査。新規別エージェントは自動起動しない |
| 6-D | Astra / High | 審査済みの新bindingで実API、G1合格後に実Chrome測定 |

V5の未送信枠は停止し続けます。次の新実験候補V6は、同じG1の4領域、G2の5ページ24送信、G3の12回60送信、合計88、上限96、未割当8転用不可を継承します。実装後のコード/契約/要求を固定し、6-Cで承認するまでは実送信しません。名前だけを変えた同一条件の再送を許可しません。

G1は固定領域の全品質、逆転/混入/保護/identity誤り0、保守的ASGI5秒のいずれかが不合格なら停止。G2/G3は通常GUI操作開始から固定領域全体が見えて操作可能になるまでを測定し、ASGIや最初の一部表示で代用しません。R02 p2全22入力、R04の145位置/143機能/手動2、他gold、優先領域、viewport、最終4帳票の未閲覧を保持します。

共有台帳は440/924・残484。設計の実API0回、直前測定1回。開始時に残量を再確認し、失敗・予約も計上します。5秒と精度の達成を確認するまで改善ゴールは未完了です。
