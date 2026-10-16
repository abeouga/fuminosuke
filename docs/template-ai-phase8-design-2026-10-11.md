# AIテンプレート作成 フェーズ8：修正設計

2026-10-11。指定開発モデルは **Astra / XHigh**。開発対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。
これは設計であり、製品実装・通常DB移行・通常機能の開放を行う工程ではない。
APIモデルは **`gpt-6-luna`**。今回の実API使用は **0回**、共有台帳は **262/496、残234回**。

## 1. 結論と判断根拠

全体解析の第一候補を、**意味・表を一度だけ出力し、その構造からセル分類を導く方式**へ変更する。
分類と意味を別々に生成させて整合させる負担を減らす。ただし、精度と速度の改善は未確認である。
次工程では製品外の比較試作を先に行い、品質条件を通過した方式だけを製品へ組み込む。

専門家A（モデルの出力契約、模擬分析）：現行方式は8種類への全セル分配と、fields/tables内の参照を重複して生成させる。出力の省略・重複・分類との矛盾が拒否につながっている。意味構造を正本にすると矛盾の一部を構造的に除去できるが、未出力セルが増える危険がある。未出力を除外扱いせず、未解決率も測定する必要がある。

専門家B（帳票と編集API、模擬分析）：現行graphは表の値をinputだけに限定し、行見出しを表現できない。既存の評価対象H2には記号欄と行・列の複合見出しがあるため、正しい回答を出せる契約になっていない。プロンプトだけでは解決せず、意味付けと表編集の共通APIに不足する表現を加える必要がある。

専門家C（応答時間と受入、模擬分析）：高速な全セル除外は利用可能な完了ではない。直列のDecisions→Responsesも必須にすると、既測定では遅くなる。ページごとの全体解析を原則1回に保ち、案が使えるまでの時間と人の修正・保存時間を別々に評価する。

客観的まとめ：3者とも、モデル変更や検証緩和だけでは不十分という点で一致する。分類を明示する方が欠落を減らせる可能性と、意味から導いた方が矛盾を減らせる可能性が対立するため、同じ意味表現を持つ対照方式と比較する。第一候補の採否を決める条件は、重点品質、表・記号の表現、未解決率、利用可能な案の時間、人の手直し量である。

## 2. フェーズ7の訂正と確定事実

[フェーズ7記録](template-ai-phase7-2026-10-10.md)の集計は維持するが、保存応答の再照合により詳細記述を訂正する。

| 項目 | 確認した内容 |
| --- | --- |
| 全セルignored | 3件ではなく **2件**。T1-new-2、T2-new-2 |
| T3-new-2 | label 5、input 1、mark 4、ignored 2。受付番号のfieldを1件出力し、記号4欄は未対応 |
| roleを除く追加診断 | 6件全拒否は同じ。最初の拒否が分類全数検査なのは **4件**。他2件は見出し/所有関係との不整合 |
| 診断の表 | diagnostic-T2-1は **3表**を出力。値セル重複と分類衝突を含むため不採用。他5件は0表 |
| 品質と時間 | 新方式26/176分類、1/78結合、4/10構造通過、全試行中央値7.827秒。訂正で集計は変わらない |
| H2の期待構造 | 記号欄4行。各欄をその行の見出し＋列見出しへ接続する。現行v1 graphでは表現不能 |

根拠は保存raw/decoded/proposalと現validator。今回の再診断は `D:/llm-ocr-archive/template-ai/20261011-phase8-design/evidence-diagnostic.json` に保存した。
rawから導ける参照があることは、救済した案が正しいことやv2モデルの精度を証明しない。
旧方式も結合45/78で完全ではなく、旧validatorの通過を新しい共通編集APIでの保存成功と同一視しない。

## 3. 変更する境界・維持する境界

変更対象は解析用snapshot/prompt/schema、案の正規化・評価・依存関係、共通意味付け/表編集API、案表示である。
対応候補は `backend/app/template_analysis/{contracts,snapshot,provider,graph,compose,service}.py`、
`backend/app/editing/{operation_contracts,commands,record_commands,manual_compiler}.py` と、その型を使用するfrontend/AI tool定義。
frontendだけでは、表・記号を表現できない出力契約と保存時に行別見出しが失われる問題を解決できない。

