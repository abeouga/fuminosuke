# Focused v5 設計契約

2026-10-11、フェーズ18。**設計版であり、製品APIや実行可能なv5 adapterは未実装。** [設計審査](../../template-ai-phase18-design-2026-10-11.md)を併読する。v4全ページの不採用を変更せず、過去のschema・prompt・入力・採点を保持する。

## 1. 契約を分ける

| 層 | 正本と責任 |
| --- | --- |
| 物理snapshot | 全ページの所有者、版、page identity、revision、画像SHA、全cell ID/geometry/text、候補generationと内容digest。画像を減らしても全体の保持検査は縮小しない |
| ScopePlan | write物理ID、全体のread-only ID、allowed grid、依存・coverage、明示選択か自動優先か。送信前に固定する |
| EvidencePack | providerへ見せるセルと画像。全体のread-only集合の部分集合であり、編集権限を増やさない |
| Provider DTO | 本フォルダーのv5 schema。物理IDや保存形式から切り離した生成用形式 |
| 共通semantic IR | 全宣言のrole/値所有/parent/mark claimsを集約し、競合、保護、依存、名前を検証する既存の意味単位 |
| 編集案artifact | 共通compilerとmanual変換で検証済みの命令、read/write set、入力・案digest。HTTP DTOのv2案とは別のversionを持つ |
| 保存 | 既存のmanual-authoring → MutationCore → TemplateAuthoringService。AI専用保存やprovider JSON直接保存を作らない |

v5は外部v4生成契約に対して非互換。暗黙のv4 parse/fallback、既存DB layoutの一括再生成、旧案の新schema扱いはしない。製品HTTP v1は現状維持し、接続工程で明示したv2境界へ変更する。geometry/矩形生成はこの契約の対象外。

## 2. 不正な組み合わせを生成しにくくする

[proposal.schema.json](proposal.schema.json)はroot object、全property必須、additionalProperties=false、入れ子anyOfを使用する。条件付きif/then/elseは使わない。

| v5形式 | 必須条件 | 共通IRへの意味 |
| --- | --- | --- |
| labeled_fields | horizontal/vertical、各itemのlabelsが1件以上 | v4 fieldsと同じlabel/parent/reading意味。name:nullはlabel textから導出 |
| direct_fields | labelsとaxisを持たず、各itemのname必須 | direct、空labels、明示名。未解決のlabelを消す救済経路にしない |
| form | grid/行範囲/labelColumns/valueColumn/parent/reading | 各行を独立fieldへ展開。Phase17のfield/form共通claimsを必ず使用 |
| records | 全列の用途を列順に記述、includeEmptyRows=true | 既存record構造。row label、context、printed/position row keyを維持 |
| recordsのvalue binding | labeledならlabels非空、directならname必須 | 名前だけを作ってlabel対応を省略しない |
| recordsのtitle | heading cell＋nullable name、またはnamed＋必須name＋nullable parent | 名前の導出元がないnull titleを禁止 |

`name:null`は参照したprinted textを各要素でtrimし、空文字を除いた順序付き文字列を半角空白で連結する。長さ1〜100、空・長すぎる結果は保留し、短縮や近接推測で補わない。explicit nameの空白だけの値もsemantic検査で拒否する。名前の共有、alias、mark option、同じ親の共有は共通IRの意味を維持する。

schemaが防ぐのは**形状上の矛盾**である。参照が実在するか、ラベルの意味、formの列重複/順序/空欄、全列記述、親循環、printed mark evidenceはsnapshotとsemantic guardで確認する。不正単位から取得可能なclaimsを先に集め、保留した単位も競合検査に含める。正常側を先勝ちで採用しない。

`roles.reason=needs_context`を追加する。保留理由であり、実行権限や自動追加API要求ではない。省略セルはunknownのまま。単位の値所有とunknown宣言が矛盾すれば保留する。文書中の文章は命令として扱わない。

4つのexampleは形状と表現方法の例であり、実帳票の正解やruntime受入証拠ではない。特にdirect-fieldsはラベルを省略してよいという採点例ではない。

## 3. 入力を絞っても対応関係を保持する

`scopeDigest`は全snapshot binding、write/read集合、allowed候補、依存、schema/prompt hashを束縛する。`packDigest`はそれにinput profile、provider cell refs、全cell evidence内容、crop rectangle、画像bytes SHA、画像detail、planner/pack policyを追加する。cropは整数の元座標のまま、回転・拡縮・再検出なし。crop originを明示し、crop座標を新しいcanonical座標にしない。

比較用のpackはwrite cellsの外接矩形を64 canonical pixels広げ、境界を元画像にclipする。そこに交差するprinted cellの全文と、`□ ○ ◎ × △ 記号 チェック 選択 凡例`を含むprinted cellをread-onlyで加える。これはcontext検索規則であり、roleや正しい関係を確定する規則ではない。表題・見出しをwriteにする必要がある場合は送信前のScopePlanで扱う。既存の親chainは全要素をread contextへ加える。今回の固定入力はblank catalogなので既存chainはない。

