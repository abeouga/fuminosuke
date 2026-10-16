# AIテンプレート作成・フェーズ3：方式とAPIの設計

設計日：2026-10-09。開発対象：`C:/Users/hanam/Desktop/llm-ocr-deploy`。
この文書は**次工程の実装仕様**であり、実装済みAPI・達成済み性能を示さない。
前提と評価は[フェーズ1](template-ai-phase1-2026-10-09.md)、[フェーズ2](template-ai-phase2-2026-10-09.md)。
APIモデルは利用者指定の **`gpt-6-luna`**。開発モデルのAstra / GPT-6.1 Solとは区別する。

## 1. 決定

1. 全体の分類・意味付けは **Responsesを1ページ1回**。`reasoning.effort=low`、画像`detail=high`、`store=false`、strict structured outputを初期設定とする。フェーズ2の比較条件を基準にし、品質確認なしにnoneや低解像度へ変更しない。
2. **種類だけ**の解析はDecisions。1セル1質問、役割7種類＋`unknown`の8候補、1要求200質問以下。同じ解析の前段・後段としてResponsesを自動追加しない。
3. 全体解析にDecisionsを直列必須化しない。並列の二重解析、confidenceだけによる自動保存、失敗時の無制限再解析も採用しない。
4. AIは既存の物理セルを参照して意味の案を返す。セル検出・座標・多角形・crop・結合履歴を生成し直さない。
5. 単体欄と繰り返し表を排他的に表現し、表の行列から個々の結合をサーバーが展開する。AIに同じ結合を二重出力させない。
6. 解析、下書きへの反映、保存を分ける。反映APIは検証した下書きを返し、保存は既存の `manual-authoring → ManualCompiler → MutationCore → TemplateAuthoringService` の経路を使う。
7. 全体解析結果を受け取っただけでは保存しない。画面で案を確認・修正し、既存の保存操作で保存する。音声による明確な局所編集の即時保存仕様は変更しない。
8. 手動の設定と解析中の変更を優先する。由来不明の既存設定を「未編集」と推定しない。再解析は不足範囲の提案を既定とする。

音声取得、直前セルを引き継ぐ訂正、矩形検出の新方式、通常環境への移行はこの設計の範囲外。手動描画・局所録音・連続音声編集を残す。

## 2. 採用根拠と限界

主要20試行の全体中央値はResponses 12.672秒、Decisions＋補完16.987秒。後者は34.1%遅く、既定採用の「20%以上短縮」を満たさなかった。表を展開した結合一致は25/78対14/78、行の一致は11/14対8/14。小さい固定評価2帳票では後者の種類・結合一致が良い例もあり、Decisions全般が劣るとの結論にはしない。

種類だけのDecisionsは調整用2帳票で1.687秒・2.096秒。ただしT1は高confidenceでも3/14を誤分類した。これは種類だけの限定採用を試す根拠であり、全体意味付けが2秒で終わる証明ではない。種類だけは初期段階では補助機能とし、固定評価で検証してから通常表示する。

以下は実在の専門家の意見ではなく、三つの評価観点を分けた設計判断である。

専門家A（応答時間）：同じ画像への直列二重呼出しを除く。短いIDと表単位の出力で生成量を減らし、通信時間、最初の検証済み案、全体完了、手直し完了を別々に測る。

専門家B（帳票の対応関係）：省略を成功扱いしない。全セルの分類、入力欄の意味又は明示的な未解決、行列の一意所属を検査する。正しいJSONや高confidenceは意味の正しさを証明しない。

専門家C（保守性）：AI用の保存系を増設しない。外部応答を独立した提案型へ変換し、既存の意味編集コンパイラーと保存transactionに接続する。表示用の案は業務DBの完成状態に混ぜない。

客観的まとめ：全体Responsesと限定Decisionsの使い分け、検証済み下書きへの反映、共通保存で一致する。初回案をさらに早くするための二重解析は費用と誤提示が増えるため見送る。新しい出力形式の速度・精度は未測定であり、フェーズ7の同条件比較で採否を確認する。

## 3. 実装の境界

既存の根拠ファイル：