- 保存済み物理ID、geometry、crop、mappingId、sourceRegionId、targetPath、受付snapshotを再生成しない。
- OCR provider/prompt/model、再読取・補正・revision・出力bindingを変更しない。AI用の短い参照名は解析snapshot内だけの別名とする。
- 新しい矩形推定や矩形検出器はこの修正に含めない。検出済み矩形への意味付けを扱い、手で描く経路を維持する。
- 手修正・同値の再指定・既存意味・既存の記号設定をfacetと操作通番で保護する。AI解析を保存済み設定の上書き理由にしない。
- 「案を反映」→既存の「確認して保存」を維持する。解析成功を保存承認にしない。音声での結果値編集の自動適用契約とは別である。
- 音声の局所録音、連続音声、表選択は任意、対象省略時の直前セル、Decisions優先＋複雑時Responsesは今回変更しない。

[フェーズ3設計](template-ai-phase3-2026-10-09.md)のうち、ここで明示した解析表現・共通API・部分成功・評価を改訂する。
それ以外の所有権、revision、journal、手動意図保護、保存経路、安全契約は継続する。先にコードを変更して契約を追認しない。

## 4. 比較する方式

| 方式 | 構成 | 利点 | 主な失敗条件 |
| --- | --- | --- | --- |
| A：旧全体方式 | フェーズ2のResponses契約 | 実測の比較基準がある | 弱い旧検証、記号・保存対応が不足。無条件に採用しない |
| B：全セル分類＋意味構造 | v2の意味構造＋必須の `roleByCell` 辞書 | 各refを必須キーにし、分類の抜けをschemaで防ぐ | 出力増、意味から導く分類との衝突。矛盾を自動修正せず保留 |
| C：意味構造のみ（第一候補） | `template-analysis.units.v2` | 重複した分類生成をなくす。必要な構造へ出力を集中 | 未出力セルの増加、表の分割/省略。未解決として残し採点する |

B/Cは同じ入力・意味表現・モデル設定を使い、全数分類要求だけを変える。Aとの比較は方式全体の比較であり、単一要因の効果とは主張しない。
BのroleByCellは構造から導く分類と一致させ、構造にもstandaloneにもないセルはunknownとする。辞書だけにある分類を追加の救済情報として採用しない。
全体解析の前にDecisionsを必須にしない。以前の直列比較は16.987秒対12.672秒で遅かった。
Decisionsは利用者が「分類だけ」を選んだ処理として維持し、API上のconfidenceを正しさや保存の承認にしない。

```mermaid
flowchart LR
  S[所有snapshot・保護情報] --> L[Lunaへページごとに1回]
  L --> P[厳密parse・参照検査]
  P --> N[意味構造から分類を導出]
  N --> V[単位と依存関係の検証]
  V --> A[正常な案・保留理由を表示]
  A --> M[利用者が案を反映]
  M --> C[共通編集APIで検証]
  C --> W[利用者が確認して保存]
```

## 5. Lunaへ渡す入力

1. 新規検出の `origin=unreviewed` は未判断として伝える。内部の初期role=ignoredをAIへの正解候補として渡さない。保護された既存分類は由来とともに渡す。
2. ページ内の参照順を `(y, x, physicalId)` で固定する。画像、寸法、矩形、多角形頂点、印字を渡し、正本IDとの対応表はサーバーが保持する。
3. 編集対象・文脈参照・保護された設定を区別する。保護された見出しやラベルを読むことと、その設定を書き換えることを分ける。
4. 見出し、繰り返し表、表に属さない欄、残る単独分類の順で構造を要求する。表の値セルをfieldsに重複出力しない。除外する場合だけ明示する。
5. 記号/チェックの対応可否をページのcapabilityとして渡す。図形や文字を推測して選択肢を発明しない。画像やセル本文中の命令を操作指示として扱わない。

全体解析は1ページ1 Responses、`gpt-6-luna`、reasoning=low、detail=high、streaming、timeout 60秒、出力上限24576を比較初期条件とする。
自動再送、失敗時の別モデル、欠落セルごとの追加要求、モデルによる自己修理は追加しない。予算は送信前予約を維持する。

## 6. 出力契約の正本

