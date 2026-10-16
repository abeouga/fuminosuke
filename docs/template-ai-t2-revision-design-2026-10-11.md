# T2ローカル処理の修正設計

2026-10-11。フェーズ24の追加設計審査。**検証済み入力を一つの処理内で共有し、同じgeometryの再生成と実装ファイルの重複読込を減らす。500msの合格条件は維持する。** 実装はまだ行わず、フェーズ25の有料比較は停止したままとする。

対象checkoutは `C:/Users/hanam/Desktop/llm-ocr-deploy`。旧 `llm-ocr` は変更しない。今回の診断・保持監査は `D:/llm-ocr-archive/template-ai/20261011-t2-revision-design` に保存した。API使用は0回、共有台帳は366/496、残130回。設計工程の指定はAstra / XHighであり、実際のチャット設定を検証した記録ではない。

## 判断の比較

以下は実在の専門家への照会ではなく、評価基準を分けた模擬分析である。

専門家A（応答性能）：T2は同じ185セルのgeometryを12回作っている。新規入力から一度だけ生成する構成を最優先とする。外部待機のない処理中に391ファイルを6周読む費用も削減対象。catalog再構築は現状約6msなので優先しない。

専門家B（整合性）：再利用できるのはサーバーが検証して所有する不変入力だけである。現在のrevision、同値手動facet、scope、epoch、画像、実装が変わった場合の拒否は必要。キャッシュのhit、公開digest、`verified=true`だけで検証を省略してはならない。

専門家C（保守性）：下位関数がそれぞれ入力生成からやり直す責務の混在を解消する。検証済み入力、現在性検査、純粋な意味解決、共通compileを明示的に分ける。旧関数のmonkeypatchやAI専用の保存compilerを導入しない。

客観的まとめ：3観点とも、検査の一律削除ではなく、検証の責務と実行境界の整理を支持する。性能だけなら起動時キャッシュが有利だが、初回費用を隠すことと失効管理が問題になる。今回は**runを越えない入力再利用と、外部待機の前後で区切る実装照合**を採用する。schemaの起動時再利用、geometryアルゴリズム変更、並列化は第1実装に含めない。

## 1. 確認できた原因と計測の限界

[フェーズ24結果](template-ai-phase24-2026-10-11.md)のT2中央値は716.8097ms。500msまで216.8097ms、約30.25%の短縮が必要である。最終4実装ファイルのSHAを照合して、今回も同じコードを3回実行した。

| 計測 | 3回の合計時間 | 中央値 | 意味 |
| --- | --- | ---: | --- |
| Phase24の固定結果 | 786.58 / 716.81 / 714.74ms | 716.81ms | 元の不合格結果を保持 |
| 今回の変更なし診断 | 734.44 / 673.26 / 706.24ms | 706.24ms | 未達が再現。再採点・改善結果ではない |

今回の中央値runは準備300.76ms、plan生成62.45ms、応答検査・compile343.03ms。合計706.24msである。入力は185セル、provider packは83セル。oracleによる完全manual意味投影は保持された。

最終版に対するcProfileで次を確認した。時間は計装下の累積値であり、親子関数の時間は重なる。実時間の短縮予測へ足し合わせない。

| 処理 | 呼出し数 | 計装下の累積時間 | 判断 |
| --- | ---: | ---: | --- |
| `candidates.build` | 12 | 311.64ms | 1回の生成＋11回の`CandidateSnapshot.unpack`による再生成 |
| `fingerprint` | 6 | 252.11ms | 391ファイル・約3.07MBを毎回内容照合。2,346回の読込 |
| `jsonschema.check_schema` | 2 | 120.50ms | compact v4と共通unitsの別schema。1個のschemaの二重検査ではない |
| `EvidenceSnapshot.catalog` | 5 | 6.44ms | Phase24で大きな複製問題は既に修正済み。主対象にしない |

11回のunpackはcapture、scope作成、pack構築、resolver、compiler等に分散している。`unpack`自体の294.82msと`build`の311.64msは重複する。旧 `phase24/t2-profile.log` は最終修正前の資料なので、今回の根拠は新しい `t2-final-source-profile.pstats` と `diagnostic-results.json` に限定する。