| 責任 | 現在の実装 | 今回の接続方針 |
| --- | --- | --- |
| セッション、範囲、認証、revision | `backend/app/api/v2/editing.py`、`editing_contracts.py`、`backend/app/editing/infrastructure/sessions.py` | 同じ編集ownerに解析を所属させる |
| 手動下書き | `backend/app/editing/manual_draft.py`、`manual_compiler.py` | 同じManualDraftを受け、閉じた意味操作へ変換する |
| 22操作と意味・行列 | `operation_contracts.py`、`commands.py`、`semantic_commands.py`、`record_commands.py` | 既存compilerを利用し、別のmapping生成器を作らない |
| 原子的保存・receipt・undo | `infrastructure/mutation_uow.py`、`mutation_core.py`、`backend/app/services/template_authoring_service.py` | 既存の保存世代・履歴・資格失効を維持する |
| 下書き保持、保存結果不明の復旧 | `frontend/src/editing/workbenchStore.ts` | AI反映を手動下書きと合成し、同じ保存復旧に接続する |
| 枠、移動、scroll | `frontend/src/editing/AiFocusLayer.tsx`、`focusState.ts` | 解析中・案・反映済みを表示し、手動選択と区別する |

新設する責任は `backend/app/template_analysis/` 配下のdomain（提案・検証・差分）、application（解析・反映）、infrastructure（provider・実行記録）と、薄い `api/v2/template_analysis.py`。配置は実装予定で、現時点では存在しない。providerはdomain/DB/HTTPへ直接保存しない。editing側はproviderをimportしない。共通の正規化・操作変換を再利用するための抽出は許可し、OCRの判定・画像処理の一般化は含めない。

実コードには追加の制約がある。`ManualCompiler.compile`にも`CommandCompiler.compile_operations`にも50操作上限があり、全体案をManualDraftに変えるだけでは大きい帳票を保存できない。また現在の1000欄制限には同じ欄の反復参照数の検査もある。フェーズ4では**保存経路を増やさず、内部のコンパイル上限を用途別に分離する**。公開manual_changes/AI指示は現行50操作・参照1000、検証済みManualDraftからの内部変換は最大5000操作・参照20000・相異なる物理セル1000とする。型・scope・graph・prepare検証は同じものを使う。50操作ごとの分割commitはせず、最後に一回だけrevisionとreceiptを保存する。5000は無制限化を避ける製品上限で、性能を実測済みという意味ではない。

保存layout自体の上限（labelPool 500、relations 1000、recordSections 100、recordGroups 300等）は維持する。合成後の全体で検査し、超過を`AUTHORING_CAPACITY_EXCEEDED`として保存前に通知する。モデル用schemaの配列上限だけで保存可能と判定しない。これは共通編集APIの必要な内部拡張であり、OCR読取や物理対応の契約変更ではない。

## 4. 入力と実行単位

### 4.1 基準snapshot

解析開始時にサーバーが固定するもの：template version ID、DRAFT状態、resource revision、session/context/interaction epoch、catalog revision、scope digest、ページidentity、画像SHA、物理セル集合・座標・多角形・結合履歴のdigest、現在のManualDraftのdigest、保護設定のdigest、prompt/schema/provider policy version。

クライアントがファイルパス、画像URL、model、prompt、API上限を指定する形式にはしない。画像は版の所有artifactから読み、指定ページ・画像SHA・物理セルを照合する。画像や印字内の命令文は資料として扱う。

最初の版は**保存済みの物理枠**を解析対象とする。意味だけの未保存変更は保護した下書きとして渡せる。枠の追加・移動・削除・結合など物理変更が未保存なら `PHYSICAL_DRAFT_UNSAVED` とし、通常の保存を案内する。解析ボタンから無断で保存しない。セル選択は不要。

### 4.2 ページ・件数・予算