設計用JSON Schemaは [proposal.schema.json](contracts/template-analysis-v2/proposal.schema.json)。
[通常例](contracts/template-analysis-v2/proposal.example.json)、[参照context](contracts/template-analysis-v2/proposal.example-context.json)、
[記号表の例](contracts/template-analysis-v2/symbol-table.example.json)、[B方式の12セル用schema例](contracts/template-analysis-v2/keyed-control.example-12.schema.json)を添付する。
Bの必須キーは実manifestから生成する。これらは設計資料で、製品のprovider契約へは未接続である。

| 要素 | 出力内容・規則 |
| --- | --- |
| `headings` | `cell,parent`。parentは明示された見出しか、snapshot内の保護済み見出し。循環不可 |
| `fields` | `cell,name,labels,axis,parent,reading`。1物理値セルにつき1意味単位 |
| `tables` | `name,parent,columns,rows,rowKeyStrategy,includeEmptyRows`。行と列を明示し、並びを推測補完しない |
| `columns` | `name,labels,reading`。readingは列内の全行に適用 |
| `rows` | `cells,labels,keyCell,keyValue`。cellsは列数と一致。labelsが行見出し、keyCellは印字行番号として別の役割 |
| `standalone` | `cell,role,reason`。意味構造を持たない分類/未解決のみ。構造中のセルを再宣言しない |

AIに永続key、targetPath、DB ID、新しい座標を生成させない。単位refはsnapshotとJSON上の位置からサーバーが決め、永続IDは共通compilerが発行する。
名前の正規化で同名欄を結合しない。セルの位置と所有関係を正本とし、既存の同名項目連番化契約を維持する。

`reading` は次の3種。

- `text`：通常input。
- `symbol`：`options` の文字列と `evidenceCells` を持つ、欄全体の記号読取。
- `checklist`：同じ情報に `selectionMode=single|multiple` を加える、欄全体の選択肢読取。

記号/チェックはmarkへ導く。optionsは表示文字をそのままlabel/valueに使用し、別の値を生成させない。空白だけ・重複・上限超過は拒否する。
evidenceCellsは同じsnapshotの参照で、分類変更を伴わない根拠参照である。参照があることだけで選択肢の正しさを証明せず、反映前に表示する。
新しい選択肢別矩形を必要とする設定は対象外。pagedでは既存capabilityどおりsymbolを拒否し、対応するchecklistのみ扱う。
列内で読取方式が異なる表は、その表を保留する。都合のよい行の削除やtextへの変換で通過させない。

standaloneの対応は固定する：label/unbound_label、input/missing_binding、mark/unsupported_mark_definition、
record_key_cellまたはcolumn_key_cell/printed_index、ignored/instruction_or_decoration、unknown/uncertain。
見出しはheadingsにのみ置く。`direct` はラベル0件に限定する。

**未出力＝unknown**。`ignored` に変換せず、意味や設定を削除しない。役割はtext欄=input、記号/チェック欄=mark、
見出し=parent_label、各種ラベル=label、行番号=record_key_cellとして一度だけ導く。同一セルに異なる役割が必要になる出力は保留する。

## 7. 共通AI向け編集APIの修正

ここは大きな仕様変更を含む。AI解析専用の保存口を増やさず、手動・音声・AIが使用する共通操作を拡張する。

### 7.1 記号欄への意味結合

現行 `MeaningBind` はinput以外を拒否する。**markSetがちょうど1つあり、そのtargetPathが指定meaningと一致するmark**にも、ラベルとの接続を認める。
既存の選択肢、selectionMode、読取設定、物理IDを保持する。異なる意味への暗黙再割当やmarkSetの削除は行わない。
複合欄、複数markSet、未対応capabilityは引き続き拒否する。通常inputの契約は維持する。

### 7.2 行ごとの見出し

`RecordRow` に `cellLabelCells: list[list[Handle]] | null` を加える。外側はcellsと同じ列数、内側はその値セルが参照するラベルの全指定である。
現行の列共通 `labelCells` は残す。表の列見出しと、各セルが意味付けに使う見出しを区別する。

