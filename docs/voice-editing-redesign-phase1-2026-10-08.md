# 音声編集・Decisions・AI向けAPI再設計：フェーズ1 設計契約

2026-10-08。実装基準 editing.v2 / decisions-first-v1。対象は C:/Users/hanam/Desktop/llm-ocr-deploy。
利用者の「Decisions優先＋複雑時だけResponses」「表選択は任意」「未選択時は直近表を優先」
「全変更セルを同時強調」を統合しました。停止したResponses単独草案を本書で置き換えます。
**フェーズ1は設計文書・Schema確認までです。製品実装・設定・DBの切替は未実施です。**
開始基準は [フェーズ0](voice-editing-redesign-phase0-2026-10-08.md)、機械可読契約は
[manifest](contracts/editing-v2/manifest.json)です。以後の工程は本書§13の7段階を優先します。

## 1. 確定事項と変更境界

応答速度を最優先に、取得中から文字起こしを進め、対象情報を事前に用意します。
明確な変更は保存してから「変更しました」と通知します。通常の変更承認は挟みません。
不足情報がある場合だけ変更群全体を保留し、元の値・解決済み対象を保持して不足部分を質問します。
最大180秒・50操作を一つの指示とし、途中segmentごとに保存しません。

- 連続音声は開始／停止だけで継続訂正できます。選択セルへの局所録音、手動描画・接続・意味編集は残します。
- 表の手動選択は任意です。選択中の表は明示scopeとし、AI枠や閲覧scrollから変更しません。
- AI枠は候補／解決済み未保存／保存済みを区別します。複数セルは同時強調し、順に巡回しません。
- 読み上げは無効の現仕様を維持します。通知・不足質問・字幕は画面に表示し、追加の発話で回答できます。
- 互換API・旧会話loopを温存せず、業務値、物理セル対応、revision、receipt、履歴を保持します。
- AI向けAPIはアプリ内OpenAI連携から利用する型付き編集境界です。外部ChatGPTへのMCP公開・クラウド公開は今回追加しません。
- 未対応のpaged音声編集はcapabilityで拒否します。単一ページへ暗黙に縮退しません。

名前の表記差を扱うことと、任意の値を補ってよいことは別です。
「名号」が誤認識されてもscope内の候補から選べますが、欠けた60000を現在値から捏造しません。
発話終了後の保存・preview反映を測り、処理中表示だけの高速化を成果に数えません。

## 2. 責務と依存方向

処理順は「取得→全文の組立→経路選択→Decisions又はResponses→共通検証→原子的保存→receiptと表示」です。
手動操作はAIを経由せず共通検証へ入ります。
コードの依存は adapters → application → domain。applicationがportを所有し、infrastructureが実装します。
domainへHTTP、ORM、OpenAI SDK、DOMを持ち込みません。

| 配置案 | 責務 |
| --- | --- |
| backend/app/editing/domain/ | Selector、ValueIntent、BoundOperation、ChangeSet、数値表現、所有・曖昧性・変更群の検証 |
| backend/app/editing/application/ | SubmitCommand、ResolveInstruction、AnswerQuestion、ExecuteChangeSet、Cancel、Undo、scopeと実行権限 |
| backend/app/editing/infrastructure/ | repositories／unit of work、既存補正・authoring接続、volatile保留store、OpenAI adapters |
| backend/app/api/v2/ | 認証、HTTP/SSE/WS、DTO変換。モデルloopや保存判定を置かない |
| frontend/src/editing/ | 下書き、command、receipt復旧、preview、AI focus、scroll policy、対象DOM登録 |
| frontend/src/assistant/ | mic、字幕、VAD、取得世代、指示終端。業務保存を所有しない |

主なportはDecisionSelector、TextPlanner、CatalogReader、CommandRepository、PendingInstructionStore、
MutationUnitOfWork、Transcriber、Clockです。
巨大serviceの単純な移動やv1全体を通す恒久ラッパーではなく、既存の実保存部分をUoWへ接続します。
手動・Decisions・Responsesは同じBoundOperationとcommitを使い、保存ルールを複製しません。
操作ごとのrepository、汎用event bus、外部queueをこの規模で必須にはしません。

現状のconversation.pyは複数モデル要求を行い、editing/service.pyには検索・保存・undoが集中しています。
stream_service.pyは30秒上限、context DTOには選択表がなく、画面のセル選択とAI表示も未分離です。
frontendだけではAI往復数・原子的保存・質問の保持を変えられないため、これらのbackend境界が変更対象です。
OCR読取provider・画像前処理・受付snapshot・出力bindingの再設計は含めません。

## 3. DecisionsとResponsesの分担

2026-10-09追記：[フェーズ3](voice-editing-redesign-phase3-2026-10-09.md)で接続を実装した。
以下の閾値とreasoning=noneは初期案。限定した正解付き検証に基づき、完全一致の閾値と意味付けの推論設定を
policy `decisions-first-v2` へ調整した。近似閾値・構造的一意性・要求回数・保存契約は維持する。

### 3.1 APIの事実と要求数