- `pageScope`は既存DTOと同じ`current/all`。allは版に保存された明示ページidentityの一覧へ展開する。legacy単一画像のpage IDはnull。配列indexを永続identityにしない。
- 1実行は物理セル1000以下。既存編集UOWの1000欄制限に合わせる。超過時は送信前にページ又は表で対象を狭める。自動分割保存はしない。
- Responsesはページ単位。1ページ1000セル以下、出力上限24576 token、1要求60秒を初期上限とする。上限超過は失敗として残し、途中JSONを採用しない。
- Decisionsはページ内の対象セルを固定順で最大200ずつ分割する。200はフェーズ2のHTTP400実測に基づく製品上限。SDKの自動retryを0にする。
- 全体解析はページ数、種類だけは `Σ ceil(ページ内対象セル数 / 200)` を必要最大呼出し数として送信前に表示する。保護された対象しかないページは送信しない。
- 解析module全体で最大2要求並列、1sessionの実行中runは1件。別request IDでも同じsnapshot・同じ解析種別は同じ実行へ合流する。同じsessionの異なる実行は409で既存runを返す。予算台帳は全機能共通、送信直前の予約を必須とする。他処理の消費で不足した場合は未送信を停止し、成功済みページの案だけ保持する。
- 自動fallback・修復用LLM・自動再送は0回。明示的な再解析は新request IDにし、失敗又は選択されたページだけを新しい予算で送る。
- この工程のAPI使用は0回。開始時222/496、残274を前提とし、実装時に台帳の現在値を再確認する。

## 5. モデル出力を一つの意味graphにする

設計schema：[`contracts/template-analysis-v1/proposal.schema.json`](contracts/template-analysis-v1/proposal.schema.json)。これはproviderの完成出力の設計であり、既存layout schemaの置換ではない。structured outputへの実送信互換性はフェーズ4で確認する。

### 5.1 参照と分類

- サーバーがページsnapshot内のセルへ短い参照`c1`などを割り当てる。対応表はrun所有で固定し、別runへ流用しない。モデルへ永続path・書込handleを返させない。
- 分類対象の物理セルと、参照だけ許す既存仮想見出し・範囲外ラベルを入力で区別する。後者はrolesの被覆対象に含めず、既存のroleと保護設定をサーバーで補って参照検査する。参照できることは変更許可を意味しない。
- `roles`は7役割と`unknown`ごとの参照配列。対象物理セルが**全配列を通じてちょうど一度**現れることをサーバーで検査する。保護されたセルもその現状態を入力し、変更禁止を検証する。
- 重複、欠落、不明ID、範囲外はページ案全体を `PROPOSAL_INVALID` にする。配列最後の値を勝手に採用しない。
- `unknown`のセルは未解決理由を必須とする。refusalや低確度を`ignored`へ置き換えない。

### 5.2 単体欄、表、見出し

- `fields`は単体inputセル、意味名、ラベル集合、方向、直近の親見出しを表す。ラベルが空なら`axis=direct`。同じinputへの二つのfieldは禁止。
- `tables`は名前、列ごとの意味・ラベル、行ごとの**明示セル列**を表す。行配列順は表示順、セル参照そのものが対応関係である。各行の長さは列数と一致し、各物理セルは同じ表及び他表を通じ一度だけ所属する。
- 表内のセルを`fields`へ重ねて列挙しない。tableのcolumnsとrowsのzipから、サーバーがcanonical bindingを生成する。既存 `records.define` が保存上の個別path/relationsを割り当てる。
- 列名が同じでも別列を勝手に統合しない。列位置だけから永続mappingを再生成しない。手動の並び・行ID・既存pathがある表は既定で保護する。
- `headings`は既存parent_labelセルの親子階層。単体field又は表が持つ`parent`は**直近の意味上の見出し**だけ。書類全体の題名を自動的に全欄の親へしない。題名は独立した根でもよい。
- 既存操作への変換では、単体欄の親はラベル（ラベルなしならinput）への`heading.attach`、表の親はrecord sectionへの`heading.attach`にする。表の各relationへ同じ親を重複書込しない。canonical評価時はsectionの親を表内bindingへ継承して比較する。
- 同じラベル集合を使う関係では親の解釈を統一する。循環、自己参照、複数親、ページ越境、labelとinputの衝突は拒否する。heading.attachが既存relationの親にも影響することをread/write依存へ含める。
- serverは座標近傍と行列整列の情報を候補として渡せるが、それだけで意味や表所属を確定しない。初版はAIが明示した行列を検証する。自動の表パターン探索・候補選択は評価前に追加しない。

### 5.3 完全性と未対応

各inputは単体field・tableセル・保護された既存結合・`unresolved`のいずれか一つに必ず属する。モデルが未解決を明記せず結合を省いた場合、サーバーが `missing_binding` を補足し、そのセルを反映可能にしない。role自体の欠落は補足せず不正として拒否する。