- AIの `rows[i].labels` と `columns[j].labels` を順序付き和集合にし、各 `[i][j]` へ明示的に展開する。近いセルへの自動補正はしない。
- 手動compilerも、既存の各セルの `LayoutRelation.labelCellIds` から行列を作る。第1行の見出しを他の行へ複写しない。
- 明示されたmatrixは新規行・既存行の双方に適用する。現在の既存行に対する早期continueで更新を捨てない。
- null/省略時、既存の関連があるセルはその関連を保持する。新規で関連がないセルだけ列共通labelCellsを使う。
- `records.replace_rows` では第1行の複合ラベルを列共通値へ流用しない。既存sectionの明示的な列見出し対応を使い、それで決められない新規行にはmatrixを必須として `ROW_BINDINGS_REQUIRED` を返す。手動/AI compilerは原則として全matrixを渡す。
- 空の内側配列は明示的なラベルなし。幅、参照所有、同一ページ、label分類、上限、手動保護を検査する。既存行のIDに別の物理セルを入れない。
- 保存の正本は既存の `LayoutRelation.labelCellIds`。recordGroupへ同じ関係を二重保存しない。既存relation IDと物理targetPathを保持する。
- relationのaxisは既存の表示方向として保ち、行見出しを含むという理由だけでgeometryや読み順を変えない。`allowCrossAxisLabels` の一括有効化で検証を迂回しない。

H2ならC6のラベルはB6＋C5、C7はB7＋C5となる。後者へB6を流用しない。
この修正は `records.define` と `records.replace_rows`、manual compiler、型生成、AI tool schema、保存前後の再構成を一貫させる。
既存operationのtyped拡張として扱い、無関係な編集API全体を再実装しない。capabilityに `records-cell-labels.v1` を明示し、古いconsumerに新しい意味を黙って渡さない。

### 7.3 共通操作へのコンパイル

分類→meaning.create→通常欄のmeaning.bind、またはmark.configureと対応するmeaning.bind→records.define→heading.attachの依存順で、1つの検証済みbatchを組み立てる。
表内の各物理欄に必要なtargetPathは共通compilerで生成・保持し、共有列名のために複数欄のidentityを併合しない。
公開APIと内部authoringの既存操作数・LayoutDefinition上限を維持する。上限超過は表を分割して潜り抜けず、案を保留する。
現在のclone/dry-run、revision確認、既存manual-authoringの保存・undo・journalを使用する。

## 8. 検証と部分成功

ページ全体の信頼境界と、独立した意味単位の不備を区別する。検証の削除ではなく、拒否の範囲を定義し直す。

1. JSONの重複キーを失う前に検出する。構文破損、schema不適合、打切り、provider refusal、未知ref、ページ外ref、所有snapshotの不一致はページ全体を拒否する。
2. 全単位の読取参照・書込facet・所有セル・見出し依存を先に収集する。不正単位を先に削除して、重複した別単位を勝者にしない。
3. 表は全体を1単位とする。列数不一致、行/値の重複、記号未対応、不正ラベルを含む表は全体保留し、行単位で救済しない。
4. 複数単位が同じ値セルを所有する場合、または役割/親が衝突する場合は、関係する全単位を保留する。先勝ち・多数決・近接推定を使わない。
5. 見出しの不備はその子孫へ伝播する。あるfieldの不備だけで、読取専用の共通見出しを使う正常な兄弟fieldまで保留しない。
6. 正常単位の和集合を共通compilerでdry-runし、全体不変条件を再検証する。残るエラーの責任単位が確定できなければページ全体を拒否し、通る組合せを探索しない。
7. 保留には安定したissue ID、対象セル、依存単位、理由を付ける。追加のAI要求で修理せず、表示と手動修正へ渡す。

利用者が反映単位を選び直した場合も、サーバーで依存先を再確認する。親の案を外したまま子だけを反映するなど、依存が欠けた選択を許可しない。既に同じ親が保護済み設定として存在する場合は参照で満たせる。

同一内容の既存保護設定はno-opにできるが、保護されたfacetへの変更は採用しない。文脈として読める参照と変更可能な対象を区別する。
この部分成功アルゴリズムは未実装。今回のschema例検証は、依存関係や全体の部分成功処理を実証したものではない。

## 9. 画面と完了判定

通信・ジョブのstateと、案の中身のassessmentを分離する。