画像はscope crop/highと元ページoverview/low。遠い凡例はtextとして残すが、画像しか根拠がない記号はこの方式では解決済みにしない。H2のc20（記号凡例）はpackに保持される。通常OCRのcropやpreview生成にはこの実験用cropを流さない。

F-lowでは全セルと元ページ画像/high、P-low/P-noneでは固定packを送る。いずれもallowed候補は**全構成セルが同じwrite poolに含まれる候補のみ**。全画像が見えても範囲外を書き換えられない。read-only label/parentの参照は既存catalogのroleと意味が一致し、書込みを発生させない場合のみ許可する。未知の範囲外親を補充して救済しない。

固定の最大セル数で文脈を途中切断しない。packが大きい場合も測定に含める。モデル出力に合わせたpack拡張、画像の追加送信、none失敗時のlow切替を同一試行の成功として扱わない。context不足は保留して終了する。

## 4. 小範囲案の公開とページ全体の完了

製品接続時のHTTP契約案は `template-analysis.v2`。これは設計名称であり、現在のrouteで受け付けられる値ではない。

- Createは既存binding/draft/manualTouchedFacetsを維持し、`requestedCoverage`（page/pages/selected）、任意の`priorityTarget`（選択表またはviewport）、明示した最大送信枠を持つ。priorityTargetだけではrequestedCoverageを狭めない。
- AnalysisScopeは`scopeId, scopeDigest, dependencies, state, proposalDigest, readSet, writeSet, heldReasons, timing`を持つ。scope stateはqueued/running/validating/ready/held/failed/cancelled/stale。immutable artifact payloadと可変の公開状態を分ける。
- scope_readyイベントはscope ID/digestと単調増加sequenceだけを通知し、GETで検証済みartifactを取得する。provider deltaや未完了JSONをUI/保存へ渡さない。再接続はsequenceから再取得できる。
- Mergeはscope ID、scope/案digest、selectedUnitIds、現在draft、manualTouchedFacets、requestIdを受け取る。親runがrunningでも**そのscopeがreadyで依存も成立している場合のみ**下書きへ反映できる。現行READABLE定数を緩めるだけの実装は禁止。
- receiptは反映したscope/案digestと命令IDを列挙する。同一requestIdは同一結果を返す。通信断後の再取得で二重反映しない。保存捕捉は適用済みscope receiptの集合とmanual serialを固定する。
- 下書き反映後も保存は「確認して保存」。手動の同値入力も保護する。利用者が保存・ページ移動・中止した場合は残りを停止し、旧revisionへの遅延応答を反映しない。
- runの`firstReadyAt`と`completedAt`、`coverage`（未着手/解析済み/保留/失敗）を別に保持する。全セルをready件数だけで覆ったと判定しない。全scope終了でも保留・未着手が残ればpartial。予算終了はpartial＋明示理由で止める。

全scopeを事前に固定し、同一write cellを複数scopeへ割り当てない。共有見出しは依存scopeで確定したread-only参照にするか、依存領域を一つへまとめる。独立性が確認できない表を無理に分割しない。意味案の新しいscope追加で全体の残予算・coverageを黙って拡大しない。

**範囲をまたぐ矛盾は公開順で正当化しない。** 同時に未反映の案に矛盾が見つかった場合は両側の依存単位をheldにし、公開済みdigestをstaleとして通知する。既に利用者が反映したdraftへの上書きや自動取り消しはしない。そのfacetは保護し、新案を保留、以前の依存案にも要確認を表示する。保存前の再照合で未解消の競合を拒否する。既存保存を別解析の結果で遡って変更しない。後続phaseでこの横断状態の短い統合試験を要求する。

UIは「この範囲の案を確認」「残りを解析中」を併記する。変更多数は全セルを同時に枠表示し、連続操作の移動アニメーション、画面外への追従、表タイトルのoverlayは既存の設計を引き継ぐ。処理中の枠を保存済み表示に使わない。自動スクロールは手動操作中に奪わず、reduced motionを尊重する。

## 5. 任意選択と自動優先の境界

利用者が表を選択していればその表を優先する。未選択なら直近の対象表、次に現在viewport内の候補、次にページ順とする。ただし直近表の有効性は同じowner/page/snapshotで検査する。これは先に処理する順番であり、自動的に表を意味認定する規則ではない。

自動plannerは候補の交差、必要な見出し、既存親chainを含む範囲をまとめる。候補がなく小さいページはページ全体を1scopeにする。巨大な連結領域、表題境界が不明な候補、全ページの残余セルはwhole-page/deferredとしてcoverageへ残し、成功のために切り捨てない。5秒を保証できない領域はそのまま計測する。

**今回の5scopeは実験用に固定したもの。自動plannerの正解や任意帳票の範囲分割を検証したものではない。** Phase19はこの固定scopeだけの実装・検証、Phase20はそのAPI測定とする。汎用plannerと全ページcompletionの製品実装は採用審査後の別受入が必要。

## 6. フェーズ19のオフライン受入

Phase17 runtimeを新しい製品外ディレクトリへコピーし、旧証拠を上書きしない。v5 DTO adapterとevidence pack builderだけを追加し、共通normalizer/compiler/manual経路とphysical identityを維持する。過去の保存24応答は旧v3/v4経路で再生し、v5生成結果と偽らない。