この意味の完全性検査は`full`の契約である。`roles`は種類だけを変更する独立taskであり、意味を完成させたと報告しない。既存依存を壊さないrole案のみを反映でき、意味が未設定である状態をdraftStateに残す。Decisionsのunknown/refusal、又は最大候補確率が0.70未満ならroleはunknownとする。0.70は初期の表示振分値にすぎず、0.70以上でも人間の確認・全検証を省略しない。返答はサーバーで同じroles表現へ正規化し、モデルに自由形式graphを生成させない。

マークはrole提案まで可能。初版は新しい選択肢の領域・markSetsを自動生成しない。既存設定は保持し、新しいmarkの意味付けは `unsupported_mark_definition` として残す。複合subfield、既存表の行再配置、仮想見出し新設、identity/name設定の自動変更も初版の全体解析から除外する。既存の手動・明示的音声操作では継続利用できる。未対応を黙って欠落させず、完成率と残件に含める。

### 5.4 検証順序と反映単位

1. provider完了状態、schema、モデル、snapshot所有。
2. 全role集合と参照の閉包、ページ・親子・行列の一意性。
3. 役割と結合の整合、inputの完全性、保護と未解決の区分。
4. 独立な変更単位の作成。単体fieldはラベル/role/親を含み、表は全行列を一単位とする。共有見出し・ラベルへの変更依存を併合し、必要な依存を切り離して選択させない。
5. 既存typed operationsへ変換し、既存compilerとauthoring prepareで検証。変更予定の物理identityが同一、対象外と保護facetが同一であることを照合する。
6. 検証済み単位だけを`mergeable`にする。構造不正のページから「正しそうな部分」を取り出さない。単に未解決な単位は他の独立単位を妨げない。

`ready`は案の構造検証完了を表し、意味の正しさ・template完成・OCR資格を表さない。画面には未解決数を別に表示する。

## 6. 手動変更の保護と下書きへの反映

### 6.1 保護の正本

`LayoutCell.source`は枠の由来であり、roleや意味の手動編集履歴ではない。これだけで上書き可否を判定しない。実装では編集側に、版・entity・semantic facetごとの `unreviewed / ai / manual / unknown` と値digestを持つ補助metadataを追加する。facetはrole、meaning、label binding、heading parent、record membership/name、reading、mark/compositeに限定し、自由なJSON pathは受けない。

- 明示的な手動/音声の意味変更は、その変更facetをmanualにする。同値の明示再指定もmanualにする。変更していない別facetを一括manualにしない。
- 検出直後の新規roleだけは、検出完了時にunreviewedを記録できる。既存データをsourceから推定してbackfillしない。
- 移行時に由来が不明な既存設定はunknownとして保護する。既存の意味・表・読取・mark・identity/nameは常に初版の保護対象とする。
- 未保存の手動変更はeditorの変更journalと、基準下書きからの実差分の双方で追加保護する。同値への明示操作をjournalで補う。最終保存時にサーバーが実差分と照合する。
- unknown/manualのroleを見直したい場合、UIの追加設定「既存の分類も見直す」で**その時点の対象role一覧**を明示解放する。解析中に後から手動変更したroleには解放を引き継がない。初版でこの指定により意味や表の既存pathを解放しない。

保護metadataは解析runの状態とは分け、意味保存と同transactionで更新する。原本layout、OCR snapshot、過去jobの形式は変えない。metadata更新だけでresource revisionを無断更新しない。通常DBはこの工程で移行しない。

### 6.2 三者比較

基準をB（解析開始の下書き）、現在の手動下書きをH、AIの変更をAとする。反映時は常にサーバーで検証する。

- B→Hで変わったfacetと、Aのwrite/read依存が交差しなければHへAを合成できる。
- 同じ変更でも手動が明示操作したfacetは手動の所有を維持する。
- 衝突した単位は全体を保留する。例えば表の一行を手で変えた場合、その表単位は自動反映しない。独立する別の単体欄は反映可能。
- ページ内の物理digest又は画像SHAが変わった場合は、そのページ案を失効させる。移動したラベルを古い座標の近傍として扱わない。
- サーバー保存revisionが変わった場合は `REVISION_CONFLICT`。自動rebaseや新API送信は行わず、下書きと旧案を保持して最新状態を取得する。明示再解析で続行する。
- 正常に反映した案だけを編集下書きへ合成する。同じmerge requestの再送は同じ結果を返す。未解決・競合を削除して成功件数へ含めない。