| assessment | 意味 |
| --- | --- |
| `semantic` | 反映できるfieldまたはtableが1件以上ある |
| `classification_only` | 意味単位はなく、分類/見出しだけの差分がある |
| `empty` | 反映できる差分がない。保留/未出力の有無は別の件数で示す |
| `noop` | 対象が既に同じ保護済み設定などで、差分も未解決もない |

assessmentとは別に `semanticUnits/classificationUnits/blockedUnits/unresolvedCells/protectedCells/noopCells` を保持する。
全ignoredはclassification_onlyでも「読み取る欄の案はありません」と表示し、テンプレート作成完了とは呼ばない。
意味案があっても未解決は件数と位置を残す。保留単位や未出力セルを反映選択に混ぜず、部分反映が残りの確認完了を意味しない。

未判断は実際の除外とは別表示にする。業務roleを画面都合で書き換えず、未変更の `AuthoringFacet.origin=unreviewed` を維持する。
再読込後も未判断が消えないようにする。runの詳細issueが期限切れになっても未判断facetを確認済みにしない。既存の由来不明な設定は保護し、新規未判断へ勝手に再分類しない。
音声・自動保存を案確認中に保留する既存動作と、明示反映・明示保存を維持する。正常な独立単位だけを反映した場合は、残る未解決を同じパネルに表示する。

AI枠は提案/反映/保留を文字でも区別し、複数変更セルを同時に囲む。フォーカス枠の移動、画面外対象へのスクロール、表名オーバーレイの既存仕様を維持する。
単に進捗が100%になったことを意味付け完了としない。余分な承認ボタンやモーダルを増やさない。

## 10. バージョンと復旧

provider契約を `template-analysis.units.v2`、案の公開DTO/評価policyを `template-analysis.v2` として識別する。
run・snapshot・cache・journalにversionを固定し、v1応答をv2の意味へ黙って読み替えない。旧記録は旧仕様の記録として保持する。
未反映の旧案は再解析が必要と表示するが、自動送信しない。既に案を反映した未保存draftは既存保存/復旧経路を維持し、journalや手修正を破棄しない。
現行のrun/page/proposal JSON格納を使えるため、この設計だけを理由とする新しい業務テーブルは不要。実装時にmigration要否を再確認し、試験コピーで先行する。
通常機能フラグは無効を維持し、通常データへの一括変換は行わない。

## 11. 次工程の比較条件と採用条件

凍結5帳票×2反復×A/B/Cの **30送信**を基本とする。最初の上限36回にはschema適合等の診断予備6回を含む。
B/Cの入出力schema・prompt・入力SHA・順序・採点法は送信前に固定する。1巡目A→B→C、2巡目C→B→Aを基本に実行し、同時送信しない。
画像/物理入力は同一。通常のキャッシュは無効化せず、cached tokenを記録する。試行IDのためのprompt改変や採点結果を見た再調整をしない。
schemaがAPI側で拒否された場合は停止して切り分ける。予備枠を使った変更・診断は本比較と分け、都合のよい結果へ差し替えない。

主比較のgoldは変更せず、2反復で分類176・結合78・行14を採点する。拒否・未出力・未対応も分母に残す。
記号optionsは既存goldにある値を追加の独立指標として照合する。その他の全セル注釈を加える場合は、画像から応答非依存に確定して送信前に凍結する。
H1/H2は既に結果を見ているため、新しいholdoutや未知帳票とは呼ばない。入力にgoldを含めない。

| 採用条件 | 判定方法 |
| --- | --- |
| 不変条件 | 物理セル/ページ誤対応、入力重複の採用、手動上書き、行の黙った欠落は0件 |
| 重点品質 | 分類148/176、結合45/78、行14/14を下回らない。同時比較Aが上回った指標はその値を下限とする |
| 表・記号 | T2は両反復で正しい重点3行と21結合。H2は両反復で記号4行、行＋列の4結合、記号optionsを満たす |
| 利用可能な案 | 10試行中8以上で、共通compilerを通る意味単位がある。全ignoredや分類だけを成功数に入れない。10/10を目標とする |
| 速度 | 上記品質を満たした上で、検証済み意味案の中央値10.138秒以下を目標とする。未達なら採用を確定せず、速度/品質の差を報告する |
| 実際の完了 | 代表帳票3種以上で、人が手直しして正しく保存・再読込できる。AIあり/手動のみの操作数と完了時間を順序を入れ替えて比較する |

