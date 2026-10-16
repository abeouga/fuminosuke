# 修正6-A：所有範囲付き回答と作業指示の修正設計

2026-10-14。Astra / High。製品未実装。根拠は [V4実測](template-ai-region-v4-live-2026-10-14.md) と `D:\llm-ocr-archive\template-ai\20261014-region-v4-live` の固定応答です。

## 目的と判断

領域の全入力について正しい意味付けを行い、通常画面で最初の正しい固定領域を5秒以内に操作可能にする目的を維持します。範囲違反を防ぐだけで完了とはしません。

今回、現行Lunaは所有10入力を保留し、範囲外の34annotationsを返しました。schemaは型・参照の書式を検査しますが、所有集合は回答後のcompilerだけが検査します。また「未使用印字はlabel」という指示に対象集合の限定がありません。所有制約を回答生成時にも与え、入力を完成させる作業と周辺情報の参照を明確に分けます。

これは失敗のすべての因果関係を確定したものではありません。適切な回答でも5秒を超える可能性があります。単なる文字数減を速度改善の証明にしません。

## 責任分担

追加候補は `backend/app/template_analysis/region_schema.py` です。snapshotから渡されたwriteRefs/geometryEditable/可視参照集合を使い、RegionAnswerの生成schemaを限定する純粋関数を置きます。DB・通信・保存・goldには依存させません。

`region_contracts.py` は意味上の回答モデルの正本とします。別の似たモデルを二重管理せず、schemaの既知の参照位置を宣言的に分類して限定します。定義や参照slotが増減したら黙って未制約に戻らず、生成時に検知して送信前に拒否します。任意の文字列キーを探索して推測変換する方式は使いません。

`visual_provider.py` のregion経路だけで限定schemaを発行します。form/grid等の非region経路は不変とします。回答は既存のRegionAnswerとcompilerへ渡し、所有・geometry・意味・保存検査を引き続き実行します。生成制約を理由に検査を削除しません。

`region_pack.py` の作業説明を修正し、編集先と読取専用の根拠を区別します。原画像、6画像の構成、候補61件、54セルのgeometry/保護情報は今回の最初の修正では維持します。画像削減や対象の縮小を同時に行いません。

## 生成schemaの参照区分

| 区分 | 適用箇所 | 許可集合 |
| --- | --- | --- |
| 意味の変更先 | LabelledInput.input、RegionUnlabelled.cell、RegionAnnotation.cell、RegionHeading.cell、RegionRow.cells/keyCell、RegionProblem.cell | writeRefs内のc参照、生成geometryで宣言するn参照 |
| 既存geometry変更先 | Resize.cell、Split.cell、Merge.cells、RemoveDuplicate.cell | geometryEditable∩writeRefsのc参照 |
| 根拠・関係先 | labels、evidenceRefs、evidence.refs、parent、remove_duplicate.survivors | 発行済み参照＋新規n参照 |
| 新規geometry定義 | add.ref、merge.ref、split.parts.ref | 既存のn参照書式 |

null許可は現契約を保持します。未宣言n参照、重複、自己参照、型不一致、所有外bounds、保護変更は既存compilerで拒否します。geometryがない応答へn参照を自動補完しません。

同じ集合は$defsで共有します。空の集合に空enumや無制約stringを生成しません。既存geometry変更先がない場合はresize/split/merge/remove_duplicate分岐を生成候補から外し、addとその後の意味付けを保持します。検出0セルの領域も除外せず、新規枠＋意味またはbounds付き保留を表現します。可視参照が空の場合でもn参照と現行null/空配列条件を扱います。

schema全体のenum数・文字数・深さ・property数を送信前に検査します。上限超過時に対象を切り捨てず、予約前に明示エラーとします。開発5ページの全24領域を生成して、この変更が既存の全ページ操作を阻害しないことを実装時に確認します。titleなど検証に影響しない自動付与情報は省けますが、descriptionに意味契約がある場合は削りません。