### 6.3 保存・undo

反映APIは業務layoutを保存しない。返却したManualDraftを既存editorに載せ、通常の保存ボタンから `manual-authoring` を使用する。保存時は現revision・scope・物理対応を再検証する。

既存 `ManualAuthoring` へ任意の `analysisMergeIds` と `manualTouchedFacets` を追加する（既定は空）。merge記録と同じowner・版・base revision、及び最終facet値digestを検証し、一致するAI変更だけをAI由来として記録する。記録にない変更・反映後の手修正・明示touchはmanual。AI由来の申告は書込権限やvalidation免除には使わない。新entityは既存substitutionsで正規IDへ対応させる。再送時はpayloadを変えない。

意味だけの保存は既存receipt/直前undoへ接続する。枠変更を同時保存した場合は既存のphysical edit制限に従い、無条件にundo可能と表示しない。保存前のAI反映を戻す操作はeditorの下書き履歴、保存後はserver receiptと区別する。

## 7. HTTP API

prefixは既存の `/api/v2/editing/sessions/{sessionId}`。Token、local request制限、owner、DRAFT、editing有効化gateを再利用し、追加 `template_analysis_enabled=false` で隔離する。新契約名は `template-analysis.v1`。無効時503、所有外は既存認証方式に従って拒否する。

| Method / path | 入出力・意味 | 外部API / 業務保存 |
| --- | --- | --- |
| POST `/template-analyses` | 下記Createを受け202 Run。requestIdの同一payload再送は同一run、異なるpayloadは409 | manifestに記録した呼出しのみ / なし |
| GET `/template-analyses/{analysisId}` | Run、ページ状態、完成したProposal、未解決、計測値 | 0 / なし |
| GET `/template-analyses/{analysisId}/events` | 認証付きfetch SSE。Last-Event-IDで再接続、sequenceで重複排除 | 0 / なし |
| POST `/template-analyses/{analysisId}/cancel` | 新規送信を停止。送信済み消費は戻さない | 0 / なし |
| POST `/template-analyses/{analysisId}/merge` | 下記Mergeを受け200、検証済みManualDraftとMergeReceiptを返す | 0 / なし |
| 既存POST `/manual-authoring` | ManualDraft＋任意のmerge由来情報。既存202 command/receipt | 0 / 既存transaction |

Create（`binding`と`draft`は既存DTOをそのまま参照）：

```json
{
  "contractVersion": "template-analysis.v1",
  "requestId": "client_generated_id",
  "binding": "Binding object from editing.v2",
  "draft": "ManualDraft object from editing.v2",
  "task": "full",
  "pageScope": {"mode": "current", "pageId": null},
  "selectedTableId": null,
  "releaseRoleProtection": [],
  "manualTouchedFacets": []
}
```

上のbinding/draft文字列は読みやすさのための型参照表記で、送信例ではない。実際はobject。taskは`full/roles`、selectedTableIdは既存表ID又はnull。役割だけの場合は自由な意味名・行列の出力を要求しない。releaseRoleProtectionは現在catalogのcell handleを最大1000。serverは版所有・scope・roleだけの解放を検証する。manualTouchedFacetsは `{target, facet}` の有限enum配列（最大5000）で、保護追加にしか使わない。targetは既存の`{handle}`又は下書き新entityの`{draftEntityId}`の排他的union。後者は同じManualDraft内のentityと型を検証し、保存時のsubstitutionsへ対応させる。

Runの必須項目：`contractVersion, analysisId, requestId, resource, baseRevision, snapshotDigest, draftDigest, scopeDigest, state, pages, plannedCalls, reservedCalls, completedCalls, expiresAt, lastSequence, proposal, error`。未生成のproposal/errorはnull。pagesは明示pageId、状態、calls、latency、issue件数。秘密・認証header・画像base64を返さない。