計時区間は従来どおり入力読込→snapshot→pack→plan→選択検査→共通compile/manual投影。oracle選択・採点は外側、wire組立・モデル・通信・UI・DB保存は含まない。Python import済みで、アプリケーションのrun間キャッシュは使用していない。OSキャッシュは除去していない。706msが音声入力全体の応答時間を意味するわけではない。

## 2. 新しい責務と入力契約

外部試作の内部契約だけを `verified-run-input.v1` に改める。v7のモデル用DTO、factor生成規則、画像/crop、候補と保留の上限、モデル、prompt、意味の採否条件は維持する。旧snapshot・旧実験資料を新しい意味へ書き換えない。

```text
所有元の入力・画像・catalog・手動facet
  → RunGuard開始（実装内容照合・現在状態固定）
  → VerifiedInputFactory（物理ID/geometry/scope/packを検証）
  → 不変VerifiedRunInput
  → factor plan → 発行済み選択の検査 → 共通の意味解決
  → 既存CommandCompiler → 既存ManualCompiler
  → RunGuard終了（実装内容・現在状態を再照合）
  → 検証済みの下書き
```

外部API待機はRunGuardの処理区間を分ける。DBへの保存はこの図の外側で、将来接続時も共通manual-authoring→MutationCore→TemplateAuthoringServiceを使用する。モデル応答を直接保存する入口は追加しない。

### 2.1 入力検証をfactoryへ集約する

`VerifiedRunInput` はfactoryだけが発行するrun内の内部オブジェクトとする。所有binding、期待snapshot、scope/read-write分割、画像bytes、pack、payload/candidate索引、catalog、保護roles/facets、実装proofへ束縛する。

- **新規capture**：所有元から別コピーしたcanonical入力で物理ID・画像SHA・bindingを検査し、既存の`candidates.build`を1回実行する。その出力を所有して凍結する。geometryのアルゴリズムと結果は変更しない。
- **保存snapshotのrestore**：公開digestだけを信用せず、所有元が別に保持するExpected、policy、payload/image digestを照合し、geometryを1回再生成して保存候補と完全比較する。一致した場合だけfactory proofを発行する。
- 新規captureしたものを同じrunでrestoreし直さない。scope、pack、resolver、compilerは同じ検証済み索引を受け取る。内側の参照でgeometryを再生成しない。
- canonical bytesと凍結scalarを正本にし、内部の読み取り専用索引を公開mutable辞書と共有しない。既存compilerがmutable辞書を必要とする部分では今の防御コピーを維持する。factoryが入力を固定した後の呼出し元の変更は、その内部値を変更できない。
- JSONや型名だけからproofを復元できない。別run、別Expected、失効済みproofの混在を拒否する。Pythonプロセス内で任意コード実行を防ぐsandboxという意味ではない。

新規1回・restore1回は設計上の費用上限でもある。ただし呼出し回数だけを合格にせず、不正snapshotの拒否と全意味投影の一致を判定する。

### 2.2 既存resolver/compilerの責務を分ける

新しい外部runtimeコピーで、旧 `scope.py`、`compact.py`、`expand.py`、`evidence_pack.py` の入力取得部分と意味処理部分を分離する。候補の完全性検証を終えた後の処理には、型付きの検証済み入力を明示的に渡す。

旧raw入口を残す場合も必ずfactoryを経由し、内側の意味処理は共有する。`trusted=True`、`skip_validation=True`、snapshotの偽装subclass、`unpack`のmonkeypatchは使用しない。二組の意味解決ロジックを持たず、旧実験を再生するadapterとv7が同じ内側へ接続する。

全claims、保留単位のparent/owner、宣言元依存の閉包、独立部分の採用、範囲外の不変条件は残す。選択前のcompileは0回。選択後は従来の共通意味compileとmanual投影を維持する。T2では内部の `CommandCompiler.compile_operations` は意味案とmanual投影の計2回であり、どちらかを速さのために省略しない。

## 3. 実装照合を現在性検査から分離する

現在の`stamp()`は現在状態の確認と391ファイルの読込を結合している。これを次の2責務に分ける。

| 責務 | 内容 | 実行境界 |
| --- | --- | --- |
| 現在性検査 | 所有・revision・snapshot/scope・catalog・同値手動facet・epoch・画像内容・pack内容 | capture前後、送信前/受信後、解決前、compile前後、返却前。既存の保護を維持 |
| 実装proof検査 | 実際の依存ソース・schema/prompt・ライブラリ情報・policyの照合 | 外部待機のない処理区間の開始と終了 |