「最初のSSE」「provider完了」「案の検証完了」「画面表示」「人の保存完了」を個別記録する。
全試行の終了時間、成功件数、失敗の所要時間、期限内に有用な案が出た割合を併記する。成功例だけの中央値を全体改善に見せない。
UIのローカル反応200ms以内とHTTP受付200ms以内は別目標。SSEをJSONとしてclone読取する旧計測器を使わず、Content-Typeに従って計測する。
少数反復からp95や保証値を作らない。人の手直し時間は現時点で未測定であり、モデル応答時間から推定しない。

## 12. フェーズと担当モデル

| フェーズ | 開発モデル / 強度 | 作業と終了条件 | 実API枠 |
| --- | --- | --- | ---: |
| 8：修正設計（今回） | Astra / XHigh | 原因訂正、v2契約、共通APIと評価境界を定義。製品実装なし | 0 |
| 9：比較試作 | GPT-6.1 Sol / High | 製品外でB/Cを試作し、専用コピーのcompilerでdry-run。固定30試行を測定。製品組込前に結果を残す | 初回36以内 |
| 10：採用審査 | Astra / XHigh | 保存応答と実装を独立再点検。勝者・未解決・契約を確定。不合格なら製品実装へ進まない | 原則0 |
| 11：製品実装 | GPT-6.1 Sol / High | 共通API、解析、画面、型を統合。保存応答のreplay、短いHTTP境界、build | 原則0 |
| 12：監査・回帰 | Astra / High | identity、記号、行見出し、revision、undo、遅着、journal、OCR/出力対応と重い関連回帰 | 原則0 |
| 13：実利用受入 | GPT-6.1 Sol / High | 採用方式5帳票×2反復を再測定、手修正/保存/再読込。必要な音声試験は利用者を呼ぶ | 初回20以内 |

各区切りで停止し、モデル切替後の指示で次へ進む。フェーズ9の試作は製品外に置くが、表現を評価するための限定compiler拡張もそこで必要となる。
重いテストは12へ集約する。ただし、11でも新しい保存境界を未検証のまま通さず、変更前後の短い契約検証を実行する。
次工程以降の初期計画枠は計56回。現在の残234回に収まり、全枠消費時の残りは178回。各工程の開始時に実台帳を再確認する。
通常移行・公開の完了をこの計画で先取りしない。受入結果と具体的な切替内容を確定してから扱う。

## 13. 今回の成果物・検証・限界

製品外の `validate_design.py` で **31件のオフライン確認**を実施した。
schema整合、未知ref、重複、行幅、親、未出力unknown、明示ignored、JSON重複キー、記号表の表現、行別ラベル展開を確認した。
通常のtext例だけを現v1 validatorへ変換し、既存の境界に照合した。記号表はv2で表現できる一方、現v1では表現できないことを確認した。
これはv2の正規化/部分成功/compiler/UIの完成、実APIのschema受理、モデル精度、速度向上を証明しない。これらは9以降で検証する。

変更は本設計、5個の設計JSON、フェーズ7の誤記訂正、AGENTS/Safety Contractの現在地更新に限定する。
`finish_audit.py` と `git diff --check` が成功した。開始時から両repository HEADと製品ソース、両通常DB各37表、API台帳を保持し、通常機能の無効を確認した。
通常data 863ファイルと781 artifactのSHAを照合した。`score-recheck.json` では主比較の重点採点を保存応答から再計算し、既存集計が変わらないことを確認した。
記録は `D:/llm-ocr-archive/template-ai/20261011-phase8-design`。既存OCR contractを維持した。製品コードと通常データは今回変更していない。
過去のbackend全件の既存23失敗/34収集エラーを解消したとは扱わない。今回全件テスト・実画面・実マイクは実行していない。

公式仕様確認（2026-10-11）：[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[Decisions](https://developers.openai.com/api/docs/guides/decisions)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。
Lunaの画像入力/Structured Outputsを用い、固定候補選択と自由な意味構造生成を区別する。schema準拠だけでは意味的な正しさは保証されない。