Proposalは `proposalDigest, units, unresolved, coverage, focus`。unitはserver発行ID、pageId、kind（field/table/heading/role）、依存unit ID、対象cell ID、変更前後の説明、状態（mergeable/protected/unresolved）、理由。providerの全文や内部operationsをUIの正本にしない。digestはsnapshotとcanonical graph、検証policy versionに束縛する。

Merge request：`contractVersion, requestId, binding, proposalDigest, draft:ManualDraft, selectedUnitIds, manualTouchedFacets`。max bodyは既存manual-authoring同様2 MiB。全unit選択をUIで一括操作できる。scope/contextが変わった場合はbindingを更新し旧runとの一致を再検査し、別の対象へ無断で反映しない。

Merge response：`contractVersion, analysisId, mergeId, baseRevision, inputDraftDigest, outputDraftDigest, draft:ManualDraft, appliedUnitIds, conflicts, unresolved, focus, substitutions`。競合のない選択unitが一つ以上なら200で独立単位だけを反映し、衝突単位を必ず列挙する。全部競合なら409でdraftを変更しない。構造不正・所有不一致は部分成功にせず全拒否する。

HTTP上のCreate/Run/MergeのDTOはフェーズ4でPydanticを正本に作り、schema/TypeScriptを生成する。この工程のprovider schemaを手書きの第二のHTTP schemaとして流用しない。既存50操作の公開manual_changes上限を緩めず、全体案はManualDraftへ合成して一回の保存で扱う。

将来ChatGPTから呼ぶ場合も、解析・案取得・反映・既存保存を同じdomainで行う。providerへ渡すのは提案schemaだけで、保存tool・任意SQL・任意pathは渡さない。外部ChatGPT用の認証・公開MCPは今回のローカルAPI実装と別工程であり、ローカルTokenを外へ転用しない。

## 8. 実行状態、失敗、再接続

```text
queued -> running -> validating -> ready | partial | failed
queued/running/validating -> cancelled | expired
ready/partial -> stale | expired
```

`partial`は独立ページの成功と不成功が混在する場合。単一ページの未解決セルはready＋未解決件数。mergeはrun状態を保存完了へ変えず、別のmerge記録を追加する。保存結果の正本は既存command receipt。

SSEは `accepted, page_started, page_validated, progress, ready, partial, failed, cancelled, stale, expired`。各eventにanalysisId・snapshotDigest・sequence・時刻を持たせる。progressにモデルの途中JSONや未検証の意味付けを含めない。単一ページでは完成前のセル別意味プレビューを約束しない。複数ページは完成して検証されたページから表示する。

| 条件 | code / 挙動 |
| --- | --- |
| provider refusal、completed以外、出力上限、timeout | `PROVIDER_REFUSED/INCOMPLETE/OUTPUT_LIMIT/TIMEOUT`。当該ページの案を反映不可、再送なし |
| role欠落・重複、自己参照、異なる表に同じセル | `PROPOSAL_INVALID`。当該ページ全拒否、診断を保存 |
| 意味の欠落、unsupported、曖昧な親 | 未解決単位として保持、未解決件数に含める |
| revision、scope、物理・画像の変更 | `REVISION_CONFLICT/SCOPE_CHANGED/SNAPSHOT_STALE`。旧案と手動下書きを保持 |
| 手動変更との衝突 | `MANUAL_CONFLICT`。単位別保留、保存しない |
| 台帳不足 | `API_BUDGET_EXCEEDED`。未送信停止、既使用分を払い戻さない |
| 同じrequestId、異なるpayload | `IDEMPOTENCY_CONFLICT`。再実行しない |
| cancelとprovider完了の競合 | 永続stateをCAS照合し、cancel確定後の応答を案に昇格させない |
| process再起動でrunningが残る | `INTERRUPTED`として失敗確定。GETを契機に有料処理を再開しない |
| SSE切断 | GET又はLast-Event-IDで再取得。解析・merge・保存の新規送信をしない |
| 保存応答の喪失 | 既存requestIdでcommand照会。元payload・下書き・merge情報を保持 |