公式Decisionsはpredicate／choice／scoreを返し、choiceは2〜255候補から選びます。
text／imageが入力で、音声や任意の編集JSON生成には使えません。本機能はtext＋choice＋gpt-6-lunaを使います。
独立した質問は一要求にまとめられます。任意の構造化変更はResponsesへ渡します。
[Decisions仕様](https://developers.openai.com/api/docs/guides/decisions)、
[create仕様](https://developers.openai.com/api/reference/resources/decisions/methods/create)

probability／confidenceは正しさの保証ではなく、正解付き実例で閾値を調整します。
公表速度は文之助の音声終了→保存表示の保証ではありません。利用可否・品質・遅延は未実測です。

| 入力 | 初期経路 | AI要求数の上限 |
| --- | --- | --- |
| 手動・候補クリック補完・undoボタン | 共通commandへ直接 | 0 |
| 明示的な単純値変更、独立した最大4節 | Decisionsで有限候補を選択 | 通常1 |
| 自由文、複雑な言い直し、依存操作、5節以上、自由な意味付け | Responsesへ直接 | 1 |
| Decisionsが複雑と判断／単純経路で全内容を扱えない | 全文をResponsesへ一度だけ渡す | Decisions1＋Responses1 |
| 複数対象候補／欠落値 | 必要slotだけ質問 | 自動再試行0 |
| timeout・認証失敗・拒否・壊れた応答 | 未保存で理由表示 | 自動再試行0 |

Decisions→Responses→Decisionsと循環させません。障害を複雑な指示と見なして別APIへ再送しません。
質問への回答は新commandで、同一instructionの保存は最大一回です。

### 3.2 候補生成と選択

FastPathCompilerは短い明示命令の構造と値の範囲だけを抽出します。
行列番号、数値、引用文字列、消去、節の境界を扱い、帳票・列名ごとの分岐や日本語全文parserを作りません。
全文を扱えたことが確定しない入力や自由な文字値はResponsesへ渡します。
局所録音は選択セルへ限定します。通常の連続音声では選択セルを必須にしません。

scope・行を先に適用し、その範囲内の列名／意味／別名を候補にします。
旧0.5／0.66の文字類似率をDecisions候補の必須前処理にしません。正解候補を認識文字差だけで先に落とさないためです。
変更後の値や現在値を欄名の検索根拠に使わず、帳票別の名称辞書をハードコードしません。

候補はサーバー側のBoundOperation案とopaque choice IDの対応で、値は抽出済みValueIntentへ束縛します。
モデルはchoice IDを選び、保存path、revision、任意の値、座標を生成しません。
一質問あたり実変更候補は最大252件＋clarify／responses／no_actionの3候補。
最大4個の独立質問を一要求にまとめ、全回答を照合後一つの変更群として検証します。
一節でも保留なら他節だけ保存しません。回答が混在して全体意図が不明なら全文をResponsesへ渡します。

255を超える候補を上位だけ切り出して一意と判断しません。構造的に完全な範囲へ絞れなければ、
compact catalogでResponsesへ渡すか表／行を質問します。候補一覧を全セル直積にしません。
質問／候補IDの重複、未発行ID、回答欠落、候補外選択、確率集合不一致は拒否します。
candidateSetId、catalogRevision、scopeDigest、command bindingに応答を束縛します。
各節の範囲はUnicode code pointの半開区間で保持し、値を他節から取り違えないことを検査します。
[decisions.schema.json](contracts/editing-v2/decisions.schema.json)は内部snapshotと正規化応答の契約です。
OpenAIのwire形式ではなく、Decisionsに独自JSONを生成させるschemaでもありません。

暫定policy decisions-first-v1は選択確率≥0.98、次点との差≥0.20、confidence≥0.90、
完全な候補集合、構造的一意性、値検証を全て要求します。未校正の開始値であり品質合格の証拠ではありません。
閾値未達は対象候補を質問します。全表で同名列等が複数適合する場合は最高scoreでも自動適用しません。
フェーズ3／7で誤保存と過剰質問を測り、policy version付きで必要な調整を行います。

### 3.3 複雑な指示・意味付け

Responsesはgpt-6-luna、reasoning.effort=noneを初期値とし、strictなsubmit_edit_planだけを公開します。
parallel_tool_calls=false、store=false、指定tool強制、SDK retry=0です。
tool引数は提案で、サーバーが検証して実行します。成功通知のための追加モデル要求はしません。
全objectでadditionalProperties=false、全property required、未指定相当は明示nullとします。
Lunaは音声非対応なので文字起こしは別に必要です。
[Luna仕様](https://developers.openai.com/api/docs/models/gpt-6-luna)、
[Function Calling](https://developers.openai.com/api/docs/guides/function-calling)

Decisions timeout3秒、Responses timeout15秒、出力8192 tokensを開始値とします。
本文20000文字、compact catalog推定12000 tokens、1群50操作まで。
超過時は範囲を質問し、項目や発話末尾を黙って切り捨てません。予算不足でも一部を先に適用しません。
catalogはrevision／scopeごとに事前生成・cacheし、schemaを対象ごとに再生成しません。
表・列・行数・疎な割当を短く投影し、全layout・全文履歴・全セル値を反復送信しません。
必要な同scopeの直前receiptだけ加え、欄名・印字・別名・字幕を権限や命令として扱いません。
自由な新名称、関係追加・解除、表・見出し・mark等の意味付けはResponsesで22操作へ変換します。

## 4. 対象範囲・値・訂正根拠

### 4.1 表の決め方

[context.schema.json](contracts/editing-v2/context.schema.json)のselectedTableIdは手動選択、
recentTableIdはサーバーが成功receiptから管理する履歴です。モデルはこれらを書き換えません。

| 状態 | 対象表 |
| --- | --- |
| 手動選択あり | 選択表に限定。別表を明示した発話ではscope切替を質問 |
| 手動選択なし、発話に表名あり | 現在resource/page scope内の明示表。一意でなければ質問 |
| 手動選択なし、表名なし、直近表あり | 直近表。列がなければ別表へ自動移動せず質問 |
| 手動選択なし、表名なし、直近表なし | 許可scopeの全表から一意に特定できた場合だけ適用 |

直近表は同session・同resource/page scopeの成功変更群から更新します。
複数表への変更群ならnull、外部revision変更・scope変更・session終了でも消します。
選択解除後は同scopeの有効な成功receiptからのみ引き継ぎ、クリック・AI枠・scrollを履歴にしません。
単体欄／テンプレート意味付けscopeと繰り返し表のscopeを混同しません。

### 4.2 selectorと行列

[planner.schema.json](contracts/editing-v2/planner.schema.json)はResponses用です。
decisionはchange／clarify／undo_previous／retarget_previous／no_action。
changeはselectors、22種類のoperations、操作と同数・同順のoperationEvidenceを持ちます。
selectorはcatalog／query／record_cell／recent_target。発行refの所有・種類・capability・元発話を照合します。
新しい意味等のlocalRefは同変更群内で先に宣言したものだけ参照できます。

行番号は1始まり、空行を含む保存表示順です。列番号はデータ列のみで、行番号欄・見出しを含めません。
表ID／rowId／columnId／fieldIdをidentityとし、配列indexを保存IDへ変換しません。
record_cells.pyの保存済みreviewStructure投影を利用し、検索用名称からOCR mappingを再生成しません。
重複列名、未割当、範囲外、重複field所属は拒否又は質問します。
連続モードの閲覧セル選択は音声scopeを変更しません。「そこ」は同scopeの直前成功対象、一つに決まらなければ質問します。

EvidenceのturnIdは元指示又は有効な回答、quoteはその本文に含まれることを要求します。
quote一致だけで意味的正しさを証明せず、明示座標・候補集合・値の対応を検査します。
旧類似閾値をResponsesの必須通過条件にはせず、言語解釈と構造的一意性を分離します。

### 4.3 音声値

| ValueIntent | 処理 |
| --- | --- |
| text | 文字列保持。引用された「6万」、6万円、6万kgも文字列 |
| digits | 数字トークン全体をASCII化。ゼロゼロ七八九→00789 |
| integer | 符号・小数・単位のない数量。六万／６万→60000 |
| blank | 明示消去のみnull |
| missing | 空欄化せず質問 |

現行の列には汎用的な数値型がありません。「価格」という名前から型を推測しません。
発話の形式と既存identityのdigits／exactLengthを根拠に検証し、桁を補完しません。
readingMode=numberは自動採番で、数字転記とは別です。
純粋な数値発話はモデルがtextを選んでも同じ規則で変換し、明示引用・単位表現を優先します。
各値は自分の節／Evidenceへ束縛し、別セルの値を流用しません。
手動値、意味名、名称、既存OCR値はこの正規化へ通しません。

## 5. 共通22操作と手動専用操作

[operations.schema.json](contracts/editing-v2/operations.schema.json) は検証後の共通操作型です。
既存22操作の意味と有効な未完成DRAFTを維持します。保存時のidentity／mappingの規則をJSON Schemaだけで代用しません。

| 操作 | 主な契約 |
| --- | --- |
| ocr.set_value | 未確定単一OCRの一つの値。sourceRegion／bbox／欄集合不変 |
| cell.set_role | 明示セルの分類。依存する意味／mark／表に矛盾が残る変更群は拒否 |
| cell.rename | input表示名／既存見出し名。印字や物理IDを改変しない |
| cell.set_reading | text／sequence。sequenceは自動採番で、数字転記とは別 |
| meaning.create | localRefで新しい意味を作る。明示名称・別名だけ |
| meaning.update | 所有意味の名称・別名を変更 |
| meaning.delete | 参照整合性を検査し、未解除依存を黙って消さない |
| meaning.bind | 意味と指定input／labelを接続。所有・重複path・行所属を検査 |
| meaning.unbind | 指定relationを解除。曖昧な名称から他relationを解除しない |
| heading.create_virtual | 明示anchorから表示位置を決定。モデルに新座標を渡させない |
| heading.update | 所有見出しの名称変更 |
| heading.attach | 子と親を接続。単一親と循環拒否 |
| heading.detach | 指定した親接続を解除 |
| heading.delete_virtual | 仮想だけ削除可能。物理見出しを削除する代用にしない |
| records.define | 明示列・行の所有集合から表を定義。空表もDRAFTとして許可 |
| records.replace_rows | 指定表の行を明示集合へ変更。各列数とcellの重複を検査 |
| records.configure | 名称・行キー規則等。意味解除や名前なしを未完成DRAFTとして許可 |
| records.dissolve | 明示した表構造の解除。過去OCRの表は変更しない |
| mark.configure | markの意味・選択方式・選択肢。既存IDか新localRefのどちらか一つ |
| mark.clear | 明示セルのmark設定を解除 |
| identity.configure | テンプレートが選ぶ識別項目・桁条件・表示順。共通の10桁や一意IDを強制しない |
| document_name.configure | 所有fieldで書類名規則を指定。読み取り中の名称を推測上書きしない |

1変更群は最大50操作、既存の展開件数・列数・行数・mark上限も適用します。
LocalRef一意性、RecordRowのgroup/localRef排他、MarkSet/Optionの既存ID/localRef排他、
引用Evidence、selector参照、operationEvidence件数はdomain検査です。Schema通過だけでは保存しません。
同一対象への競合する複数書込みは「500、いや501」のような明示訂正を一つに解決してから保存します。
順序が不明な複数書込みは最後の配列要素を自動採用せず質問します。

手動の描画・移動・分割・結合・ページ編集は既存manual authoring adapterで扱います。
新APIの手動専用 `manual-authoring` endpointから同じExecuteChangeSet／receiptへ入ります。
元DTOは `ManualDraft`、revision付き保存と物理検証を維持し、外側の契約をediting.v2へ置き換えます。
モデル用schemaにはManualDraft／layout／自由なJSON patchを含めません。
手動の意味変更は22操作へ変換します。UI側に独立した意味付け保存ロジックを増やしません。

## 6. HTTP・イベント・要求の同一性

[commands.schema.json](contracts/editing-v2/commands.schema.json)、
[events.schema.json](contracts/editing-v2/events.schema.json)、[例](contracts/editing-v2/examples.json)を併用します。
このフェーズのSchemaは実装前の契約です。稼働中APIへ導入済みではありません。

| API | 用途 |
| --- | --- |
| POST /api/v2/editing/sessions | resource／view／入力mode／scopeを所有sessionへ束縛。認証tokenを発行 |
| GET /api/v2/editing/sessions/{id}/context | revision、capabilities、catalog、選択、直前receipt、limitsを一括取得 |
| PUT /api/v2/editing/sessions/{id}/context | 比較付きscope／選択／interactionEpoch更新と旧実行権限の失効 |
| POST /api/v2/editing/sessions/{id}/commands | 全文指示、質問回答、手動22操作、undo、cancelの一つの入口 |
| GET /api/v2/editing/sessions/{id}/commands/{requestId} | 同一要求の状態、最終イベント、receiptを照会 |
| GET /api/v2/editing/sessions/{id}/events | 認証付きfetch SSE。Last-Event-IDで再開。URLにtokenを載せない |
| POST /api/v2/editing/sessions/{id}/manual-authoring | 手動専用snapshotから既存authoring検証・共通mutationへ接続 |
| DELETE /api/v2/editing/sessions/{id} | 未保存権限と音声取得を終了。確定receiptは保持 |

session/manual-authoringは既存の所有・token・ManualDraft検査を引き継ぎます。
contextは [context.schema.json](contracts/editing-v2/context.schema.json)の選択表・局所対象・世代へ置き換えます。
page scope検査を保持し、capabilitiesより広い範囲を許可しません。
このSchemaはPUTの更新bodyとGET context内のscopeContextを定義します。
GETのcatalog／capabilities／limitsは別DTOとして型から生成し、既存の所有検査を継承します。
selectedHandlesは表示中の選択集合で、localTargetHandleは局所録音の一対象です。
連続OCRの閲覧選択はscopeDigestを変更しません。局所録音、又はテンプレートで「選択した欄」を編集する指示は選択snapshotへ束縛します。
local modeの対象欠落／選択集合との不一致、別resourceへのcontext更新は拒否します。
commandsとmodel toolの型は別物です。ブラウザーはmodelのplanを保存APIへ転送しません。

requestは契約version、requestId、expectedRevision、contextRevision、interactionEpoch、
catalogRevision、scopeDigest、afterCommandId、commandを持ちます。
音声commandは所有utteranceIdとtranscriptDigestを指定し、サーバーの確定全文との一致を確認します。
アプリ内の自由な文字指示はtext inputとして同じ経路判定へ渡せます。文字入力を録音済みと偽装しません。
replyToはサーバーが発行したquestionIdとversionです。単なる「はい」を任意の保留指示へ割り当てません。
input.kind=choiceは候補クリックによる補完で、現在のreplyToを必須とします。任意のchoice ID単独では保存しません。

HTTPは短い受付transactionの後に202とacceptedイベントを返し、サーバー所有taskが続行します。
acceptedのcommandId／requestIdは保存結果の照会にも使います。結果はSSEから届きます。
実行中にHTTP/SSE接続が切れたことだけでは保存を取り消しません。明示cancelと通信断を区別します。
認証・形式・予算・受付上限の拒否は202前の4xx/5xxとし、受け付けたふりをしません。

同じsession＋requestIdの再送は、ownerとpayload fingerprintを先に照合して元の状態を返します。
同じキーに違うpayloadは409です。現在のrevision／期限の検査より先に、所有する成功receiptを復旧します。
モデルを再実行して別の要求へ変換しません。requestのfingerprintと検証済みchangeSetのdigestは別に保存します。
テキスト全文はfingerprint計算に使いますが、本文は通常DB・ログ・localStorageへ永続化しません。

afterCommandIdは同sessionの直前commandだけを指せます。
同じcontext/interactionEpochで、先行commandの成功receiptだけがrevisionの前進を説明できるときに限り、
後続の基準をそのafterRevisionへ進めます。他タブ、手動変更、scope変更をこの仕組みで越えません。
先行が質問・失敗・取消・競合なら後続を自動実行しません。全文を残して新しい状態を案内します。
質問回答はreplyToで明示し、単なるqueueの次要素を回答と解釈しません。

appliedは業務データ・revision・ResourceOperation・MutationRequestの成功保存後にだけ返します。
通知はモデル自由文ではなく、サーバーreceiptの対象名から生成します。
previewはそのrevisionを下限として一度取得します。取得失敗時は「保存済み・表示更新失敗」とし、再保存しません。
保存後のGET往復は残ります。動的layout/FieldReadingの別patch形式を重複保守するより、現行の正本表示DTOを優先します。
新しい手入力が始まっていれば、GET結果を下書きへ上書きせず保存版と編集中の内容を保持します。

イベントはaccepted、target_focus、applied、clarification_required、no_action、conflict、failed、cancelled、expired。
target_focusは一時表示用で成功receiptではありません。現在focus snapshotはcontextから復元できます。
applied.receipt.focusは保存後のviewRevisionと安定anchorを持ち、期限付きhandleだけで画面を探しません。
イベント順序はsession内sequence、実行の世代はcommandIdで判定します。古いイベントでUIを巻き戻しません。
成功／失敗の最終イベントmetadataとreceipt参照は永続化し、SSEは再取得可能な通知手段とします。
字幕delta・モデルの出力途中・UIの処理中表示は業務イベントの成功へ昇格させません。

## 7. 音声と長文の契約

ストリーミング文字起こしは `gpt-live-transcribe` を初期候補とします。
24kHz PCM、turn_detection=null、delay=low、languages=[ja]を開始値とし、アプリがcommitします。
scope内の表名・列名・意味名・別名をkeywordsへ入れ、scope変更は次の指示境界で反映します。
これは認識ヒントであり強制変換ではありません。gpt-transcribe committed-turnとの比較は必要時のフェーズ4に限定します。
providerのitem IDにより字幕と確定文字列を整列し、completed到着順だけで全文を組み立てません。
このモデルはserver_vad/semantic_vad非対応です。通常のRealtime会話向け設定を流用しません。
[Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription)、
[VAD仕様](https://developers.openai.com/api/docs/guides/realtime-vad)

`AudioSegment`（転送単位）、`Utterance`（一つの指示全体）、`Command`（編集要求）を区別します。
連続モードの開始／停止は各一回。局所モードは開始／終了送信で明示的に指示を区切ります。
両方とも同じutterance assemblerと編集use caseを使います。TTSは今回有効化しません。

- Utteranceは最大180秒、PCMは最大8,640,000 bytes。取り込み時のscopeとgenerationを固定する。
- 転送segmentは最大30秒で分割可能。各segmentの確定は編集を起動せず、manifestへ追加する。
- 最終manifestの順序、重複、欠落、全itemのcompletedを検証して全文digestを固定する。
- 無音、空の全文、segment欠落、上限超過、切断で未完了の音声は編集しない。先頭の分だけ保存しない。
- 音声bytesは処理中の上限付きメモリだけ。全文は指示／質問の期限内のメモリだけ。終了後に破棄する。

終了判定は交換可能な `UtteranceBoundaryPolicy` とし、フェーズ4の開始値を次に固定します。
通常は最後の発声から1200msの静寂を指示終端とし、300ms程度のVAD区切りでは終端にしません。
末尾が未完の言い直し・列挙であると判定した場合は最大3000ms待ちます。局所録音の終了送信は即座に終端です。
正規表現の全文命令parserは追加せず、終端候補の分類に限ります。判断不能な終端から部分操作を推測しません。
1200msを超えた通常の間は別指示になる場合があります。長い沈黙でも常に同じ指示として保持する保証はしません。
この限界は実マイクの長文・言い直しで評価し、精度と待ち時間の両方を記録して調整します。

発声が止まってから1秒以内に「音声を受信・指示を整理中」という正確な処理状態を表示します。
これはHTTP acceptedや変更成功とは区別します。§12の保存・反映時間が改善しなければ速度要件達成とはしません。
新発話開始をpotentialContinuationとして通知し、同世代の未commit commandをfenceで保留します。
続き／訂正なら旧planを失効させて全文を再構成し、独立指示なら保留を解きafterCommandIdで順序を守ります。
認識失敗では黙って再開しません。通知がcommitより遅い場合は下記の保存済み訂正として扱います。
既にcommitした変更は遅い音声開始通知で巻き戻さず、直前receiptに対する訂正として扱います。

接続300秒／累計1200秒の予算管理は継承し、残り接続時間が180秒＋終了待ちに足りない場合は、
無発声の境界で予約付きの接続更新を行います。発話を開始してから時間切れで途中を保存しません。
これは通常の接続更新として回数を記録します。障害後の無制限再接続・録音の勝手な再開はしません。
更新失敗や累計上限では停止理由を表示し、取得済みの未完指示は保存しません。
この接続更新は旧「自動再接続なし」からの意図した変更で、実装・予算検証はフェーズ4です。

## 8. 聞き返しと訂正

保留情報の正本はapplicationの `PendingInstruction` です。
originalInstruction、時系列の質問回答、partialPlan、確定済みbindings／values、missingSlots、
session/context/interactionEpoch、baseRevision、questionId/version、期限を持ちます。
原指示を要約だけへ置き換えず、質問回答を元指示全文とともに次の解釈へ渡します。
モデルに再解釈させても、回答で変更を明示していない既知の対象・値を落としたり変えたりしたら保存を拒否します。

例：「1行目の体重を500、2行目の架空列を60000」では、どちらも未保存にします。
「価格だよ」への回答後、元の500と60000を保持した2操作を一つのtransactionで保存します。
値が欠けた質問への「はい」「それでよろしく」を数字として保存しません。
表→列→値と複数回質問しても、すでに回答したslotと元の値を消しません。
model側clarifyがpartialPlanを返せない場合も全文を保持し、回答後のplanと元指示の根拠を照合します。

質問は一度に一つ。残るmissingSlotsを順に処理します。
scope切替質問への明示回答では、元の値を保持した同instructionを新scopeへ再束縛した事実を記録します。
旧plan／候補IDは失効させ、新catalogとrevisionで全体を再解決します。一般の手動scope変更による旧指示失効とは区別します。
候補IDを選ぶ明示回答など、同一性が決定的な補完は追加モデル要求なしで解決できます。
自由発話の回答は通常の経路判定でDecisions最大1回＋必要なResponses最大1回を使用します。新たな変更・取消を含む回答は、単なるslot補完へ押し込みません。
質問の期限は表示から300秒、指示全体は最初の受付から最大900秒です。
300秒の質問期限を延長するのは有効な回答で次の質問を発行したときだけです。無通信やGETでは延ばしません。
期限後・再起動後は未保存指示の失効を明示し、古い回答を別指示に適用しません。

「取り消して」は直前群をundoします。「違うよ」だけでは何を直すか質問し、保存値を勝手に取り消しません。
「違う、501」は直前対象が一つに決まる場合に値を訂正します。複数なら対象を質問し501を保持します。
「体重ではなく価格」は、直前が単一値／単一名称の変更なら元対象の復元＋正対象への同値適用を一transactionで行います。
複数変更や物理編集の対象差替えを推測しません。どの操作を訂正するか質問します。
Undoは同resourceの最後のoperation、現在revision=afterRevision、現在値一致、同scope、編集可能を要求します。
手動／他タブ編集、確定／公開を越えず、undoのundoや無制限の履歴巻き戻しは追加しません。

## 9. 状態・競合・復旧

内部command状態は ACCEPTED→PLANNING→VALIDATING→COMMITTING→APPLIED。
分岐は WAITING_INFORMATION、NO_ACTION、CONFLICT、FAILED、CANCELLED、EXPIRED、INTERRUPTEDです。
質問はそのcommandの最終応答ですが、同じinstructionは保留中です。回答は新request IDで同instructionへ束縛します。
成功変更群はinstructionにつき一つだけ。回答再送や旧質問の回答で二度適用しません。

| 事象 | 正本の動作 |
| --- | --- |
| モデル待機 | DBのwrite transactionやresource lockを保持しない |
| 保存直前 | session所有、context、epoch、期限、revision、許可操作、対象／値、実行fenceを再検証 |
| 手動入力開始 | UIのinteractionEpoch更新をサーバーへ通知。未保存音声のcommit権限を失効。手動値を保護 |
| page/scope/手動選択表/局所選択変更 | contextRevision増分と旧指示失効。新しいscopeへ旧指示を移さない |
| 連続モードの単なる閲覧選択 | 実行scopeを変えない閲覧なら無効化しない。局所録音の選択はbindingなので無効化 |
| 明示cancelとcommitの競争 | 短い同一DB transactionの順で決定。cancelが先なら未保存、commitが先ならappliedとreceipt |
| HTTP/SSE応答消失 | 元request IDで状態照会。成功receiptがあれば適用を再実行しない |
| 保存後の表示取得失敗 | 保存成功を保持し、previewだけ再取得 |
| 競合 | 元の指示／手動下書きを保持して停止。新revisionへ黙ってrebaseしない |
| プロセス再起動 | 永続receipt照合が先。未保存のvolatile指示はINTERRUPTEDにし、モデルを自動再要求しない |
| session終了 | 取得・保留・未commit権限を失効。履歴と成功receiptは削除しない |

resource単位のcommit直列化とDBの条件付きrevision更新を併用します。
session内は実行1件＋待機2件までです。容量超過は受付前に拒否し、古い指示を黙って捨てません。
未解決の質問中に無関係な新指示を保存して旧保留だけ残す動作はしません。
「それは中止、次は…」などの明示取消があれば旧保留を終了して新instructionを開始します。

## 10. 永続化・移行・旧入口

新規のcommand管理は `editing_command_requests` を設け、session/resource、requestId、入力fingerprint、
instructionId、state、fence、binding、期限、最終event／receipt参照を保存します。
全文指示、音声、未保存のmodel出力はここへ入れません。質問本文の保存は原文quoteを含まない定型文・候補metadataに限ります。
メモリに本文がある稼働processが一つだけ実行権を持ち、epoch/fenceをcommit時にDBで再確認します。
外部queueやRedisを今回必須にしません。process再起動で保留は失効し、保存済み結果はDBから復旧します。
複数worker運用へ拡張する際はこのportでvolatile本文の所有方法を交換します。RAM共有を前提に複数workerを有効化しません。

実保存は既存 `mutation_requests`／`resource_operations` と補正・authoring revisionを使います。
commandのAPPLIED遷移と成功receipt参照も同じ保存transactionに入れます。
値だけ保存してcommandが未確定になる別commitを設けません。
新契約の履歴metadataはcommandへの参照で識別し、旧operationをv2として再解釈しません。
内部保存層へのv2接続時は、旧turn token前提をadapterへ分離して共通UoWを抽出します。
旧v1 serviceを丸ごと通過させる恒久的なv2ラッパーにはしません。

通常DBには現在 `assistant_voice_v1` まで適用され、v1共通編集5テーブルは試験コピーだけにあります。
移行は通常DBコピーと試験DBコピーの両方から検証します。旧履歴、予約、参照、値、revisionを保持します。
フェーズ2では分離DBへschemaを導入し、通常DBのmigration／有効化はフェーズ7です。
切替時に旧session／未保存提案を失効させ、旧assistant proposal／conversation／batches／音声入口を廃止します。
旧receipt／監査履歴は必要な読取adapterから取得できますが、旧writeを受け入れる互換層は残しません。
旧書込み拒否と通常の手動／音声保存が同じ切替で成立することを検証します。

API予算は通常DBの共有台帳を正本とします。開始時137/196、残59。今回使用0です。
Decisions HTTP、Responses HTTP、文字起こしHTTP、WS接続、各upstream音声commitを送信前に予約します。
音声packetごとには予約せず、再送・接続更新・失敗は既存方針どおり消費します。
複数segmentに分ける長文の消費は1回に見せず、実upstream commit単位で記録します。
残り枠がない指示は送信前に止めます。追加枠は必要時に利用者へ報告します。

## 11. AIフォーカス・スクロール・表名表示

AiFocusStateはcommandId、contextRevision、interactionEpoch、viewRevision、anchor集合を保持する一時UI状態です。
手動選択、DOM focus、review warning、OCR判定、編集scopeと分離します。
モデルはDOM selectorや座標を返しません。サーバー検証済み対象をTargetAnchorRegistryで描画位置に結びます。

| 状態 | 枠と表示 |
| --- | --- |
| 対象候補・質問 | 控えめな点線＋候補表示、未保存 |
| 対象解決済み・保存中 | 青系実線＋処理中。未保存値を確定表示しない |
| 保存成功 | receiptの全変更対象を同時に緑系実線。「表名・行・列を変更しました」 |
| 失敗・競合・取消 | そのcommandの処理中枠を解除。OCR警告・手動選択は保持 |

色だけに依存せず線種・状態表示を併用し、aria-liveで最終結果を一度通知します。
成功枠は最低1500ms示した後、次の有効なfocus又は明示解除まで保持します。古いeventで巻き戻しません。
多セルは集合一つのeventとし、保存順の再生や自動巡回をしません。画面外も集合に残し表示時に枠を描きます。
対象が削除された場合は残る関連anchorだけ返し、存在しないセルを捏造しません。
anchorIdの一意性、kindとfieldId／cellIdの排他、resource/page所有、primaryAnchorIdの集合内所属を検査します。
receipt.focus.viewRevisionはreceipt.viewRevisionと一致させます。候補枠の一意性は保存許可を意味しません。

OcrReviewResultsのdata-review-pathは既存描画対応の登録入口とし、resource/page/field又はtemplate cellの安定IDを使います。
template canvasは既存の座標変換でrectを計算し、OCRのbbox・cropを変更しません。
receiptのviewRevision以上を描画後に成功枠を出します。preview取得失敗は保存済みと表示し再保存しません。

単一A→Bはoverlayを前後のrect間で約250ms移動・変形します。
文書座標を保持しscroll中はviewportへ再投影して別セルを指さないようにします。
複数集合への移動は共通枠を保持し、追加／削除を同時に短く出現／消失させます。
reduced-motionは即時表示。アニメ終了を保存や次の音声の待ち条件にしません。

scrollは最寄りのpreview containerへ適用し、header・固定通知・表名overlayを除いた可視領域を使います。
既にセルが収まれば動かさず、一部隠れれば必要最小限だけ動かします。
画面外の単一対象は中央付近へ置けますが、表名と対象が見えるなら中央へ寄せ直しません。
複数対象のunionが収まれば一度だけ表示し、収まらなければ現在見える対象を優先します。
全対象が画面外なら発話中の最初の対象へ一度移動し、残りへ巡回しません。過大セルは先頭とラベルを優先します。
手動scroll／pointer／IME／drag中は自動scrollを抑制し、終了後800msの無操作期間で最新集合だけ再判定します。
世代の変わった保留scrollは破棄し、keyboard focusやcaretを奪いません。

表名overlayは元headerが上端より上へ出て、表本体が1px以上見える間だけ表示します。
表本体が全て画面外、又は元headerが戻れば消します。横方向で見えないだけの場合は表示条件にしません。
複数表なら上端を横切る表を優先します。IntersectionObserver／ResizeObserverと一つの描画loopで判定し、
固定タイトル分のscroll余白を確保します。表示だけでselectedTableIdを変更しません。

主操作は音声開始／停止、任意の表選択、局所録音、undo、手動編集です。
大きな装飾ボタンや案内キャラクターを主役にせず、preview・対象枠・短い通知を中心に構成します。

## 12. 受け入れ条件と性能指標

以下は後続実装の合否条件であり、今回のSchema確認は製品動作の実証ではありません。

| ID | 入力・故障条件 | 期待結果／検証工程 |
| --- | --- | --- |
| C01 | 未選択、任意帳票の1行目＋列名／番号 | 固有名分岐なし、保存構造から対象解決。3／7 |
| C02 | 選択表／明示別表／直近なし／重複列 | §4の優先順、scoreだけで曖昧な全表検索を保存しない。3 |
| C03 | 名号→名古／目以後う／5、売却子牛→売却更新 | 候補と値を分離、適用／質問の正解を個別定義。3／7 |
| C04 | 2操作中の列が不明→列だけ回答 | 元500／60000を保持して一括保存。4 |
| C05 | 表→列→値と連続質問、値質問に「よろしく」 | 既知slot保持、値を捏造しない。4 |
| C06 | 00789、六万、引用6万、6万kg | 00789／60000／6万／6万kg、入替えなし。2／3 |
| C07 | 30〜180秒・短い間・500→いや501 | 全文一群、途中500を保存しない。4／7 |
| C08 | segment逆順／重複／欠落／切断／超過 | 再整列又は全件未保存。4 |
| C09 | 保存直前の続き／手動入力／表scope変更 | fenceで旧plan拒否、既commitはreceipt訂正。2／4 |
| C10 | 同一再送／異payload／応答消失 | 同receipt／409／照会復旧、二重保存・AI再送なし。2 |
| C11 | DB故障／再起動／cancel競争 | 全rollback又は成功receipt。2 |
| C12 | 値訂正／対象訂正／undo／外部revision | 原子的変更、競合を越えない。2／4 |
| C13 | DRAFT意味作成・接続・表・mark・見出し | 手動とAIで同じ22操作。3／5 |
| C14 | 手動描画／局所録音／IME／drag | 操作維持、scope限定、遅いGETで下書き消失なし。5 |
| C15 | 候補内の命令／他owner ref／未知op | 権限拡張・任意path・誤対象保存なし。2／3 |
| C16 | ACTIVE／確定済み／未対応paged | 既存capabilityで拒否、縮退なし。2／7 |
| C17 | 196／197回目、複数audio commit | 予約成功／送信前拒否、予算復活なし。3／4 |
| C18 | 255上限／不完全候補／未知choice／拒否 | 黙った切捨て・修復loop・部分保存なし。3 |
| C19 | 単純4節／依存節／長文／fallback | 経路・回数上限、先行一部保存なし。3 |
| C20 | A→B、多セル、古いevent | 移動枠／同時枠、scopeと手動選択不変。5 |
| C21 | 画面外／既可視／手動scroll／reduced-motion | 必要な一度のscroll、操作を奪わない。5 |
| C22 | 表header画面外／表本体全て画面外 | 表名overlay表示／非表示。5 |
| C23 | commit後のpreview取得失敗 | 保存成功保持、再保存なし、revision一致後に枠復旧。5 |

speech_end、feedback、utterance_sealed、transcript_final、accepted、decision_start/end、
responses_start/end、commit、previewを計測します。ブラウザーはperformance.now、サーバーはmonotonic clock。
両者の時計を直接引かずcommand/request IDと各durationで対応させます。
API件数・tokens・操作数・音声長・候補数・scope・失敗種別を併記し、通常ログへ本文や音声を残しません。

| 区間／品質 | 初期目標 |
| --- | --- |
| 発声終了→正確な処理状態 | p95≤1秒。保存成功とは区別 |
| 確定字幕→preview、単純Decisions経路 | p95≤2秒 |
| 発声終了→preview、単純経路 | p50≤2秒、p95≤4秒 |
| 発声終了→preview、最大180秒／複雑指示 | p95≤8秒 |
| シナリオ内の誤対象保存・値入替え・部分保存 | 0件 |
| 明確な単純指示の追加質問なし成功 | 正解付き集合で95%以上を目標、n／内訳／失敗を併記 |

設計目標であり未達成です。n<20ではp95の合否を断定せず件数・中央値・最大と不足を報告します。
n≥20でも小標本の参考値としてwarm/cold・ネットワーク・経路別件数を示します。
成功例だけで速度を集計せず、不要質問・誤保存・無音・切断・認識欠落を別々に数えます。
旧resolve中央値8.156秒は33件の混在履歴で、音声終了→previewとの直接比較値ではありません。
同一条件の基準を後続工程で作り、支配的な区間を確認して最適化します。

## 13. 実行フェーズ・モデル・終了条件

開発用Codexモデルとアプリ内Luna／文字起こしモデルは別です。
各工程を終えたら停止し、利用者のモデル切替と実行指示を待ちます。

| フェーズ | 開発モデル・強度 | 範囲／終了条件 |
| --- | --- | --- |
| 1 設計契約 | GPT-6-Astra / xhigh | 本書、Schema、scope・UI・速度基準、開始退避・保持確認。製品コード変更なし |
| 2 共通command・v2 API | GPT-6.1-Sol / high | domain/application/adapter、context、22操作、原子的保存、receipt、競合・再送。コピーDBの短い検証 |
| 3 Decisions・Responses | GPT-6-Astra / xhigh | 候補pool、値抽出、1要求選択、fallback、意味付けplan、予算。正解付き例で経路・誤保存・過剰質問を評価 |
| 4 音声セッション | GPT-6.1-Sol / high | streaming、180秒、順序、終端・割込み、保留slot、連続訂正・局所録音。制御eventで検証 |
| 5 画面・意味付け接続 | GPT-6-Astra / xhigh | 任意表scope、同時枠、移動・scroll・表名overlay、手動共存。対象build／実画面 |
| 6 統合レビュー・性能調整 | GPT-6.1-Sol / high | 二重責務、余分なAI往復、cache失効、保存／表示競合。計測に基づく限定修正 |
| 7 最終検証・切替 | GPT-6-Astra / xhigh | 必要な重い回帰、保存response/crop/XLSX、利用者実マイク、速度・精度、DB移行、旧write撤去、実入口 |

短い契約／API／状態確認を先に行い、重い全体検証は7へ寄せます。
旧全件の既知23失敗／34収集エラーを消して成功扱いにせず、維持対象suiteと未検証範囲を明記します。
最小の保存境界確認を最後まで放置する意味ではありません。実マイクは7で利用者を呼びます。
人工音声だけで実マイクを完了扱いにせず、回帰があれば通常切替を完了扱いにしません。

残59回の目安は3で最大16、4で最大8、6で最大6、7へ29確保です。追加許可ではなく196上限内の配分です。
未使用分は後工程へ残します。WS接続・segment・Decisions／Responsesを個別に数え、29回＝29発話とはしません。
標本数が不足する場合は必要数と根拠を報告し追加枠を求め、上限を勝手に増やしません。

## 14. フェーズ1の成果物と検証境界

本書と6つのJSON Schema、manifest、正常／拒否例20件を保存しました。
現在の22操作と照合し、Decisions内部snapshot、任意表context、AI focusイベントを明示しました。
設計用の固定成果物であり、フェーズ2以後はv2型から生成して契約差分を確認し、型定義へ正本を移します。
v1 Pydanticへの永久依存や手編集Schemaと実装の二重管理は残しません。

開始ソース825件／業務ファイル855件、通常31テーブル・試験36テーブルをread-onlyで退避・照合しました。
証拠は D:/llm-ocr-archive/voice-assistant/20261008-redesign-decisions-phase1。
停止時の旧草案はsource-before、本工程差分とSchema検査・文書参照・保持結果は同folderへ保存しました。
API開始137/196、残59、今回の外部AI呼出し0です。

Schema 6件、object形式158件、参照208件、正常／拒否例20/20、22操作集合、文書内local link 10件を確認しました。
git diff --checkは終了0。通常／試験DB・855業務ファイル・対象外ソースとHEADを保持しました。
変更は本書、contracts/editing-v2内の契約7ファイル、AGENTS.md、OCR Safety Contract、Artifact Storage Policyの11ファイルです。
operations.schema.jsonの22操作そのものは変更していません。
検証コマンドはDのvalidate_contracts.py、保持確認はaudit.py after。結果はvalidation.jsonとpreservation.jsonです。
jsonschemaの検査用依存はDのvalidation-depsへ隔離し、製品の依存定義・仮想環境に追加していません。
製品build、実編集API、実Decisions、実音声、通常移行は未実施です。
製品コード・DB・mapping・物理対応を変更しない範囲で既存OCR contractを維持します。

公式仕様確認日は2026-10-08。provider利用可否、品質・遅延、暫定閾値は未実測です。
次はフェーズ2、GPT-6.1-Sol / high。本書§2・§4〜6・§9〜10を優先し、
音声UI全体・モデル接続・通常切替まで一度に進めません。