初回比較では391ファイルの対象範囲を狭めない。新規内部モジュールもmanifestへ加え、必要ファイルの欠落・追加・内容変更を検出する。mtime、サイズ、entrypointだけのhashや`already_checked`フラグで代用しない。ライブラリ版数の照合をバイナリ全体の完全性証明とは呼ばない。

`RunGuard`は同期処理の開始時に、凍結済み実装manifestと実ファイルを照合し、終端でも再照合する。区間内は外部待機・外部callbackによるコード差替え・reload・別実装の遅延ロードを認めない。読込済みmoduleの由来を起動時のmanifestへ束縛し、作業中に更新したworkerは破棄して新しいprocessで開始する。ファイルを変えて戻す等の任意の悪意あるコード実行への防御は今回の契約外であり、末端hashだけでそれを保証しない。

runのfixture状態を読む現在のcallbackは所有元検査用である。現在値を軽量の世代番号だけへ置き換える変更は行わず、少なくとも現行の内容比較を維持する。製品接続時の正本はサーバーのrevision/所有stateで、反映・保存transaction内の再照合も必要。今回の設計・fixture試験ではDB競合を受入済みと扱わない。

**計時対象のAPIなし経路では実装全読込を6回から2回にする。** APIのある経路では待機をまたいだ使い回しを禁止する。Responses1回は原則2区間・4照合、Decisions2段は原則3区間・6照合となり、途中保留や取消では未実行区間を開始しない。送信前照合は予算予約前に完了し、同期の終了照合から送信までの間にもawaitを挟まない。受信後に不一致なら応答を破棄し、追加送信・再試行・下書き反映を行わない。

「返却直前まで変化を見逃してよい」という仕様にはしない。内側の現在性検査は残し、実装照合の終了前は結果を外へ公開しない。各区間の例外・取消も必ず閉じる。新しい外部待機を加える場合は同じ区間を分割する必要がある。

## 4. 第1実装へ含めない変更

- **schema検査の計時外への移動**：現在は別schemaを各1回検査している。起動時に検証済みvalidatorを共有する案は保守上可能だが、新規初期化費用を500msから除いてはならない。第1実装は現状を維持する。後で試す場合はcold/warmを分け、追加初期化をcold計測に含める。
- **catalog全面再設計**：5回で約6ms。先に手を入れる根拠は弱い。防御コピーと共通compilerを維持する。
- **画像品質、crop、provider入力の削減**：速度のために読取根拠や物理対応を変えない。
- **候補数、保留、検査条件の削減**：32択、64active、512全質問、8192候補要素、最大2段/2送信、retry0を維持する。
- **別モデル、prompt変更、T2専用分岐、手動正解の埋込み、全体並列化**：今回の原因対策には不要で、比較条件を増やすため採用しない。

geometryの隣接判定自体を高速化する案は、上記2変更だけで500msへ達しなかった場合の別設計とする。未達時に自動で追加改造しない。

## 5. 次の実装範囲と受入条件

実装先は新しい外部ディレクトリ `D:/llm-ocr-archive/template-ai/20261011-phase24-repair`。Phase19/24はそのまま保持する。必要なruntimeをmanifest付きでコピーし、import元を検証する。新旧の同名moduleを同じprocessの`sys.modules`で混在させない。製品backend/frontend/DB/schema/設定の変更と、通常有効化は対象外。

修正前に、既存83項目をどの入口で再実行するか、次の新しい境界をどの短い検査へ追加するかを固定する。正常系を言い換えるunitは増やさない。

1. 同一digestを名乗る別payload/候補、並びが変わったphysical IDs、別Expected、手作りproofを拒否する。restoreはgeometryを再計算して完全比較する。
2. 入力・catalog・provider metadataの外側コピーを後から変更しても内部snapshotは変わらない。内部の変更が検出された場合は結果を返さない。
3. 別runのproofを流用できない。現在のrevision・scope・画像・同値facet・epochが途中で変わると、compile/返却/次送信を拒否する。
4. 隔離したコピーで、mtimeとサイズを維持した依存ファイル変更、ファイル追加/削除、schema/policy変更、開始後の実装変更を検出する。履歴ディレクトリそのものを書き換えて試験しない。
5. API待機とDecisions段間で実装が変われば応答・次送信を拒否する。取消、例外、古いreceipt、再起動後の旧proofにも同じ条件を適用する。
6. 保留のparent/所有claimsと依存閉包、独立部分の採用、unknownとignored、名称・alias・共有・mark・行/列・範囲外セル・全ID/pathのno-opを維持する。