runとmergeの有効期間は作成から30分、session終了で書込利用失効。実行deadlineは `60秒 × ceil(送信manifest件数 / 2) + 30秒`（最大30分）として固定する。保存済みreceipt/由来digestはrun期限後も履歴から参照できる。raw model応答・画像を通常ログへ残さない。試験時の保存応答はDドライブの専用artifactのみ。運用run snapshot/proposalは有効期間後に削除可能、由来の最小auditは業務履歴の保持期間に従う。

永続化の実装予定は `template_analysis_runs`（owner、request fingerprint、固定snapshot/manifest、状態、期限、計測値）、`template_analysis_pages`（run/page identity、provider予約、状態、案/診断）、`template_analysis_merges`（request fingerprint、基準/入力/結果digest、反映単位、新ID対応）、`template_authoring_facets`（版/entity/facetの由来と値digest）、`template_analysis_events`（run/sequence）。外部provider待ちの間にDB write transactionを保持しない。events/run/pagesの更新とsequence割当を同transactionで行い、unique制約と状態CASを使う。既存API台帳を複製しない。migrationは隔離コピーでのみ実施し、通常移行は別途扱う。

## 9. 画面への接続

- 現在のpreviewと手動editorを中心に、主操作は「AIで意味付け」「案を反映」「保存」。種類だけの解析は追加メニューに置き、全体処理の必須手順にしない。
- ボタン操作直後は対象範囲と解析状態を表示する。解析中の枠は対象範囲を示すだけで、セル分類済みの色として使わない。
- 完成した案は役割・意味・行列の差分と未解決数を表示する。反映前の案、下書きへの反映後、保存済みを区別する。「変更しました」は保存receipt受領後だけ使う。
- AI案の対象セルは同時にアウトラインで示す。primary focusが変わると既存の移動アニメーション、画面外なら自動scroll、表名のoverlayを利用する。全セルを順番にscrollして待たせない。reduced motionと手動scroll抑制を既存仕様どおり維持する。
- 複数セルの枠、手動選択、DOM focus、編集scopeを別状態にする。案の表示で表選択や音声対象を変更しない。
- 読み込み中に手で修正できる。遅い応答は一致するsnapshotの案欄にだけ載せ、古い値で入力を上書きしない。競合後も手動下書きを保持する。
- 既存の書類名/識別設定、手動描画、polygon、mark、番号採番を省略しない。装飾を増やさず、通常の業務用editorの密度を維持する。

## 10. 計測と受入条件

### 10.1 測る値

同一端末・画像・model・reasoningで `click→accepted`、server queue、provider send→first event、provider完了、validation/merge、最初の検証済み案、全体案、利用者の修正→保存完了を分離する。API sends/usage、失敗/未解決/保護/競合、手修正回数も記録する。途中字幕やローディング表示を「AI完了時間」に数えない。

全体中央値の開発目標はフェーズ2の12.672秒から20%短縮（10.138秒以下）。保証値ではない。品質・拒否条件を悪化させて達成しても採用しない。provider待ちを含まないUI受付は200ms以下を目標とする。2帳票だけのp95推定や、成功試行だけを抜く採点はしない。

### 10.2 実装前に固定する失敗条件

| 対象 | 必要な観測 |
| --- | --- |
| 保存済み実response | Phase2の重複行セル・自己参照を拒否。表のzip展開で直接binding省略を誤判定しない |
| role全数・意味欠落 | missing/duplicate/out-of-scopeを拒否、結合未指定は未解決として表示し反映不可 |
| graph | 親cycle、共有labelの親矛盾、row幅・複数表重複、page越境、mark/compositeの無断変更を拒否 |
| 手動保持 | 解析中にrole/名前/表を修正、同値再指定、別欄編集、再読込、旧revision、物理変更で失わない |
| idempotency | Create再送で追加送信なし、merge再送で同じ新ID、save再送でrevision/receipt一回 |
| lifecycle | cancel直後のlate response、timeout、provider拒否、再起動、SSE切断、予算不足、DRAFTでない版 |
| 共通保存 | 通常手動とAI反映の同じ下書きが同じ意味になり、scope/capability/prepareの拒否を回避しない |
| 保護契約 | 既存セル/region/mapping/path/画像/crop/受付snapshotを保持。新しい意味だけ既存server割当経路 |