OpenAI公式資料ではenum/anyOfと$defs、キー順を使用できます。rootはobject、各propertyはrequired、additionalProperties=falseを維持します。schema適合は意味の正しさの証明ではありません。[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。モデルは [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) を維持します。

## 指示と返答量

指示を次の順に整理します。

1. この要求は白紙帳票の指定領域のテンプレート完成であり、記入済み値のOCRでも全ページの役割一覧作成でもない。
2. writeRefsとgeometryBoundsを編集対象とし、周辺セル・画像は見出し、凡例、文脈の根拠として参照する。
3. 各所有セルを画像で判断し、記入位置にはinput/labels/axis/name/readingを接続する。印刷された質問と空欄が一対であれば、空欄であることや候補が複数あることだけを理由に保留しない。関係が不明なら従来どおり保留する。
4. symbol/checklistの選択肢は画像の凡例を読み、同じ読取定義を共有する。文字・日付・数値・自由記入を勝手に記号化しない。
5. annotationsは所有セルのうち入力・見出し等で使わないものだけ。readOnlyRefsをannotations/unresolved/変更先へ出力しない。labelsや根拠参照には使える。
6. 省略は未解決、複合欄は独立値を維持、手動保護は保持する。

schemaの意味順はgroups/tables/readings/headings/annotations/unresolvedを候補とし、意味定義を優先してから残りを分類します。完成前の部分JSONを操作可能な案として公開しません。キー順変更は量・速度の保証ではありません。

必要なら作業を説明する小さな合成例を1つだけ入れます。実帳票の名称・正解ID・正解symbol列をpromptへ埋め込まず、通常欄で「編集先と読取専用見出し」の関係だけを示します。例のIDを実入力と混同しない形式にします。追加例の有無は6-Bで固定し、6-C後・実応答後に変更しません。

今回の固定正解対照はcompact JSONで1,584文字、失敗応答は2,154文字でした。正しい全10入力を表すために失敗応答より長い出力が必須なわけではありません。ただしtoken数、生成時間、精度が同じ割合で改善するとは主張しません。

## 設計試作と未確認事項

製品外の `scope_schema_probe.py` では、旧schemaが許す実失敗応答を候補schemaが拒否し、全10入力の正解対照を許すことを確認しました。11変更slotで読取専用セルを拒否し、根拠としての読取専用参照は保持しました。schema文字数は9,496→8,312です。

試作は空geometryEditable、全24領域、全geometry操作、OpenAIによるschema受入を検証した実装ではありません。そのまま製品へコピーして完了扱いにしません。画像の広さ、候補の数、Lunaの視覚判断の影響は未確定です。全件保留が続く場合はこの設計も不合格であり、保留を入力へ強制変換しません。

## 実装時の必須検証

- V4実失敗応答は生成schemaでも既存compilerでも拒否。範囲外要素を削除して成功へ補正しない。
- 固定10入力の正解対照が同一meaning/role/ID/operationsになる。見出しを入力にする既知の逆転、複合欄混入は従前の保存応答とgoldで判定する。
- 上表の全参照位置、null、宣言済み/未宣言n、読取専用の根拠・親、geometry空集合、検出0領域、共有親・複数領域を確認する。
- 全24領域のschema/要求を生成し、schema制限内・画像bytes/geometry/候補集合保持・非region不変を確認する。
- 短い実ASGIで旧案失効、正常案反映、明示保存、別接続再読込、手動同値編集/競合保護、mapping/targetPath/crop/revisionを確認する。必要な静的検査と生成契約の検査を行う。
- frontendは変更しない予定。表示・計時は既存経路を使い、実測段階では実Chromeで検証する。

## 契約と既存データ

region生成schemaのpolicyを新設し、実行contractをv8から新versionへ進めます。schema生成規則・promptを契約digestへ含め、実要求には対象集合と最終schemaのdigestを固定します。static policyと対象依存digestを混同しません。既存v8案はGET/反映/保存で従前と同じ失効経路を通します。通常DBを一括移行しません。

既存のRegionAnswer意味モデル、共通保存、physical ID、mapping/targetPath、crop、revision、通常OCRを変更しません。prompt/schemaの意図した変更はAGENTS/Safety Contractへ記録します。互換用fallbackや再送による救済は追加しません。

## 工程と次の実測

| 工程 | モデル | 完了条件 |
| --- | --- | --- |
| 6-A：今回の修正設計 | Astra / High | 実失敗再現・変更境界・受入条件を固定。本書まで |
| 6-B：実装 | GPT-6.1 Sol / High | 全参照制約、通常/空領域、全24要求、保存保持、旧案失効を検証 |
| 6-C：独立工程審査 | Astra / XHigh | 実装と再現証拠を照合し、新測定条件を承認 |
| 6-D：再測定 | Astra / High | Lunaで同じ品質分母と実Chrome5秒、保存保持を実測 |

新しい別エージェントは起動しません。V4は停止したまま保持します。次の実験案はV5とし、G1の4対象・G2の5ページ24送信・G3の12回60送信、計88・上限96・未割当8転用不可を継承します。6-B実装後のコード/contract/要求を固定して6-Cで審査するまで、V5の実送信を許可済みと扱いません。

共有残枠は実装前に再確認します。現時点439/924・残485回。各G1の全領域品質、逆転/混入/保護/identity誤り0、保守的ASGI5秒のいずれかが不合格なら以降を止めます。再送・fallback・同一条件の実験名だけ変更は行いません。G2/G3は通常GUIのクリックから固定全領域が見えて操作できるまでを計測し、初回token・ASGIで代用しません。

R02 p2全22入力、R04の145位置・143機能・手動2領域、他goldと手動領域の分母、固定優先領域・画面条件を維持します。修正7用の最終4帳票は内容未閲覧を保ちます。高速化・精度・改善ゴールは未達です。