実装前に、次の未検証条件の入力・期待結果を固定する。既存Phase17の12条件と重複する試験を増やさず、既存結果との一致と新しい入口を検証する。

1. labeled/direct/form/recordsの変換後、名前・label共有・parent・mark・行順と空行・row key・contextの意味が共通/manualで保存される。
2. unknown参照、間違ったsnapshot/scope/pack SHA、範囲外値所有/role/parent、crop origin改変を拒否。範囲外cell全体とphysical geometryは完全一致。
3. form保留のparent claims、列重複/順序/空ラベル、role/ownership/mark競合は正常側を含めて保留。同じ親共有は許可し、親削除で救済しない。
4. explicit/derived nameの空文字、whitespace、過長、labels不足、named title不足、mark evidence不足を保留する。partial JSON/refusal/incomplete/timeout/cancelは案にしない。
5. same-value manual touch、既存meaning・parent・role保護、既存graphの全ID/pathを含むno-op、read-only contextだけの参照で書込みを起こさないこと。
6. 固定全15入力hash、5scopeとgold、H2凡例、元画像からの正しいcrop、F/P同一write pool、pack/source hash、単一送信台帳の予約と停止をAPIなしで確認する。

scope HTTP/DB/event/receiptはPhase19には作らない。短い純粋境界検証で通過しても、実保存、所有lifecycle、全件回帰、UI、全ページ完了を受入済みにしない。

## 7. フェーズ20の固定比較と停止条件

[phase20-protocol.json](phase20-protocol.json)の順序、[focused-scopes.json](focused-scopes.json)のscope・元入力hash・派生goldを正本とする。provider schema/prompt、pack、実装、環境、送信bodyのhashを実行前に固定する。schema/promptの最終hashはPhase18のdesign-manifestにも記録する。実装で契約の修正が必要になった場合は差分を報告し、新protocol版を固定するまで送信しない。

同じscope・schema・prompt・出力上限で、F-low対P-lowは入力pack全体（セルcontext＋画像detail）の効果、P-low対P-noneはreasoning effortの効果を比較する。画像だけ・cell textだけの独立効果やv4→v5の因果効果は主張しない。30回は5既知帳票×2反復×3方式、並列1、60秒、stream=true、store=false、max_output_tokens=24576。warmup/retry/診断追加は0。失敗も送信回数へ計上する。

provider/semantic/compile/手動投影/採点を通った時刻を区別する。`first_text_ms`は利用可能時刻ではない。`scope_ready_ms`はcompile/manualまで検証したartifactが得られた時刻。`quality_qualified_scope_ready_ms`はその案が**当該試行の全重点正解と誤結合0**も満たした場合のscope_ready値であり、採点処理時間を含まない後判定指標。品質未達はnull、比較集計だけ60000ms penalty。実timeoutと混同しない。開始はpack準備前、cold/warm cache別記録、provider開始/first delta/終了・schema/resolver/compiler/manual終了、cached/input/output/reasoning tokens、finish/refusal/incomplete理由を保存する。将来の実UI到達/表示時間は別指標。

各方式の最低条件は分類146/146、結合74/74、行14/14、記号16/16、重点誤結合0。試行別の正解条件も全件要求する。重点外の追加binding/parent/nameも原画像・textと照合し、誤りを発見した案は不合格。未採点の追加書込みは「正解」と推定せず、別の人間レビュー待ちとして記録する。

速度条件は品質条件を満たした上で、中央値5秒以下、5秒以内8/10以上、各帳票最低1/2が5秒以内。これを満たしても「固定した局所案」の候補採用に限る。全ページの旧基準（分類150/176以上かつ同時D13未満なし、結合72/78以上かつ同時D13未満なし、行14/14・記号16/16・誤結合0・ケース別条件・全ページ5秒8/10等）は保持し、今回の分母へ置き換えない。全ページの改善は未測定とする。

10件/方式の中央値と対比較を報告し、p95、一般的な成功率、未知帳票精度を断定しない。方式選定は品質→5秒条件→中央値→API token量の順。全方式が不合格なら次の30回を自動実行しない。即時停止はscope/physical保持破綻、入力hash不一致、台帳不整合、またはAPI設定不受理で比較条件が成立しない場合。通常の意味誤り/60秒timeoutは記録して固定scheduleを継続する。

## 8. 公式仕様との対応

GPT-6 Lunaはimage input、Responses、streaming、Structured Outputs、reasoning none/lowをサポートするため、この2強度を比較対象にする。noneが速く正確かは未検証。[モデル仕様](https://developers.openai.com/api/docs/models/gpt-6-luna)

Structured Outputsは形状制約に使い、意味・対応関係の保証に拡張しない。入れ子anyOf、必須property、additionalProperties=falseを採用した。オフラインJSON Schema通過は実endpointのschema受理とは別である。[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)

入力削減だけで大幅な短縮を保証できないため、出力の要求範囲、推論強度、依頼数と待ち合わせ境界を分けて設計する。小範囲分割は全ページの総時間・呼出数を増やす可能性も記録する。[Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization)