Phase1の88role・39binding・7rowを凍結のまま使用する。H2はmanifestの`H2-valid`。新しい表形式の採点はgold非依存のcanonical展開を経由する。初回案と利用者の修正後を別に採点する。既存期待値をモデルの返答へ合わせない。全459セルの正解率や実利用完成を重点評価だけから主張しない。

Decisions種類限定はH1/H2も評価し、role-onlyとしての未解決と高confidence誤りを計上する。whole-templateの品質や時間に置き換えない。全体Responsesの新schemaが既存baseline以上の誤提案・未解決を出す場合は通常開放せず改善する。高速化未達でも数字を報告し、採用判断と性能目標を分離する。

## 11. 残フェーズと開発モデル

| フェーズ | 開発モデル / 強度 | 実施内容と完了条件 |
| --- | --- | --- |
| 4 バックエンド | GPT-6.1 Sol / High | 新domain/DTO、snapshot、2provider adapter、run/merge・protection metadata、共通compiler接続。保存response・短い境界検証、隔離DBの実HTTP/保存/再送/undoで確認 |
| 5 画面 | GPT-6.1 Sol / High | 解析/案/反映/保存を既存editorへ接続。手動変更保護、複数枠、late response、失敗復旧を対象検証とbuild、隔離Chrome操作で確認 |
| 6 独立監査・回帰 | Astra / High | ID/関連/保護・予算・race・保存/出力の監査、関係する全体回帰。重いテストをこの段階へ集約し、既知回帰を残して完了しない |
| 7 実帳票受入 | GPT-6.1 Sol / High | 固定帳票と複雑な実帳票で速度/品質/手直し完了時間を測定。利用者による案の確認と保存、必要なら実マイク時に利用者を呼ぶ |

フェーズ4はまず保存responseと短いin-memory検証を使う。新provider schemaの実接続確認が必要な場合だけ、既存総予算内で最初の上限8送信を設ける。4/5で性能比較を繰り返さず、まとまった実測は7へ集約する。新規schema未移行の通常DB・通常editing無効は維持する。

フェーズ7の初回実測枠は最大40送信とする。5帳票×旧Responses/新Responses×2反復の20送信、種類だけ5帳票×2反復の10送信、残10を画面受入・不成功の再確認用とする。旧/新は順番を交互にし、同時期の実測も併記する。Phase2の12.672秒をネットワーク条件の異なる唯一の対照にしない。既に結果を確認したH1/H2は固定回帰であり、新しい未知帳票の評価とは区別する。使用台帳の残数が少なければ枠を縮小し、総上限を超える前に報告する。

検証はdeployのSafety Contract §8の現行経路を使う。退役した `verify_ocr_contract.py` や旧Google経路の全件テストは復活させない。各工程で実行した静的確認、保存応答再生、実HTTP、実画面、実AI、実音声を区別して報告する。

## 12. この工程の成果物と確認範囲

本設計とprovider用schemaだけを追加する。実装コード、DB schema、業務layout、通常設定、API台帳には変更しない。開始・終了のソースと全テーブル論理hash、schema/examplesのオフライン確認、文書参照、差分確認を `D:/llm-ocr-archive/template-ai/20261009-phase3` に保存する。検証結果とAPI残数は同directoryの`validation.json`、`preservation.json`を正本にする。

実行した `validate_design.py` はJSON Schemaと例、禁止プロパティ、既存typed operations/手動adapterのin-memory変換、重複行セル・行幅・自己参照等の既存拒否、物理セル保持、文書参照の38確認を通過した。26項目/52セルのManualDraftで現在の `EDIT_BATCH_TOO_LARGE` を再現し、内部上限拡張の必要性を確認した。新しい全体graph validator、run/merge API、protection metadataを実装・検証したという意味ではない。

この工程では製品のprovider接続・UI・保存・実マイクの動作成功を主張しない。意味の正しさ、新schemaの実速度、legacy設定の保護表示の操作性は後続受入で確認する。既存OCR contractを維持し、破壊的変更の許可を既存データの削除・過去snapshot再生成には使用しない。

公式仕様確認（2026-10-09）：[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)、[Decisions](https://developers.openai.com/api/docs/guides/decisions)、[Function calling / streaming](https://developers.openai.com/api/docs/guides/function-calling)。streamingはprogressを早く表示できるが、途中の未検証JSONを保存可能にする仕組みではない。