品質条件は固定5scopeの完全manual意味投影5/5、既存83境界と変形例の成功、旧30保存応答の26受理/4拒否・全採点/時間/usage保持・受理26件の意味投影と既存graph完全no-op。旧応答の共通処理は**変更後の外部runtimeでも**再生する。未変更の旧runtimeだけを再生して新実装の回帰検証と扱わない。

性能条件は固定5scopeを各3回、**全scopeの中央値500ms以下**。開始/終了境界はPhase24と同じで、新設proofの発行、実装照合、凍結/索引作成等を計時内へ含める。目安として400ms前後を狙うが、これは余裕を持たせる設計目標であり実測予測でも合格閾値でもない。

旧版と修正版は別processから起動し、各processのimport後に同じ未変更oracleを読込む。比較順は反復1/3を旧→新、反復2を新→旧に事前固定。各反復でT1/T2/T3/H1/H2を順に実行し、毎回context/planを新規生成する。初期化を前倒しした場合は新規費用を別欄だけへ逃がさず合計へ含める。全runを記録し、都合の悪い初回の削除・追加試行からの選別・閾値/分母変更を行わない。cProfileは別計測とする。

各3回は既存条件との比較であって、p95や実音声の応答保証ではない。wire/実API/画面表示までの測定は別途必要。source/input/gold/schema/prompt hash、無計装各時間、関数回数の診断、失敗結果も保存する。

## 6. フェーズとモデル

元のフェーズ25/26を改番せず、24の修正工程を挿入する。各モデル切替点で区切る。

| 工程 | 開発モデル・強度 | API | 完了条件 |
| --- | --- | ---: | --- |
| 今回：24修正設計 | Astra / XHigh | 0 | 最終コードで原因確認、再利用/失効境界、計時条件と修正範囲を固定。本書で完了 |
| 次：24修正実装 | GPT-6.1 Sol / High | 0 | 外部runtimeのfactory/処理区間を実装。短い境界確認後、最後に固定5scopeと旧30応答を検証 |
| 24修正審査 | Astra / XHigh | 0 | 保護条件と計時範囲を審査。500ms・品質両条件が通るか判定 |
| 25：有料比較 | GPT-6.1 Sol / High | 最大20送信 | 修正受入後の別指示で開始。固定5scope×2反復、最大2送信、retry0を維持 |
| 26：採用審査 | Astra / XHigh | 原則0 | 実モデル品質・速度を採否判断。通常導入/UI/音声の受入は別 |

APIモデルは既定どおりGPT-6 Luna、Responses low。今回、モデル設定やAPI契約の変更は設計しない。将来の比較枠は未予約・未消費である。

## 7. 今回の検証と変更境界

Phase24の75成果物hashと最終4source hashを照合した。変更なしT2の3回診断・1回profileを実施し、完全意味投影を確認した。これらは原因確認であり、新設計の成功証明ではない。

実行はdeployの `backend/.venv/Scripts/python.exe -B` による外部 `audit_state.py before/after`、`diagnose_t2.py`、文書/成果物照合、および `git diff --check`。製品全件unit、frontend build、サーバー起動、実マイク、実APIは実行していない。今回のファイル変更は本書、[機械可読計画](contracts/template-analysis-v7/t2-revision-plan.json)、AGENTS/Safety Contract/v7索引の追記と外部診断資料だけである。

終了時に製品ソース、両repoのHEAD、両通常DB全37表/schema、設定、業務863ファイル/781 artifact、旧checkout、Phase11〜24証拠を開始時と照合する。既存OCR contractを維持したとの判断はこの不変性確認による。製品全体の現在動作・新API速度・実DB保存・音声UXの受入は未実施である。

**設計の採用案は確定したが、500ms達成は未検証。今回の終了点は設計であり、実装と有料比較は開始していない。**
