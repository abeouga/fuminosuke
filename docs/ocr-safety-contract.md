# OCR Safety Contract

2026-10-18：**意図的なOCR contract変更**（§4.5新設）。印刷の項目名を持たない記入欄（printedLabelTextが空で元の色の切り抜きを送る欄）だけ、同じ欄の手書き部分を元の色で残し印刷部分を白くした画像を、元の画像の直後に追加で送る。指示文に記入の有無の判定と読み方を追加し、prompt versionを`mapped-compact-v2`に上げた。記号・チェックリスト・印刷の項目名を除外済みの欄は変更しない。同じ画像の変更前後比較で、空欄の印字「／」「( )」「電話」が値にならず空欄になり、時刻欄は「午前10時30分」まで読めた。詳細は§4.5。

2026-10-18：**意図的なOCR contract変更**（§3.6新設）。スマホ写真のため、暗い・影のある入力に限り紙の背景を平坦化して位置合わせし、横向きは90°/270°回転も試す。受入閾値・mapping・crop矩形・provider契約は不変、白い入力は画素まで従来と同一。合成写真40条件で位置合わせ成功20→39・欄四隅の最大誤差5.16→2.39px、実OCR3帳票で欄の取り違え0。記録は[スマホ写真・スマホ画面・文字拡大](phone-photo-and-mobile-2026-10-18.md)。以下は従前記録。

2026-10-16：[7-Bの実Fargate試験](web-beta-phase7b-fargate-2026-10-16.md)は製品未接続の `fargate-renderer-feasibility-v1`。通信syscall拒否、権限分離、2GiB実OOM、timeout、取消、起動元喪失と再描画、固定3PNG/FODSの既存経路とのbytes一致を確認した。従来の `linux-container-renderer-v1` admission、v4 candidateや旧資格へ成功を後付けしない。RLIMITプロセス制限・task一時領域・壁時計期限は旧Docker条件と異なり、製品接続前に新実行/graph/candidate policyと所有bindingを明示検証する。本工程では業務backend/frontend、provider/prompt/schema、mapping/crop、snapshot、revision、確定/出力、通常DBを変更せず既存OCR contractを維持した。終了時に別途製品6ファイルの変更を検出して保持したが、固定imageの試験を現在のworktree全体の回帰保証にはしない。正式環境認定・実OCR品質・公開受入の証明ではない。以下は従前記録。

2026-10-16 Fargate移行：[移行設計](web-beta-fargate-plan-2026-10-16.md)へ公開方式を変更し、Terraformと夜間停止の定義を準備した。今回の変更はinfra・運用文書に限定し、業務backend/frontendと通常データ、provider/model/prompt、mapping/crop、revision、確定・出力契約は変更していない。既存OCR contractを維持した。PostgreSQL/S3の保存境界とFargate描画隔離は後続の明示工程で検証し、EC2での成功をFargate受入へ流用しない。23:00〜7:00停止でも未保存値・受付snapshot・予約消費・削除記録を保護する。以下は従前記録。

2026-10-16：[Webベータ修正6-Bの独立再審査](web-beta-phase6b-review-2026-10-16.md)でR1〜R3の解消を確認した。回収の再中断・対象外資源保持・親リンク拒否、復元失敗時の全利用者停止と未完成root起動拒否、管理CLIによる明示再承認・旧cookie拒否を実検証。修正37・独立15・Linux11条件、frontend176/buildが成功。製品コード/テストと通常データは変更せず、確定XLSX SHA・既存OCR contractを維持。次はフェーズ7の実環境受入で、公開判定は未完了。以下は従前記録。

2026-10-16：[Webベータ修正6-A](web-beta-phase6a-repair-2026-10-16.md)で、削除記録のDB行がなくても所有資源だけの残存ファイルを回収する。復元時は全利用者のactiveを0にし、旧sessionを破棄して、管理者が現在の利用許可を確認したsubだけを明示enableする。以前の復元後即時再ログインを廃止し、registry v2/台帳形式/通常DBは維持する。修正前後37条件、認証127・運用41・Linux6・frontend176/buildと確定XLSX SHAを確認。OCR生成・mapping/crop・補正/確定/出力経路は変更せず既存OCR contractを維持。6-B再審査待ちで公開未受入。

2026-10-16：Webベータの[フェーズ6独立審査](web-beta-phase6-review-2026-10-16.md)は修正要求。削除のDB commit後に残るファイルが再適用で回収されないこと、backup後のアカウント停止が復元で失われることを専用データで再現した。製品コード/通常DBは変更せず、既存OCR contractを維持。認証127・保存17・運用39・renderer8条件と確定XLSX SHAは一致。frontend全176件は173成功/3失敗、build成功。限定mypy19成功は依存検査85指摘の解消を意味しない。フェーズ6の公開阻害事項0は未達、フェーズ7は未開始。修正6-Aと再審査6-Bを次工程とする。

2026-10-16 Webベータ：[フェーズ5](web-beta-phase5-2026-10-16.md)で共通送信予約・外部追記・削除再適用・offline backup/restoreを追加。通常DBのschema/924回枠は不変。音声metadataの既存ledger FKは参照mirrorで維持し、共有側だけが送信上限を判定する。rollback・不明・復元で外部予約を払い戻さない。使用済みテンプレートを削除してjobの版IDをNULLにしない。archiveで版ID・保存内容を保持し、未使用だけ版→親の順で削除する。専用同一2ページ入力で旧競合→新archive、job/input/artifact/review/confirmation全表と確定XLSX SHA不変を確認。provider/model/prompt/schema/geometry/association/mapping/crop/補正/確定/出力生成の契約は変更しない。復元は全SHAとSQLite integrity・外部台帳・削除記録を検証してから公開し、未完成rootと旧binaryへのrollbackを拒否。既存OCR contractを維持した。実AWS・実マイク・実負荷は後続受入である。

2026-10-15 Webベータ：[フェーズ4](web-beta-phase4-2026-10-15.md)でpaged専用レビューへの既存guardの判定順を修正。job受付snapshotのjobPagesを、存在しない単一OcrResultの検索より先に判定し、MULTI_PAGE_REVIEW_REQUIREDを返す。単一補正・確定APIへの流入も同guardで拒否する。専用コピーの変更前後でページ/input/reference SHA、確定revision、業務全テーブル、記入済みXLSX SHAを照合し不変を確認。provider/prompt/schema/geometry/association/mapping/crop/補正/確定/出力生成は変更しない。AI案の明示反映・保存、手修正保護と未知timeout時の回復確認を維持する。既存OCR contractを維持した。実マイク・公開受入は後続工程。

2026-10-15 Webベータ：[フェーズ3](web-beta-phase3-2026-10-15.md)の認証境界を追加。cloudはCognito subを共通registryで照合し、固定した利用者別DB/storage/runtime内で既存のTemplate/Job・子資源を処理する。通常DBを移行せず、provider/prompt/schema/physical identity/mapping/crop/revision/確定/出力契約を変更しない。各要求・SQL/commit・storage解決で失効を検査し、SSE/音声にも同じ境界を適用する。停止済み利用者のlifespan cleanupだけに実行fence保存の限定権限を持たせる。127条件の制御OIDC＋実ASGI、18条件の実HTTPSと既存2ページXLSXの内容/SHA一致を確認。実AWS/Cognito受入は後続工程で、有料AIは共通quota接続まで閉じる。以下は従前時点の記録。

2026-10-15 Webベータ：[フェーズ2](web-beta-phase2-2026-10-15.md)でLinux隔離rendererを追加。新規Linux preview=`spreadsheet-preview-linux-v1`、寸法=`linux-fods-physical-length-v1`、renderer=`linux-print-reference-v1`、実行=`linux-container-renderer-v1`、graph=`linux-prepared-artifact-graph-v1`、候補=`paged-candidate-readiness-v4-linux`。旧版/過去job/旧資格の暗黙変換は行わない。v4は人間レビュー前提の未認定候補で、実行証拠・版・原本・全ページ画像・mapping・実装SHAを検証する。限定5組のfont代替を新規Linux準備だけに明示保存し、未知の代替は停止。provider/prompt/schema/association/補正/確定/出力adapterは変更せず、専用実APIで2ページの対応・保存・出力を確認した。Linux workerは固定image・network none・非root・資源制限・接続lease・core guardの中でOffice/PDF/rasterを実行。APIにDocker socketを渡さず、所有marker以外を回収しない。従来の正式資格のPASS化やscope拡張ではない。API0・通常データ保持、Cognito/公開/実マイクは後続工程。

2026-10-15 Webベータ方針：[フェーズ1仕様](web-beta-phase1-2026-10-15.md)を現在地とする。ユーザー指示により精度・5秒応答の改善を保留し、既存品質での製品化へ移る。V5は停止継続、V6修正設計はベータ範囲外。新規ゴールは作成しない。AWSは `abe-deploy-user` / 東京、新規クラウドDB、利用者非公開、現行XLSX・複数ページ対応を維持する。APIベータ追加1,000回は既存440/924・残484の開発台帳と別枠。フェーズ1は文書と保持基準のみで、実装・資格・認証・公開条件の解除ではない。既存のphysical identity・mapping・crop・revision・明示確定を維持し、Linux rendererは新環境の証拠を検証する。以下の過去の「次工程」「ゴールactive」は当時の記録であり、現行工程の指示には使わない。

2026-10-15：V5の6-Dは固定G1-01で停止。[実測記録](template-ai-region-v5-live-2026-10-15.md)。全10input/label一致・範囲外変更0だが相反する保留32、未接続symbol、案0、ASGI11208.887ms。予約1/実API1、440/924・残484、後続未送信。専用revision1、通常業務データ/既存台帳439行/旧checkout/前封印を保持。開始時に既存移行5空テーブルとsession1件追加を確認し現baselineとして記録。通常OCRと製品コードは変更0。[6-A修正設計](template-ai-region-decision-repair-design-2026-10-15.md)でモデル向け判断配列とinline readingを提案。旧RegionAnswer直接生成を伝送専用型＋決定的adapterへ変更する意図を明記し、canonical/geometry/保存/physical identity/mapping/crop/revisionを維持する。未実装、実精度/5秒は未証明。次はGPT-6.1 Sol/Highの6-B、Astra/XHighの6-C。V5再送禁止、V6は実装後審査前の提案。起動許可解決済み、改善ゴールは未完了。以下は従前時点の記録。

2026-10-14：所有範囲付き生成schemaの6-C独立工程審査はAstra/XHighで合格。[審査記録](template-ai-region-scope-review-2026-10-14.md)。全24要求/231参照条件、専用ASGI17＋4条件、保存後の54セル/22意味/22保存先/22cropを確認。旧v8取得/反映/保存拒否、手動同値編集/geometry競合保護を維持。製品変更/API/通常予約0、制御予約7、439/924・残485。通常OCR/データ/旧checkout/前封印/最終4帳票保持。V5は審査済み88送信配分・上限96・未割当8転用不可、実binding未発行。次は6-D=Astra/Highへ切替後の新試行固定。実Luna精度・実画面5秒・改善ゴールは未完了。起動許可は解決済み。以下は従前時点の記録。

2026-10-14：所有範囲付き回答の6-BをGPT-6.1 Sol/Highで実装。[実装記録](template-ai-region-scope-fix-2026-10-14.md)を現在地とする。region-schema.v1/pack.v3/runner.v9、生成時の変更先制限・応答再検証・scope/schema digestを接続。全24要求、実ASGI17＋追加4条件、54セル/22入力/22crop、検出0追加の保存再読込、旧v8案失効を確認。対象3ファイルruff/mypy通過。実API/通常予約0、439/924・残485、専用制御予約12（失敗分含む）。通常データ/旧checkout/前証拠/最終4帳票を保持。V4停止継続、V5の88送信提案は6-C審査待ち・実binding未発行。次は6-C=Astra/XHigh。実Luna精度/実画面5秒/改善ゴールは未完了。以下は従前時点の記録。

2026-10-14：現行製品V4をAstra/Highで実測し停止。[実測記録](template-ai-region-v4-live-2026-10-14.md)を現在地とする。V4-G1-01はreadOnly34セルへのannotations、所有10入力すべて保留。案0、ASGI8399.642ms、専用revision1を保持。後続未送信、実API/通常予約1、439/924・残485。範囲外出力を除いても案0を再現、製品変更0。次の[所有範囲付き回答設計](template-ai-region-scope-repair-design-2026-10-14.md)は生成schemaで変更先を限定し、作業指示を整理する。設計試作のみ、精度/速度未証明。次は6-B=GPT-6.1 Sol/High、6-C=Astra/XHigh、承認後6-D=Astra/High。V4の同一条件再送は禁止、V5条件は実装後審査待ち。通常データ/旧checkout/前証拠/最終4帳票を保持。実画面5秒/改善ゴールは未完了。以下は従前時点の記録。

2026-10-14：現行製品先行のV4測定手順をAstra/XHighで承認。[測定手順審査](template-ai-region-v4-protocol-review-2026-10-14.md)を現在地とする。製品/契約/原画像/gold不変、21新trial、4＋24＋60＝88送信、上限96・未割当8転用不可。現factoryの要求が未送信D referenceと一致、最初の10入力＋見出しと全ページ/R04分母を保持。v3は停止を維持、V4実binding未発行。正本は20261014-region-v4-protocol-review/experiment-allocation-approved.json。製品変更/API/予約0、438/924・残486。次は6-D=Astra/Highへ切替後、新実試行を固定してG1-01から測定。実精度/画面5秒/ゴールは未完了。以下は従前時点の記録。

2026-10-14：6-Dのregion-v3診断AをAstra/Highで実測し停止。[実測記録](template-ai-region-v3-live-2026-10-14.md)を現在地とする。旧指示/旧metadataでreadOnly15セルへのannotations、所有10入力全件保留。VISUAL_ANSWER_REFERENCEで拒否、案0、ASGI5831.501ms、専用revision1を保持。保護違反の固定停止条件に従いB/C/DとG1以降は未送信。現行Dの品質/速度は不明。実API/通常予約1、438/924・残486、製品変更0。次の[測定再設計](template-ai-region-production-evaluation-plan-2026-10-14.md)は現行製品を先頭とするV4/88送信案、上限96・未割当8。6-C=Astra/XHighの測定手順審査待ちで、未送信枠の再開/転用はしない。実画面5秒/ゴール未完了、起動許可は解決済み。以下は従前時点の記録。

2026-10-14：要求簡素化の6-CをAstra/XHighで審査し合格。[独立審査記録](template-ai-region-request-review-2026-10-14.md)を現在地とする。24領域の情報/画像/schema保持、92 binding条件、実ASGI17条件、54セル/22入力/22cropの保存再読込を確認。比較案B/Cの形式説明不整合を製品外の新manifestで修正し、A/Dは不変。次工程の正本は20261014-region-request-review/experiment-allocation-approved.json。92送信配分、上限96、未割当4転用不可、実binding未発行。製品変更/実API0、専用制御予約4、437/924・残487。通常データ/旧checkout/過去封印/最終4帳票を保持。次は6-D=Astra/Highへ切替後に実試行を固定。実Luna精度/画面5秒/ゴールは未完了。以下は従前時点の記録。

2026-10-14：要求簡素化の6-BをGPT-6.1 Sol/Highで実装。[実装・検証記録](template-ai-region-request-fix-2026-10-14.md)を現在地とする。白紙テンプレート向け指示、region-pack.v2の可逆表現、準備時間の内訳を接続。G1 metadata約57%減、24領域合計約49%減、画像/schema/候補保持。専用ASGI14条件、54セル/22入力/22cropの保存再読込、対象3ファイルruff/mypy通過。実API0、437/924・残487、専用予約8（失敗4を含む）。旧案は失効、通常データ/OCR契約/既存差分/過去証拠を保持。新92送信配分は6-C審査前提案、実binding未発行。次はAstra/XHighの6-C。実Luna精度/画面5秒/ゴールは未完了。以下は従前時点の記録。

2026-10-14：6-DのG1-01を実Lunaで測定し不合格。対象10入力すべて保留、案0、ASGI終了5752.1ms。後続G1/G2/G3は未送信で停止。保存応答再現と正解対照10入力の表現可能性を確認したが実精度/画面5秒は未達。製品変更0、API1、437/924・残487。次は要求の明示化・可逆圧縮の6-B=GPT-6.1 Sol/High、6-C=Astra/XHigh、合格後に新実験。起動許可は解決済み。改善ゴールactive。 [実測・停止記録](template-ai-region-live-2026-10-14.md) / [修正設計](template-ai-region-request-repair-design-2026-10-14.md)。以下は従前時点の記録。

2026-10-14：R4の[表示・計時修正の独立再審査](template-ai-region-visibility-review-2026-10-14.md)はAstra/XHighで合格。後着の優先領域の先頭表示、可視性/要約/hit-test、選択前計時とscroll再判定を確認。新しい実ASGI14条件、Chrome明示保存/reloadで54セル/22入力/22保存先、対象31テスト、生成契約と24要求hashを照合。製品変更/API0、共有436/924・残488、制御予約8。実非表示タブ復帰は未検証、guard模擬と区別。次は6-D=Astra/Highへ切替後、実trialを送信前固定して初回4試行。実Luna品質/5秒/改善ゴールは未完了。以下は従前時点の記録。

2026-10-14：R4の[表示・計時修正](template-ai-region-visibility-fix-2026-10-14.md)をGPT-6.1 Sol/Highで実装。計画順のdisplayOrder、変更要約と展開詳細、選択/反映control・要約の可視性/hit-test、scroll等での再判定、手動操作を妨げない表示調整を接続。最終Chromeの後着優先領域は制御応答4807.7ms、54セル/22入力/22保存先の明示保存再読込、入力中focus・位置保持、通常先着と小画面を確認。frontend161/build、backend対象2静的検査、隔離保存14条件通過。実API0、436/924・残488、制御予約24。専用8084/5284・8086/5286稼働。次はAstra/XHighの6-C再審査、合格まで6-Dは未開始。実Luna品質/5秒と改善ゴールは未完了。以下は従前時点の記録。

2026-10-14：6-Cの[独立再審査](template-ai-region-rereview-2026-10-14.md)はR4/P2の修正要求。R1の依存分割・R2の失敗位置・R3の選択待ち混入は解消を確認。先頭対象領域が後着すると画面外でも4851.3msのactionableが付くことを実Chromeで再現し、6-Dは未開始。製品変更/API0、436/924・残488。395ファイル封印と現ソース、24 reference要求と88送信配分を照合。専用8080/5280・8082/5282は起動済み。次はGPT-6.1 Sol/Highの表示・計時修正、続いてAstra/XHighの再審査。改善ゴールは未完了。以下は従前時点の記録。

2026-10-14：6-Bの[修正実装](template-ai-region-repair-2026-10-14.md)をGPT-6.1 Sol/XHighで完了。初期領域の依存・geometry検査、未検出領域の失敗bounds、選択前の操作開始可能計時を修正。隔離ASGI・Chrome保存再読込・frontend159/build・backend対象4ファイル静的検査を通過。実API0、436/924・残488。新測定配分は4+24+60=88送信（上限96、余り8は未配分）。専用8078/5278は稼働中。次はAstra/XHighの6-C独立再審査であり、6-Dへ先行しない。実Luna品質/5秒/改善ゴールは未完了。以下は従前時点の記録。

2026-10-14：開発R01/R02 p1/R03 p1の[採点基準審査](template-ai-region-gold-review-2026-10-14.md)を完了。629自動機能・21手動対象・705必須位置を原画像で固定し、3ページの評価枠をChromeで照合。R03記入例を評価根拠に閲覧したがモデル入力には含めず、最終未使用4帳票は保持。製品6-CはR1〜R3の修正要求を維持し、6-Dへ進めない。API0、436/924・残488。次は指定済みGPT-6.1 Sol/Highへの切替後に6-B修正。起動許可は解決済み。以下は従前時点の記録。

2026-10-14：修正6-Cの[独立審査](template-ai-region-review-2026-10-14.md)は修正要求。初期領域分割の可変親子依存切断、検出0セル領域の失敗位置欠落、actionable計時への選択待ち混入を確認。G3の12送信計画と通常GUIの要求数にも不整合があり、送信前に再固定する。住所/チェックの分離と共通保存は正解fixtureで確認したが、Luna品質・実画面5秒は未検証。製品コード変更0、実API0、共有436/924・残488。専用8076/5276の起動許可は解決済みで稼働を確認。次は6-B修正=GPT-6.1 Sol/High、続いて6-C再審査=Astra/XHigh。6-Dへ先行しない。改善ゴールはactive、過去の封印と通常データを保持。以下は従前時点の記録。

2026-10-14：修正6-Bの制御応答によるChrome受入を完了。[実装・画面検証記録](template-ai-region-implementation-2026-10-14.md)を現在地とする。ユーザーがstart-runtime.ps1の実行を明示許可した後、専用8076/5276を起動しPID/HTTP/コードhashを確認。解析中2/4要求で2案を反映、後続案の追加、手動記号保持、末尾取り消し/再反映、明示保存revision1→2、再読込を確認。22入力先/保存先と54セルID/位置が一致。外部通信を遮断した制御応答で実API0、Luna品質/実画面5秒は未達成の扱い。actionable markの選択待ち混入を6-Cで点検する。completion-1へ新規封印、checkpoint-1と通常データ/旧checkout/前証拠を保持。6-B完了、改善ゴールactive。次は6-C=Astra/XHighへのモデル切替。新規別エージェント未許可、6-D実APIへ先行しない。以下は従前時点の記録。

2026-10-14：修正6-BをGPT-6.1 Sol/Highで実装し隔離検証。[実装・検証記録](template-ai-region-implementation-2026-10-14.md)を現在地とする。製品APIはtemplate-analysis.v3、入力先明示・所有領域・途中反映・receipt履歴・取り消し・明示保存を接続。計画取得は予約0、限定領域は1送信。点検表22入力/54セル、2領域の物理編集/手修正保持、検出0セルのbboxを実ASGIで確認。frontend158/buildと関係17ファイルのmypy/ruff通過。数値表記だけの違いで失効する反例を修正し、client自前hashを不要にした。実API0、Chromeは専用8076/5276の手動起動待ち。6-B/ゴール未完了。Luna品質/5秒/独立審査/最終4帳票は未検証。次工程6-C=Astra/XHighへは画面確認後に進む。新規別エージェント未許可、旧未送信23試行は使用しない。以下は従前時点の記録。

2026-10-14：修正6-Aの[評価準備・設計補足](template-ai-region-evaluation-inputs-2026-10-14.md)を追加。grid圧縮でも表外の年度/名称/月/実施者を処理範囲に残すこと、R03の6旅行記録×各5区間を混同しないことを6-Bへ追加した。R01は192、R02 p1は221の物理位置候補、R03は構造・曖昧箇所を記録。機能goldは未承認で6-Cの送信前審査対象。R04固定goldと最終未使用4帳票は変更なし。製品変更/API0。実行モデルAstra/Highのため6-B=GPT-6.1 Sol/Highへの切替待ち。以下は従前時点の記録。

2026-10-14：修正6-Aの詳細設計をAstra/Highで完了。[入力先・領域単位の設計](template-ai-input-region-design-2026-10-14.md)を次の実装の正本とする。製品コード/API変更は未実装。入力先の明示、原位置画像、所有/依存付き領域計画、途中案の公開と累積反映を設計した。保存応答の逆転・混入、全体完了まで反映できない現行制約を確認。5ページの分割probeとR04の145 core境界横断0件を確認したが、意味精度/5秒は未検証。新実験は最大96送信、先行4件の不合格で停止。旧未送信23試行は再開しない。API0、共有台帳436/924・残488。次は6-B=GPT-6.1 Sol/High、6-C=Astra/XHigh、6-D=Astra/High。別エージェントの新規起動は未許可。この改善ゴールは品質/実画面5秒/保存保持の達成までactiveとする。以下は従前時点の記録。

2026-10-14：修正6の実API初回4試行をAstra/Highで評価し不合格。[実測・停止判定](template-ai-structure-live-2026-10-14.md)を現在地とする。Luna none/lowの全ページ構造要求は通信成功したが、点検表の逆転14件、複雑フォームの見出しへの誤入力先等を専用DB保存・再読込でも確認。R04は固定143機能の合格0、145位置の合格2は既存手動領域の保持のみ。最初の検証済み案はASGI下限9.441〜15.199秒、none点検表は案なし。初回受入で残23試行を未送信とし差戻し、27試行比較・最終評価は未完了。製品コード変更なし、25crop対応・保存応答の手動保護を確認。通常データ/旧checkout/前証拠/最終4原本を保持、実API/通常予約4、436/924・残488。今回の評価ゴールは停止理由・採点・保存対応・修正案・証拠を記録して終了し、製品全体は未完成。次は修正6-Aの入力明示契約と領域分割の詳細設計。以下は従前時点の記録。

2026-10-13：修正5-BをAstra/XHighで独立再審査し合格。[再審査結果](template-ai-row-key-review-2026-10-13.md)を現在地とする。製品5ファイルを読取審査、旧R1と同じ変更内容を実ASGIで拒否。新しい専用先で13シナリオ、追加16境界、既存50/34条件を通過。正常左右番号は3行6mappingで保存。R04の145位置/143機能/手動2領域の固定基準を白紙帳票評価に承認し、元goldを変更せずhash指定のgold-approval.jsonへ記録。前回証拠詳細1件の既知欠損は継承、旧ASGI保存根拠を保持。製品コード変更なし、通常データ/旧checkout/最終4原本を保持、API/通常予約0、432/924・残492。新しい別エージェント起動なし。次は修正6=GPT-6.1 Sol/High、Luna最大27回に初回4回を含める。実Luna品質/5秒/最終評価は未完了、ゴール未完了。以下は従前時点の記録。

2026-10-13：修正5-AをGPT-6.1 Sol/Highで実装。[行番号対応・R04基準の記録](template-ai-row-key-repair-2026-10-13.md)を現在地とする。共通record_geometryで行番号と物理行の一意対応・所有・入力との重なりを検査し、analysis/共通records/TemplateAuthoringService.prepareへ接続。前回の中間行偽装・直接保存・意味変更なし保存を拒否、正常な左右番号は3行6mappingで保存再読込。resolution policy v2で旧案のGET/承認後保存を拒否。新13シナリオ、既存50/34/86/45/112、対象5ファイルruff/mypy通過。依存先mypy61件は変更前と同一。R04は145位置/143機能/手動2領域の許容範囲・同等表現・評価行を固定、独立審査待ち。前回compiler詳細1ファイルを操作ミスで上書きし復元不能、元ASGI保存証拠・要約・manifestは保持し例外を記録。通常データ/旧checkout/最終4原本は保持、API/通常予約0、432/924・残492。次は修正5-B=Astra/XHigh独立再審査、合格まで修正6を開始しない。製品Chrome/実Luna/最終評価未完了、ゴール未完了。以下は従前時点の記録。

2026-10-13：修正5のAstra/XHigh独立審査を実施し、実API比較は差戻し。[審査結果](template-ai-structure-review-2026-10-13.md)を現在地とする。表の中間行を行番号として申告すると欠落検査を迂回し、実ASGIの案反映・共通保存・別接続再読込で6欄が4mappingになるR1/P1を再現。既存50＋34境界は通過。R04の122観察・5曖昧群を審査したが許容領域/採点規則未固定でgold未承認。今回、新しい別エージェント起動なし、製品コード変更なし、実API/通常予約0、432/924・残492。通常データ・旧checkout・前証拠・最終4原本を保持。次は修正5-A=GPT-6.1 Sol/HighでR1修正とgold整備、修正5-B=Astra/XHighで再審査。合格後に修正6/7へ戻る。審査作業終了・合格条件未達、ゴール未完了。以下は従前時点の記録。

2026-10-13：修正4の画面接続・専用Chrome受入を完了。[画面接続記録](template-ai-structure-ui-2026-10-13.md)を現在地とする。新設/変更前後/除去の同時表示、枠の途中描画、入力中スクロール保留、表名追従、手描き後の旧案失効を確認。再解析時の旧touch混入・手描き初期値の比較・取り消し通知を修正。frontend全154件/build、実Chrome10シナリオ、原本投入/下書き/取り消し/手修正/明示保存/再読込、R04の無関係77セル保持を確認。有料providerだけ制御し実API/通常予約0、432/924・残492。backend製品・通常データ・旧checkout・前証拠・最終4原本を保持。次は修正5=Astra/XHigh独立審査、修正6=実Luna比較、修正7=最終評価。実Luna品質/5秒・OSファイル選択・実マイクは未検証、ゴール未完了。以下は従前時点の記録。

2026-10-13：修正4の画面接続を実装、Chrome受入は手動起動待ち。[画面接続記録](template-ai-structure-ui-2026-10-13.md)を現在地とする。新設/変更前後/除去枠の表示、個別位置表示、入力中スクロール保留、枠手修正後の旧案失効を接続。frontend全151件/build通過。実画面・原本投入から保存/再読込は未確認。専用8074/5274の起動が自動承認レビューでblocked by policyとなり、手動起動を依頼済み。実API/通常予約0、432/924・残492。backend製品・通常データ・旧checkout・前証拠・最終4原本を保持。修正4・ゴールとも未完了。以下は従前時点の記録。

2026-10-13：共通物理編集の修正3を実装。[矩形編集・共通保存の記録](template-ai-structure-v1-2026-10-13.md)を現在地とする。GeometryCompilerで手描き/AIの物理検査・順序を共通化し、structure.v1の矩形＋意味を一要求/依存単位へ接続。公開API v2・runner v5、geometryの同値手修正/削除/取り消し保護、検出0枠からの開始を実装。新34境界・構造実ASGI112・既存50/86/45・frontend19/build・対象24ファイルのruff/mypy通過。未保存手描き82セルと無関係77セル保持、開発5ページ各1要求の準備を確認。通常データ/設定/台帳/旧checkout/前回証拠/最終4原本を保持、実API/通常予約0、432/924・残492。R04全入力一覧は122観察・5曖昧群の審査待ちで、正解採点には未使用。修正4=GPT-6.1 Sol/Highの画面接続、修正5=Astra/XHighの独立審査、修正6=実Luna比較、修正7=最終評価が残る。Chrome・実Luna品質/5秒・全体回帰は未完了。ゴール未完了。以下は従前時点の記録。

2026-10-13：接続検査の修正2を実装。[実装・保存検証](template-ai-form-binding-v2-2026-10-13.md)を現在地とする。form.v2と通常欄/表列の共通BindingResolver、行列/途中セル検査、全行の根拠/readSet、旧定義の反映/保存拒否を共通経路へ接続。変更前R1の4反例と旧定義の反映/保存を再現し、新50境界・実ASGI表86・通常欄/規則表45、対象9ファイルのruff/mypy通過。1行表・不規則3行表・見出しなし列・記号列の保存/再読込を確認。通常データ/台帳/旧checkout/前回証拠/最終4原本を保持、API/通常予約0、432/924・残492。新schemaの実Luna受入/品質/5秒・独立再審査・Chrome・矩形AI・最終評価は未完了。次は修正3=GPT-6.1 Sol/Highの共通物理編集、続いて修正4の画面接続。ゴール未完了。以下は従前時点の記録。

2026-10-13：接続検査と矩形AI編集の統合設計（修正1）を作成。[統合設計](template-ai-structure-repair-design-2026-10-13.md)を次の実装の正本とする。通常欄/表列の共通binding、共通GeometryCompiler、矩形差分と意味付けの一要求化、geometry保護、明示反映/共通保存を定義。R1の製品外契約試作12条件、全5ページの候補容量を確認。新規手描き5枠の順序差で解析が拒否されるR3も再現。[別担当の独立審査](template-ai-form-links-independent-review-2026-10-13.md)で既存8モジュールのR1/R2を再確認し、実API比較は差戻し。新設計の製品実装/独立審査・Luna品質・5秒・Chrome・未使用4帳票評価は未完了。製品コード/通常データ/既存証拠を保持、API/通常予約0、432/924・残492。次は修正2=GPT-6.1 Sol/Highの接続検査実装。ゴールactive。以下は従前時点の記録。

2026-10-13：複雑フォームの追加審査で制約迂回を再現。[追加審査](template-ai-form-links-review-2026-10-13.md)を現在地とする。普通の欄を1行1列表として返すと、見出しなし根拠の欠落／未発行の離れた見出しへの接続が、実ASGIの案表示・反映・明示保存を通過。R1/P1として表回答の契約修正が必要。R04も矩形欠落3・複合6・重複1があり、既存79矩形だけで原本全体の完成を評価しない。実API比較22/66回は未実行。現在と実装後半の実行モデルがともにAstra/XHighのため同一担当の追加審査とし、別担当の独立審査完了は主張しない。製品ソース変更なし、API/通常予約0、432/924・残492。通常データ・旧checkout・実装証拠・最終4原本を保持。ゴールactive。以下は従前時点の記録。

2026-10-13：複雑フォームの接続修正を実装。[実装・保存検証](template-ai-form-links-2026-10-13.md)を現在地とする。form.v1の候補明示選択・共通親/記号設定・根拠付き見出しなし指定を共通compilerへ接続。正解選択による20接続/22欄/19記号と、規則表190欄の共通保存・再読込を確認。新境界39＋12、実ASGI保存45、既存47、対象ruff/mypy通過。5ページ全17単位を準備可能。実Luna精度・5秒・独立審査・Chrome・矩形AI補助・最終4原本は未検証。API/通常予約0、432/924・残492。初回比較12回は計算誤りで、全ページ3反復66回（1反復22回後に判定）へ訂正。次はフェーズ3=Astra/XHighの独立審査。ゴールactive。以下は従前時点の記録。

2026-10-13：複雑フォームの接続修正を設計。[修正設計](template-ai-form-link-design-2026-10-13.md)を次工程の正本とする。保存応答low22/none23宣言がすべてdirectで、原画像の横18＋縦2接続が0/20と確認。意味推測なしの接触候補204件に20/20が到達したが、追加metadata4,929文字で速度効果は未検証。候補選択・根拠付き見出しなし指定・保留、共通親/記号設定のform.v1、共通compiler/保存、15受入条件と最大12回の初回比較を定義。製品コード/実API/新schema受入/独立審査は未実施。今回API/予約0、432/924・残492。矩形AI補助・原本投入・未使用4帳票・手作業比較も未完了。次はフェーズ2=GPT-6.1 Sol/Highの実装、フェーズ3=Astra/XHighの独立審査。ゴールactive。以下は従前時点の記録。

2026-10-13：規則的な表の専用回答形式を追加。[Luna推論量・表の短縮実測](template-ai-grid-profile-2026-10-13.md)を現在地とする。初回4回は同じ画像/要求でlow/noneを比較し、点検表の接続漏れと汎用role宣言の矛盾を確認。全体none化は不採用。発行済み完全グリッドだけ短いschema/noneへ接続し、追加2回は4.339/4.249秒、19行×10列・190保存先・共通保存再読込を確認。新境界27/既存実ASGI47/対象ruff・mypy通過。複雑フォームは元のlow要求を保持。実API/通常予約6、432/924・残492。通常データ・既存426台帳行・旧checkout・前回証拠・最終4原本を保持。全帳票5秒/独立審査/原本投入からの最終評価は未達、ゴールactive。以下は従前時点の記録。

2026-10-13：実回答の役割矛盾を依存関係ごとに保留する修正を実装。[矛盾分離・保存検証](template-ai-conflict-isolation-2026-10-13.md)を現在地とする。点検表の全体拒否を再現し、同じ回答から20欄・11単位を提示、c48/c49/c50は無変更・未解決。不明参照/読取専用/固定役割の拒否を維持。新境界69・実ASGI共通保存37・旧47/102・契約変更を明記した行列回帰44・対象ruff/mypy通過。実API/通常予約0、426/924・残498。通常データ・既存台帳・旧checkout・前回証拠・最終4原本を保持。5秒/モデル品質/独立審査/原本投入からの最終評価は未達、ゴールactive。以下は従前時点の記録。

2026-10-13：追加500回の許可を使用済み424回から反映し、上限924。[予算・遅延計測](template-ai-latency-2026-10-13.md)を現在地とする。初期イベント/本文開始/終端を分離し、制御HTTP5条件・既存実ASGI47条件・対象ruff/mypy通過。同条件実Luna2送信で点検表は本文9.896秒、c48/c49/c50の役割矛盾により17.015秒・拒否。記録簿は5.491秒でready。速度改善は未実装、部分保留は診断のみ。現在426/924・残498。通常業務データ・既存424台帳行・旧checkout・前回証拠・最終4原本を保持。5秒/安定品質/独立審査/最終評価は未達、ゴールactive。以下は従前時点の記録。

2026-10-13：修正済みschemaで実Luna案の提示とChrome訂正・保存を確認。[実測・画面検証](template-ai-schema-live-2026-10-13.md)を現在地とする。農薬記録簿190欄はASGI 7.408秒、点検表54セルは19.497秒でready。点検表は18接続と1名称などを明示訂正して22保存先を共通保存。Chrome追加1送信で案表示10.243秒、明示反映・保存・表示名訂正・再読込を確認。5秒と点検表品質は未達、未知4資料・独立審査は未実施。製品実装/設定変更なし。今回実API/通常予約3、424/496・残72。通常データ・既存421台帳行・旧checkout・前回証拠・最終4資料を保持。専用試験サーバー停止、ゴールactive。以下は従前時点の記録。

2026-10-13：画像提案の短縮方式を実Lunaで2送信し、回答schemaの不足を修正。[実測・修正報告](template-ai-compact-live-2026-10-13.md)を現在地とする。農薬記録簿は10列名が一致、終了5.854秒だが表名nullで拒否。定期点検は19記号欄に意味が付いたが接続方向の矛盾で18.347秒・拒否。双方の利用可能な案はなし。新schema12条件、既存35/47/102、制御HTTPの実ASGI訂正・保存35、対象ruff/mypy通過。実API/通常予約2、421/496・残75。通常業務データ・既存419台帳行・旧checkout・前回証拠・最終4資料を保持。修正後schemaの実OpenAI受入・Luna品質・5秒・Chrome・独立審査・最終評価は未完了、ゴールactive。以下は従前時点の記録。

2026-10-13：セル番号付き切り抜きと物理行列の共通展開を実装。[修正報告](template-ai-visual-compact-2026-10-13.md)を現在地とする。visual.v3で意味と記号設定を一体化し、省略欄は未解決のまま独立案を提示。R01 metadata 49,568→985文字、R02 13,719→3,589文字。製品解析へ接続、35新条件・実帳票コピーの実ASGI27条件・旧47/102/74/36/46/13条件・対象ruff/mypy通過。公開API6schemaは不変。実API/通常予約0、419/496・残77。通常データ/旧checkout/実応答/最終4資料を保持。Luna品質/5秒/Chrome/独立審査/最終4資料の評価は未完了、次は条件を固定した開発資料の再実測。ゴールactive。以下は従前時点の記録。

2026-10-13：実Luna画像提案の開発評価2送信を実施。[実測報告](template-ai-visual-live-2026-10-13.md)を現在地とする。自主点検54セルは記号欄の意味欠落で拒否。農薬記録簿200セルは19行×10列を認識したが2〜10列の意味がずれ、品質未達。最初の利用可能な案15.098秒で5秒未達。専用コピーの171欄を訂正し、共通保存・再読込を確認。製品ソース/設定は変更なし、通常業務データ/旧checkout/既存証拠/最終4資料を保持。実API/共有予約各2、累計419/496・残77、従来比較70回は未使用・未割当7。次は参照付き画像入力、検証済み行列の共通展開、記号意味の一体化、独立単位の提示をAPI0で修正する。実Chrome/独立審査/最終評価は未完了、ゴールactive。以下は従前時点の記録。

2026-10-13：未保存の意味付けを保持したAI解析・案反映を実装。[下書き意味付けの修正報告](template-ai-draft-semantics-2026-10-13.md)を現在地とする。共通compilerのID対応をプレビューだけに戻し、通常欄/記号/checklist/表を事前保存せず解析できる。複数回反映と途中の手修正、最後の一度の共通保存を専用DBで確認。記号を含むAI/手動由来照合も修正。新16/12/102条件、既存74/36/46/13/47条件、frontend17件、ruff/mypy通過。実API0・通常予約0、417/496・残79。通常OCR/データ/旧checkout/既存成果物/最終4資料を保持。実Luna/5秒/Chrome/独立審査/最終評価は未完了、ゴールactive。以下は従前時点の記録。

2026-10-13：画像提案を解析サービスへ接続。[接続・検証報告](template-ai-visual-runner-2026-10-13.md)を現在地とする。全体画像＋範囲画像、visual.v2、共有API予約、順次公開、ページ内部分成功、二重実行防止、予約直後の版/画像検査、明示反映・共通保存を実装。専用DB/制御HTTPの実ASGI48条件、既存74/36/46/13条件、frontend17件、対象ruff/mypy/build通過。実API0・通常予約0、417/496・残79。Luna品質/5秒/Chrome/独立審査/最終4資料の採点は未完了。未保存の新規意味IDは予約前に保存を要求。通常データ/旧checkout/封印を保持、ゴールactive。以下は従前時点の記録。

2026-10-13：記号・チェックリスト・見出し名の画像提案契約と共通保存を修正。[意味付け・保存の修正報告](docs/template-ai-visual-semantics-2026-10-13.md)を現在地とする。visual.v2とmarkの行別保存先、whole-cell設定時の分類拒否と3接続消失を修復。36条件、実ASGI/専用DB保存46条件、共通13条件、既存入力74条件、対象ruff/mypy通過。共通manual/commandsの2ファイルは通常編集にも影響する。新しいrunner/provider/画面接続、独立審査、Luna品質、実画面、5秒条件は未完了。公開原本4種類を最終評価用に選定・固定したが未採点、開発転用禁止。API0・予約0、417/496・残79。通常DB/既存成果物を保持、ゴールactive。以下は従前時点の記録。

2026-10-13：画像を根拠とする入力準備と共通編集への変換を追加。[画像入力の実装](template-ai-visual-input-2026-10-13.md)を現在地とする。製品ソースvisual_input/visual_compileの2モジュールを追加したがrunner/provider/画面は未接続。実帳票5ページ841セルで文字情報なしの入力準備、74条件と対象ruff/mypy通過。実API・モデル品質・画面保存の合格ではない。記号欄・未設定見出し依存・API予算/公開制御の統合・独立審査・未知4系統・実画面保存を継続する。API0、417/496・残79。通常OCRと通常データ不変、ゴールactive。以下は従前時点の記録。

2026-10-13：継続ゴールの実帳票入力検証で追加制約を再現。[実帳票入力報告](template-ai-real-input-findings-2026-10-13.md)を現在地とする。4公開原本・5対象ページを取得/確認。deployの印字OCR依存欠落で全件送信前保留、隔離した既存OCRを加えても見出し越境・結合構造の候補不足・全ページ上限を確認。今回4系統は修正設計に用いる開発資料、未知評価の合格には数えない。製品・prototype3・通常データ不変、API0・417/496。次は画像を根拠に提案できる入力と表単位の処理へ修正し、共通保存・独立審査・実API・未知4系統・実画面検証を継続する。ゴールactive、製品採用未認定。以下は従前時点の記録。

2026-10-13：継続ゴール「GPT-6 LunaによるAIテンプレート作成の実用化」に基づく修正を実施。現在地は[実用化修正報告](template-ai-readiness-2026-10-13.md)。製品外のprototype3で記号フォームの候補到達29/30→30/30、T2中央値510.06→482.88ms。限定回帰通過、実API/共有予約0・417/496。旧保護回帰の移植・独立審査・実API・実帳票4系統・実画面保存は未完了。ゴールactive、製品採用は未認定。以下は各工程時点の履歴。

2026-10-13：**Phase34-Eは未完了。資料の候補到達性とT2時間基準が未達のため修正設計へ戻す**。[34-E報告](template-ai-phase34e-2026-10-13.md)と[結果JSON](contracts/template-analysis-v8/phase34e-result.json)を現在地とする。
製品外コピーで再生/実送信の分離、HTTP入口の一回許可、固定experiment/追記専用記録、共有予約ID、明示goldラベルを実装。新境界18+21+8+10、pair23+上限6、旧30gold、正例回帰2、旧82応答/24 DTOの歴史再生を確認。ただし全面的な旧335項目の新入口移植、出典/負例意味/画像の独立審査は未完了。
新資料はoracle29/30。V3-09の3行記号フォームが候補不足で、候補生成を変えない34-D境界に達した。T2ローカル中央値は583.22→515.46ms、500ms未達。実応答時間・モデル精度は未検証。候補/正解/分母は合格させるために変更しない。
実API/共有予約0、累計417/496・残79。製品未接続、34-C差戻しを維持。次はAstra/XHigh・API0の修正設計、その後34-E完了と34-F独立審査。35〜38は保留。以下は従来時点の記録。

2026-10-12：**Phase34-D修正設計完了。実装・実APIは未開始、34-Cの差戻しを維持**。[34-D設計](template-ai-phase34d-design-2026-10-12.md)と[計画JSON](contracts/template-analysis-v8/phase34d-plan.json)を現在地とする。
R1は再生/実送信の分離とHTTP境界の一回許可、R2は固定experiment ID/記録先/共有予約IDの対応、R3は全資料の出典・派生審査、R4は値の知識不足と意味設定不能の区別で修正する。cohort v3、校正12/評価18、D1〜D12受入条件を定義。候補prototype2/回答v8/GPT-6 Luna/閾値/共通manualは維持。新資料・新動作は未検証。
今回使用/予約0、累計417/496・残79。製品/旧checkout/両DB/通常データ/台帳/原版/旧本文を保持、既存OCR contractを維持。次は34-E=GPT-6.1 Sol/High・API0実装、34-F=Astra/XHigh・API0再審査。指示まで未開始。34-F合格まで35〜38は保留、上限18/24/28の70回・未割当9回を未予約で保持。以下は従来時点の記録。

2026-10-12：**Phase34-C独立審査は差戻し。実比較・製品採用は未承認**。[34-C審査](template-ai-phase34c-review-2026-10-12.md)と[結果JSON](contracts/template-analysis-v8/phase34c-result.json)を現在地とする。
API0入口の実HTTP通過、journal root変更による同一trial再実行、校正由来3評価資料、文脈不足gold2件をR1〜R4として指摘。既存335+新pair23+実験41+旧審査5+reader6は再実行通過。33画像の越境0、旧30gold一致、旧30/10/42応答と24 DTO歴史再生、5対象のローカル中央値500ms以下（T2 477.3ms）。これらは新指摘の免責や実応答速度の保証ではない。
今回使用/予約0、累計417/496・残79。製品/旧checkout/両DB/通常データ/台帳/元成果物/旧本文を保持し、既存OCR contractを維持。次工程案34-D=Astra/XHigh・API0修正設計、34-E=GPT-6.1 Sol/High・API0実装、34-F=Astra/XHigh・API0再審査。指示まで未開始。35〜38は保留、上限18/24/28の70回・未割当9回は未予約。以下は従来時点の記録。

2026-10-12：**Phase34-B製品外修正実装・API0検証完了。独立審査待ち、実比較/製品採用は未承認**。[34-B報告](template-ai-phase34b-2026-10-12.md)と[結果JSON](contracts/template-analysis-v8/phase34b-result.json)を現在地とする。
R1の記号欄6組を補完して旧30件27/30→30/30。単独欄の固定親候補、文字所属を検査した新30枚＋修正回帰3枚、校正6family×正負2件（全12件Decisions）と評価18件を固定。凍結cohort/選定/trial/runを結合し、入力生成前から永続attemptを消費、重複・再起動・応答混入・予約後/公開前改変を拒否する。
既存境界335、新pair23、新実験境界41、追加5シナリオ、旧rawの旧実装コピー再生が通過。trial照合を含む5対象ローカル中央値は全て500ms以内。実モデル精度・実応答5秒は未検証。独立gold/画像/source family審査と実帳票4family条件は未達、合成結果だけでは採用しない。
今回API/予約0、累計417/496・残79。製品ソース・両DB/通常データ・台帳・原版/過去証拠・旧本文を保持、既存OCR contractを維持。次は34-C=Astra/XHigh・API0。合格まで35〜38は保留、上限18/24/28の70回・未割当9回は未予約。モデル切替後の指示まで次工程を開始しない。以下は従来時点の記録。

2026-10-12：**Phase34-A修正設計完了。実装・実APIは未開始、Phase34の差戻しを維持**。[34-A設計](template-ai-phase34a-design-2026-10-12.md)と[計画JSON](contracts/template-analysis-v8/phase34a-plan.json)を現在地とする。
R1〜R4を、欄内記号凡例の有限候補、単独1項目/1階層の固定親候補、文字越境の検査、凍結cohortとtrial/runの結合で修正する設計。候補policyをprototype2へ改訂し、旧receiptを転用しない。回答v8・全claims・物理/現在性/同値手動編集・共通manualは維持する。
校正v2は6元family×正負2件の12件、全件Decisions適格を受入条件とする。旧30件は原版を保持して開発/回帰へ移し、最終評価18件は別の元資料で準備。新規実帳票4family条件は未達、合成比較の成功だけでは製品採用しない。
比較割付は正確なID/split/family/hash/順序に固定し、予約前後・公開前までtrialを照合する。35/36の結果と選定成果物なしに後続計画を発行しない。一trial一attempt、retry/返金0、ローカル5scope中央値500ms以下と実応答5秒の条件を維持。
今回API0、累計417/496・残79。製品コード・実装原版・両DB/台帳・通常データ・過去証拠・旧本文を保持、既存OCR contractを維持。次は34-B=GPT-6.1 Sol/High・API0の製品外修正実装、続いて34-C=Astra/XHigh・API0の独立再審査。合格まで35〜38は保留、上限18/24/28の70回・未割当9回は未予約。モデル切替後の指示まで次工程を開始しない。以下は従来時点の記録。

2026-10-12：**Phase34独立審査完了。Phase33は差戻し、実API比較へ進めない**。[Phase34審査](template-ai-phase34-review-2026-10-12.md)と[審査JSON](contracts/template-analysis-v8/phase34-review.json)を現在地とする。
R1：V08/V09/V10の6記号pairが候補不足。R2：K02/K04/K06の6ラベルが値欄へ26px越境。R3：比較割付が同一scopeの重複を拒否しない。R4：校正に記号・親子対応・親競合・範囲端文脈欠落・画像/文字矛盾の被覆が不足。原版・goldは保持。
既存境界335項目＋独立5シナリオ通過、保存42試行=41受理/1同一拒否。独立gold内部照合30/30、期待DTOのmanual再生30/30、有限候補からの到達27/30。原画像30枚を確認したがデータ受入は不合格。T2ローカル中央値400.38ms、全5scopeで500ms以下。実通信5秒・モデル精度の改善証拠ではない。
製品ソース・審査対象runtime・両DB全表/台帳・通常データ・過去証拠・旧本文を保持、既存OCR contractを維持。今回API0、累計417/496・残79。製品不採用・未接続を維持。
次工程案は34-A=Astra/XHighの修正設計、34-B=GPT-6.1 Sol/Highの製品外修正、34-C=Astra/XHighの独立再審査（すべてAPI0）。合格まで35〜38は保留、計画上限70回・未割当9回は未予約。次工程はモデル切替後の指示まで開始しない。以下は従来時点の記録。

2026-10-12：**Phase33の製品外実装・境界検証を実施。データ受入未達のため実API比較は停止**。[Phase33報告](template-ai-phase33-2026-10-12.md)と[結果JSON](contracts/template-analysis-v8/phase33-result.json)を現在地とする。
候補の設定結果を示す説明、固定されたDecisions採用policy、設定/receiptの所有検査、比較割付を実装。候補集合・v8回答schema・全claims・共通manual・物理/現在性/同値手動編集を維持。製品コードと通常UIへの接続はない。
境界335項目通過、旧42試行=41受理/1同一拒否を再現。全5scopeのローカル中央値500ms以下、T2=403.17ms。HTTP/実台帳/証拠保存を含まず、実応答5秒やモデル精度の改善証拠ではない。
新規合成30範囲の期待投影は27/30。V08/V09/V10の単独記号pairを既存有限候補で表現できず、期待値は保持。校正の単独記号被覆と新規実帳票4familyも未達。独立gold審査前、製品不採用を維持。
API今回0、累計417/496・残79。製品ソース・両DB全表/台帳・通常データ・過去証拠・旧本文を保持し、既存OCR contractを維持。次は34=Astra/XHigh・API0で実装/期待値/候補被覆不足の審査。データ受入未達を解消せず35〜37の実送信へ進めない。次工程はモデル切替後の指示まで開始しない。以下は従来時点の記録。

2026-10-12：**Phase32修正設計完了。Phase31の製品不採用を維持し、runtime修正・製品接続は未開始**。[Phase32設計](template-ai-phase32-design-2026-10-12.md)と[計画JSON](contracts/template-analysis-v8/phase32-plan.json)を現在地とする。
候補を選んだ設定結果とname所属を示す入力表現を設計。回答v8/schema・候補集合・全claims・共通compiler/manual・物理/現在性/同値手動編集を維持する。指示だけ→候補説明だけを変える18回の比較後、独立したcontext質問とchoiceの24回校正を行う計画。一律閾値解除・保留の強制補修はしない。
既知5scopeとE12の6説明例をAPI0で照合。製品精度・応答速度の改善証拠ではない。新規校正12/評価18はfamilyで分離し、既知E01〜E12・旧校正を最終評価へ流用しない。次は33=GPT-6.1 Sol/High・API0の製品外実装、34=Astra/XHighの審査、35=Sol/High最大18、36=Astra/XHigh最大24、37=Sol/High最大28、38=Astra/XHigh採否。各段階の不合格で後続送信を止める。
今回API0、累計417/496・残79。将来比較の計画上限70・未割当9、今は未予約。製品ソース・DB・通常データ・過去証拠を保持し、既存OCR contractを維持。次工程はモデル切替後の指示まで開始しない。以下は従来時点の記録。

2026-10-12：**Phase31採否審査完了。30-Dの測定記録と不合格判定を受入、v8の製品不採用を確定**。[Phase31審査](template-ai-phase31-review-2026-10-12.md)と[審査JSON](contracts/template-analysis-v8/phase31-review.json)を現在地とする。
791封印成果物・953実行固定hash、41要求/原応答/台帳、82画像を照合。保存42試行=41受理/1同一拒否、別採点と全計測を再現。A品質4/10・5秒以内1/10、B/C品質0/10、評価完全一致1/10・正しい保留2/2を維持。
v8 Responses23件中22件は生の構造回答で保留。B/C T3は第一候補28/28正解でも採用8/28。閾値を外すAPI0診断で5件は正答へ復元するが、保留対象E12にも誤った見出し案が生じる。E09正答.04/.02に対しE12誤答.12/.03（confidence/確率差）で、一律の閾値引下げは採用しない。30-DのT3説明はrow不足ではなくrole/binding/mark不足と訂正、旧証拠は保持。
API今回0、累計417/496・残79。製品ソース・両DB全表/台帳・通常データ・過去証拠を保持し、既存OCR contractを維持。製品接続なし。次はPhase32=Astra/XHigh・API0で判断用入力表現、保留と校正、比較の切り分け、独立評価集合の修正設計を推奨。次の指示まで未開始。以下は従来時点の記録。

2026-10-12：**30-D固定実API比較を完了、修正版v8は採用基準不合格**。現在地は[30-D報告](template-ai-phase30d-2026-10-12.md)と[結果JSON](contracts/template-analysis-v8/phase30d-result.json)。既知5scope×2×A/B/C＋評価12の42試行を完了、実送信41（Responses31/Decisions10）、E11は送信前保留0、retry/warmup/補修0。
Aは品質合格4/10・5秒以内1/10・合格例中央値6228.87ms。B/Cは品質合格0/10、各held8/partial2で完全manual投影0/10。CのT2 ready中央値5520.07msも正答時間ではない。評価は完全一致1/10・正しい保留2/2、残る正答対象9件は保留。元画像Bも失敗しており圧縮単独の原因とは断定しない。
953実行固定hash、41要求/原応答/台帳の一致、保存再生42件=41受理/1同一拒否を確認。API今回41、累計417/496・残79。製品ソース・業務データ・過去証拠を保持、通常DBの変更はAPI台帳のみ。既存OCR contractを維持。製品接続なし。
次はPhase31=Astra/XHigh・API0の採否審査。現在のチャットモデル自体は未確認。次の指示まで開始せず、固定結果を追加送信で補修しない。以下は従来時点の記録。

2026-10-12：**30-C独立再審査を通過**。現在地は[30-C審査](template-ai-phase30c-review-2026-10-12.md)と[審査JSON](contracts/template-analysis-v8/phase30c-review.json)。製品外30-B実装を固定比較へ進める版として受入。差戻し指摘なし。製品採用・実モデル精度・実通信5秒の合格ではない。
開始時927成果物・401実装を照合。コピーで既存201＋修正51境界、保存40応答の35受理/5同一拒否、合成24scopeと既知5scopeのmanual投影を再現。審査側9条件で最終検査後の中止/同値編集を送信0、8件再入を1予約/1送信へ限定。予約応答喪失、要求bytes変更、書込失敗、proof改変も拒否。
各3反復のT2再測定=395.21ms、全5scope中央値500ms以下。保存された変更前後計測を再計算し一致。5scopeの元crop・転送画像・完全要求本文も変更前後一致。oracle/mockであり実通信時間の証拠ではない。
今回API0、376/496・残120。製品ソース・通常データ・原本を保持し、既存OCR contractを維持。次は30-D=GPT-6.1 Sol/High、GPT-6 Luna最大42送信・Responses low、retry/warmup/補修0。次の指示まで実API比較を開始しない。以下は従来時点の記録。

2026-10-12：**30-B修正実装のローカル受入を通過、独立再審査待ち**。現在地は[30-B報告](template-ai-phase30b-2026-10-12.md)と[結果JSON](contracts/template-analysis-v8/phase30b-result.json)。実装は製品外`D:/llm-ocr-archive/template-ai/20261012-phase30b`、製品接続・通常移行なし。
R1〜R3を一run一所有thread、原子的な開始/dispatch権、待機/予約後の内容・現在性検査で修正。中止待機者はownerのguardと接続枠を解放しない。対象の共有StateSourceは同値編集でもepochを更新し、後続指示で旧応答を拒否する。画像の同期区間内証拠と元PNG decode共有を追加し、次区間は内容を再検証。
変更前/後各3反復でT2=639.69→389.46ms、全5scope中央値500ms以下。既存201＋追加51境界、保存40応答の35受理/5同一拒否と旧採点、合成24scope期待投影を確認。全claims/schema/実装内容/共通compiler/manualと元cropを維持。401実装を固定。oracle・mockであり実モデル/通信の成功ではない。
今回API0、376/496・残120。既存OCR contractを維持。次は30-C=Astra/XHigh・API0の独立再審査。30-Dの有料比較は再審査受入と次の指示まで開始しない。以下は従来時点の記録。

2026-10-12：Phase30の開始準備として**30-A修正設計を完了**。現在地は[30-A設計](template-ai-phase30-readiness-design-2026-10-12.md)と[計画JSON](contracts/template-analysis-v8/phase30-readiness-plan.json)。Phase29の差戻しと実API比較の停止は維持。修正実装は未開始。
R1〜R3は一run一実行所有者、原子的な開始権・一回dispatch権、初期化/予約後の再検査で修正する設計。T2は同期区間内の不変bytes証拠と元画像の一回decodeにより重複計算を減らす。現在性・全実装内容・schema・全claims・共通compiler/manualの検査と500ms条件を維持する。
工程30-A=Astra/XHighの設計、30-B=GPT-6.1 Sol/Highの製品外実装、30-C=Astra/XHighの独立再審査、30-D=当初のSol/High・GPT-6 Luna最大42送信の比較、31=採否審査。各区切りで次の指示を待つ。現在のチャットモデルは未確認。
今回API0、376/496・残120。Phase29の封印476成果物を開始時照合し、未修正Phase28 T2の一回profileで完全manual投影を確認。profile時間は性能受入値ではない。製品ソース・通常データ・旧証拠を保持し、既存OCR contractを維持。以下は従来時点の記録。

2026-10-12：Phase29独立審査は**差戻し**。現在地は[Phase29審査](template-ai-phase29-review-2026-10-12.md)と[審査JSON](contracts/template-analysis-v8/phase29-review.json)。Phase30の実API比較へ進めない。
新規3件をネットワーク禁止で再現：R1/P1 同一runの同時実行で2予約・2送信、R2/P2 client初期化中のキャンセル後に未送信要求が送信される、R3/P2 台帳予約中のepoch変更後も古い要求を送信する。R3は応答後の現在性検査でdraft返却を拒否。通常DBの誤適用を再現したものではない。T2は保存中央値625.34msを確認、審査再測定634.55msで500ms未達を継続。
Phase28の634成果物・実装399ファイルを照合。コピー上で既存201境界、保存40応答の旧採点、合成24scopeの期待DTO投影を再現。修正実装・製品接続なし、既存OCR contractを維持。API0、376/496・残120。次は3件＋T2の修正設計・実装・独立再審査。次の指示まで開始しない。以下は従来時点の記録。

2026-10-12：Phase28の製品外修正実装とオフライン検証を実施。**受入不合格：T2ローカル中央値625.34msが500ms上限を超過。Phase30の実API比較へ進めない。** 現在地は[Phase28報告](template-ai-phase28-2026-10-12.md)と[結果JSON](contracts/template-analysis-v8/phase28-result.json)。
意味提示/構造別branch schema/所有alias展開/元crop保持の転送pack/最大一送信の共有HTTP・期限・現在性検査を実装。旧runtime400ファイルと候補/全claims/共通compile/manualを保持。既存83＋48件、新v8 70件、旧保存40応答の採点再現、既知5scopeのoracle完全manual投影を確認。校正12＋評価12の合成familyと期待値を固定。実モデル品質・endpoint受理・実通信時間は未確認。
API0、376/496・残120。製品接続・通常データ移行なし。既存OCR contractを維持。次はPhase29=Astra / XHigh・API0の独立審査。次工程の指示まで開始しない。以下は従来時点の記録。

2026-10-12：AIテンプレート作成Phase27の修正設計を完了。現在地は[Phase27設計](template-ai-phase27-design-2026-10-12.md)と[v8契約](contracts/template-analysis-v8/README.md)。製品外の設計であり、runtime実装・実API・製品接続は未実施。
独立した有限判断はDecisions一回、構造・親・列用途・名称が連動する判断は条件付きResponses一回へ固定。意味説明と内部所有IDを分離し、構造ごとのfactor/name所属を生成schemaへ反映。既存候補・全claims・共通compiler/manual・現在性・同値手動編集の保護を維持。必須名称のplaceholder補完、ID推測修復、confidence閾値の引下げは行わない。
詳細cropの元PNGを保持し、全体画像だけJPEG quality85へ変換して小さい場合に採用する転送packを設計。コピー上のT2要求は画像変更だけで4.45MB→1.23MB（72.43%減）。これは通信速度・意味精度の改善証拠ではない。5scopeのbranch schema例と別構造choice拒否をオフライン確認。実endpoint受理とruntime検証は未了。
次はPhase28=GPT-6.1 Sol / High・API0の製品外実装、29=Astra / XHigh・API0の審査、30=Sol / High・Luna最大42送信の固定比較、31=Astra / XHighの採否。各工程はモデル切替後の指示まで開始しない。既存5秒/品質条件とPhase25不合格は保持。
今回API0、376/496・残120。製品コード・両DB各37表/台帳376行・schema・設定・旧checkout・業務863ファイル/781 artifact・旧証拠を保持。既存OCR contractを維持、通常未移行/無効を継続。以下は従来時点の記録。

2026-10-12：AIテンプレート作成Phase26の独立審査を完了。Phase25の測定記録と不合格判定を受入、製品採用は不合格のまま。
[Phase26審査](template-ai-phase26-review-2026-10-12.md)と[審査JSON](contracts/template-analysis-v7/phase26-review.json)を現在地とする。Phase25の116成果物を開始時照合し、保存10応答をネットワーク禁止で再生。9受理/1同一拒否、別採点で品質成立4/10・5秒以内0/10・重点誤結合4、完全manual投影・物理/scope・既存全ID no-opを再確認。H1は必須名称nullだけでなくrecords誤選択があり、名称だけ補った診断でも誤結合3。H2も条件付きIDと名称を補ってなお誤結合4。補修での救済を採用しない。T3の保留10判断は生第一候補9正解/1誤りで、閾値校正は未了。
T2中央値6139.32msのうちHTTP5031.79ms、初期化223.83ms。要求4.45MBの99.295%は画像base64で、転送とprovider処理の時間は未分離。ローカル小修正だけで5秒達成としない。次はPhase27=Astra / XHigh・API0の修正設計を提案。モデル向け意味判断・条件付き回答・名称方針・入力packを設計し、共通compiler/manualと所有検査を保持する。設計/実装/追加比較/製品接続は次工程の指示まで未開始。
今回API0、376/496・残120。製品コード・両DB全37表/台帳376行・schema・設定・旧checkout・通常863ファイル/781 artifact・旧証拠を保持。既存OCR contractを維持、通常未移行/無効を継続。以下は従来時点の記録。

2026-10-12：AIテンプレート作成Phase25の固定実API比較を完了。品質・5秒条件は不合格、製品へ導入しない。
[Phase25報告](template-ai-phase25-2026-10-12.md)と[結果JSON](contracts/template-analysis-v7/phase25-result.json)を現在地とする。審査済みPhase24修正revision2を凍結し、GPT-6 Lunaで固定5範囲×2反復、T3=Decisions・他4範囲=Responses lowの10送信を実施。warmup/retry/repair/追加診断API0。全重点正解・完全manual意味投影は各4/10、品質付き5秒以内0/10、品質成立中央値6139.32ms。重点分類89/146・結合54/74・行6/14・記号3/16、重点誤結合4。T3は部分保留、H1は必須名称nullで保留、H2は誤選択1件と条件外choice拒否1件。HTTP200/構造検査の通過を意味の正解と扱わない。
原応答再生9受理/1同一拒否、別採点9/9、物理/共通manual/既存全ID no-op、実行hashと台帳を確認。製品ソース・業務データ・両schema・設定・旧checkout・旧証拠を保持し、通常DBの変更はdeployの今回ledger10行だけ。既存366行を保持、既存OCR contractを維持、通常未移行/無効を継続。今回API10、376/496・残120。次はPhase26=Astra / XHigh・API0の独立審査。次工程の指示まで審査・再設計・追加比較・製品接続を開始しない。以下は従来時点の記録。

## 2026-10-12：T2修正revision2の独立審査（製品外・受入）

[審査報告](template-ai-t2-repair-review-2026-10-12.md)と[審査結果](contracts/template-analysis-v7/t2-repair-review.json)を現在地とする。修正実装の500成果物hashと保存30計測、393ファイル同内容の隔離再生を照合。83＋48境界、旧30応答26受理/4拒否・全採点/時間/usage・26全ID no-op、oracle5/5の完全manual意味/physicalを再現した。待機中の同値手動facet変更とmodule reloadでも応答を拒否。実装proof失敗後の永久失効と予約前/受信後/段間の検査を確認した。
T2中央値472.96ms、全5範囲の500ms条件を通過し、修正審査は受入。各3標本であり実モデル・実DB保存・UI・音声の受入ではない。Phase25最大20送信は次工程の指示まで未開始/未予約。通常未移行/無効、製品採用未認定を継続する。
今回API0、366/496・残130。製品OCR/provider/prompt/model/schema、mapping/association/crop、受付snapshot、補正revision、確定/出力・両DB/schema・設定・通常データ・全旧証拠は不変。既存OCR contractを維持する。以下は過去時点の記録。

## 2026-10-12：T2修正のローカル受入（製品外・修正審査待ち）

[修正結果](template-ai-t2-repair-2026-10-12.md)を現在地とする。factory-owned検証済み入力を共通resolverへ渡し、geometry生成を新規1回/復元時1回へ集約した。復元ではExpected・画像・policy・digest・候補完全比較を要し、公開hashだけの受入やtrusted flag/monkeypatchはない。全claims・保留依存・範囲外不変・現在の内容検査・同値手動facet・共通compiler/manualを維持する。
実装manifest393ファイルを同期区間の開始/終了で内容/集合照合する。連続Responses1回は2区間、Decisions二段は3区間。first/secondを個別に返す場合も区間を閉じ、追加の再検査を行う。予約前・受信後・段間の変更で結果/追加送信を拒否する。例外・取消も閉じ、未実行区間を開始しない。
固定比較T2中央値751.75→472.96ms、全5範囲500ms以下、完全意味投影5/5、旧83＋新48境界、変更後runtimeで旧30応答26受理/4拒否・全採点/時間/usage・全ID no-opを確認。oracle/ローカルのみ。元Phase24の716.81ms不合格を上書きせず、実API/DB保存/UI/音声の受入へ拡張しない。次はAstra / XHigh・API0の修正審査。Phase25有料比較は未開始。
今回API0、366/496・残130。製品OCR/provider/prompt/model/schema、mapping/association/crop、受付snapshot、補正revision、確定/出力、両通常DB/schema・設定・通常データ・旧証拠を保持し、既存OCR contractを維持する。以下は過去時点の記録。

## 2026-10-11：T2ローカル処理の修正設計（追加審査・未実装）

[T2修正設計](template-ai-t2-revision-design-2026-10-11.md)を次の外部試作の基準とする。Phase24はT2=716.81msで不合格のまま。最終ソースの変更なし診断も706.24msで未達。geometry12回・依存391ファイル照合6回を確認した。
設計ではfactory所有の不変VerifiedRunInputに物理/候補/scope/packを固定する。新規候補は1回生成、復元候補はExpected・digest・geometry完全比較後だけ再利用する。公開hash・trusted flag・monkeypatchで検証を省略しない。全claims/依存保留/範囲外不変/同値手動facet/共通compiler/manual投影を維持する。
実装内容照合を同期区間の開始/終了へ集約するが、現在の所有/revision/scope/画像/pack/catalog/facet/epoch検査は維持する。実装manifestの範囲を縮めず、外部待機前後で区間を分け、終了照合前に結果を公開しない。製品DB transactionの競合検査は未接続。schema初期化費用の計時外移動で500msを達成したと扱わない。
次の24修正実装・審査はAPI0。全5scope中央値500ms以下・完全意味投影・83境界と新失効条件・変更後runtimeで旧30応答保持を要する。Phase25有料比較はまだ開始不可。モデル/prompt/画像/候補上限・OCR対応契約は変更しない。今回API0、366/496・残130。製品・両DB/schema・通常データ・Phase11〜24証拠を保持する。以下は過去時点の記録。

## 2026-10-11：条件付きfactor planの製品外試作（フェーズ24・時間未達）

[Phase24結果](template-ai-phase24-2026-10-11.md)を現在地とする。製品外にv7生成器・不変evidence factory・選択後compiler adapter・mock wire runnerを実装。旧完成案の分解や全直積列挙ではなく、geometry/evidenceから構造と条件付きfactorを直接発行する。生成器のgold/帳票ID/旧応答への依存はない。
完全manual意味投影5/5、physical identity、既存graphの全ID/path込みno-op、83境界と旧30応答26受理/4拒否・旧採点/全計測を確認。必要な選択/名称は評価側のoracle供給で、新モデル精度を示さない。旧Phase22の4/5不合格を保持する。
所有/revision/scope/画像内容/catalog/同値手動facet/epoch/依存実装hashを照合し、選択後にpackを再cropしない。旧pack guardやcompilerは変更しない。保留claimsと宣言元を競合closureへ残し、侵入案と依存部分を保留する。未知をignoredで加点せず、未解決名称の依存表を完成扱いしない。
全範囲のローカル中央値500ms以下が事前条件。最終T2は716.81msで未達のため総合不合格、Phase25有料比較は開始しない。Decisions/Responses経路・二段receipt・最大2送信・予約・中断・timeoutはmockのみで、製品のHTTP/DB transaction/画面/音声は未接続。
今回API0、366/496・残130。製品OCR/provider/prompt/model/schema、mapping/association/crop、受付snapshot、補正revision、確定/出力を変更せず既存OCR contractを維持。両DB全37表/schema・設定・旧checkout・通常データ・過去証拠を保持する。次はAstra / XHigh・API0のT2費用審査を提案し、新指示まで追加設計/実API/製品接続を開始しない。

## 2026-10-11：条件付き判断・自由名称の設計審査（フェーズ23・設計のみ）

[Phase23審査](template-ai-phase23-review-2026-10-11.md)と[v7設計契約](contracts/template-analysis-v7/README.md)を次試作の基準とする。完成案の全直積を構造と条件付きfactorへ分離し、選択された案だけを共通resolver/compiler/manualへ渡す。新runtime・API接続・製品保存は未実装。
先行回答に依存する質問は別request、有限判断は最大2段。自由名称が必要な候補を含む場合は、送信前にgpt-6-lunaのResponses一回へ振り分ける。Decisionsも同モデル。未発行ID・古い段間binding・保護facet・全claims競合を拒否し、確率から別案を採用しない。
名称はserver所有slotの表示意味であり、physical identity・mapping/path・scopeの再割当てを許可しない。未解決名は依存単位を保留し、unknownをignoredへ変換しない。実保存は既存の「案を反映」→「確認して保存」、manual-authoring/MutationCore/TemplateAuthoringService・revision/receiptへ接続する設計を維持。
Phase22の4/5不合格は不変。52案の7択＋最大6択への分解と名称供給診断はoracle/表現確認に限る。新モデル性能・v7生成器・製品採用の成功ではない。packの重複再構築を除く設計でも、内容hashと現在の所有/revision/同値手動意図の検査を省略しない。
次は24=製品外実装/受入API0、25=合格後の最大20送信測定、26=審査という提案。各区切りで指示を待つ。今回API0、366/496・残130。製品OCR/補正/出力・両通常DB/schema・設定・既存証拠を保持し、通常未移行/無効を継続する。

## 2026-10-11：有限候補選択の成立性試作（フェーズ22・不合格）

[フェーズ22記録](template-ai-phase22-2026-10-11.md)と[v6試作契約](contracts/template-analysis-v6/README.md)を現在地とする。製品外でgold非参照の候補生成と有限選択境界を実装。候補発行前と選択合成後に共通guard/compilerを通し、全claims・親/role/値所有・mark依存・scope/手動facetを保護する。unknownはcoverageに保持し、保存用ignored初期値へ加点しない。
正解参照によるoracle選択ではT1/T2/T3/H1の意味投影が一致したが、H2は共通compile前の構造仮説52＋保留2が固定上限32を超過。15refsを全体保留し、候補の打切りやposition/列用途の自動採用は行わない。自由な表名のliteral導出も未解決。4/5を採用成功へ読み替えず、有料比較・製品接続を停止する。
39境界条件と旧30応答再生26受理/4拒否・採点/全計測/usage/manual投影・既存全ID no-opを確認した。閾値は未校正。要求仕様はoffline projectionであり実endpoint/モデル/画面/保存/音声は未受入。次の提案は23=Astra / XHigh、API0の依存粒度・候補数・名称方針の審査。各フェーズで指示を待つ。
製品OCR/provider/prompt/model/schema、mapping/association/crop/physical identity、受付snapshot、補正revision、確定/出力は不変。両DB全37表/schema・設定・旧checkout・通常ファイル/artifact・Phase11〜21証拠を保持し、既存OCR contractを維持。API0回、366/496、残130。以下は当時の記録。

## 2026-10-11：focused v5採否と候補選択の設計審査（フェーズ21）

[フェーズ21審査](template-ai-phase21-review-2026-10-11.md)を現在地とする。Phase20の30保存応答・採点・計測を再検証し、全方式の製品不採用を維持。26案の共通/manual意味投影・既存全ID no-opを再現し、4件も同じmark定義理由で拒否された。unknownの保存用ignored初期値へ加点しない最終採点を確認した。
T2の4応答コピーでprinted行識別と先頭列用途だけを変えた診断は3件で重点表条件を満たし、残1件はunknown列で保留。診断は元のAI応答や品質・速度の上書きではない。現snapshotで成立しないprinted案を事前除外する余地を示すが、positionへの自動補正・採用は許可しない。
次の設計仮説は、整合した意味案をサーバーが有限列挙しGPT-6 Luna / Decisionsが選ぶ方式。親・列・行・markの依存を同じ候補へ束ね、未発行選択・競合・stale snapshot・手動facet変更を拒否。全claims、unknown、全ページcoverage、共通compiler/manual保存境界を保持する。候補の事前成立は意味正解や保存権限を保証しない。自由な意味は別Responses/手動経路とし、常時二重APIや幾何規則だけの採用はしない。
次は22=Sol / High、API0、製品外の契約具体化・成立性試作。保留込み32候補/依存単位は設計上限でありAPI仕様ではない。gold非参照の候補coverage未達・候補爆発なら送信せず停止。新方式の製品接続・OCR/provider/schema変更・自動移行は未実施。両DB全37表/schema、製品コード、通常ファイル/artifact、旧checkout、Phase11〜20証拠は不変。既存OCR contractを維持。API0回、366/496、残130。各フェーズで停止して指示を待つ。以下は当時の記録。

## 2026-10-11：focused v5の固定30試行（フェーズ20）

[フェーズ20記録](template-ai-phase20-2026-10-11.md)を現在地とする。製品外Phase19 runtime/生成契約を保持し、固定30 requestをGPT-6 Lunaへ送信。HTTP200/completed/strict shape30/30、guard受理26/30。F-low/P-low/P-noneは分類46/67/30（各146）、結合11/15/4（各74）、行全方式0/14、記号4/8/0（各16）、重点誤結合8/12/4、品質付き5秒以内2/2/0（各10）。全方式不合格。形状受理・早いdelta・短い出力を意味正解としない。
初期runnerの分類採点がunknownを保存用ignored初期値と混同して3試行を過大評価したため、canonical proposalから別実装で分類だけ再採点した。初期値50/71/34を46/67/30へ訂正し、元応答・gold・分母・全計測時刻・品質付き時刻/採否を保持。実bindingのみの採点、scope外cell全体・物理identity、manual意味投影、既存graph全ID/path no-opを保存26案で確認した。schema時間はresolver合算で、UI/保存受入とは扱わない。
通常DBの変更は送信前予約されたAPI台帳30行のみ。既存336行・他36表、両schema、旧checkout全体、設定/通常ファイル/artifact/Phase11〜19証拠を保持。製品OCR/provider/prompt、mapping/crop/physical identity、受付snapshot、補正revision、確定/出力実装を変更せず、既存OCR contractを維持。製品未移行/無効・採用不合格を継続。API30回、366/496、残130。次は21=Astra / XHigh、原則API0の審査。追加比較・製品接続・自動再設計を開始しない。以下は当時の記録。

## 2026-10-11：focused v5と入力パックの製品外実装（フェーズ19）

[フェーズ19記録](template-ai-phase19-2026-10-11.md)を現在地とする。新しい外部runtimeへv5変換とevidence packを追加した。既存397ファイル・Phase18 schema/prompt/scope/gold/protocolは不変。共通normalizer/compiler/manual経路、全体物理snapshot、global refs、元座標、scope/手動facet/read-set、保留formのparent claimsを維持する。入力packは編集権限を増やさない。
新入口49条件と、既存12条件のv4/v5両入口を通過。旧24応答のrequest/source/scope hash・採点・受理/保留・名前/共有/mark/親/表/行・manual投影・既存graph全ID/pathを保持。scope外cell全体とcrop画素の一致、既存read-only label/parent参照、同値手動意図の保護を確認した。手作成の正解案であり新AI精度/速度ではない。
独立台帳と模擬transportで予約commitが送信より先、通信断でも予約保持、上限/別host拒否を確認。通常台帳・両DB/schema・設定・863通常ファイル・781 artifact・旧証拠は保持。製品OCR/provider・mapping/crop/physical identity・受付snapshot・補正revision・確定/出力実装は不変で、既存OCR contractを維持する。
API0回、336/496、残160。30回の次回request/source/scope/pack digestを固定したが未送信。製品採用不合格・通常未移行/無効を継続。次は20=Sol / Highで固定30回測定、21=Astra / XHighで採否。実schema受理、汎用planner、scope公開HTTP/DB/receipt、保存/取消、UI/マイク、製品回帰は未受入。各区切りで指示を待つ。以下は当時の記録。

## 2026-10-11：局所案先行公開とfocused v5（フェーズ18・設計のみ）

[フェーズ18設計](template-ai-phase18-design-2026-10-11.md)と[focused v5契約](contracts/template-analysis-v5/README.md)を現在地とする。全体snapshot/物理identityとscope権限、provider向けevidence packを分離する。任意表選択は優先順位であり、全ページ依頼のcoverageを勝手に削らない。案の公開単位と全体完了を分ける将来HTTP v2を設計したが、製品v1のguardやDBを変更していない。
label付き/direct、record列の名前・title導出をschemaで分離。形状だけで意味正解を保証せず、Phase17の保留form親宣言を含む全claims、scope/手動facet/read-set保護、共通compiler/manual投影を必須にする。案は下書きへ明示反映し、既存保存経路で確認して保存する。部分JSONの適用、範囲外親の補充、先勝ち採用はしない。
次回比較はGPT-6 Luna low/noneの固定局所5scope×2反復×3方式、最大30回の設計。元goldを改変せず派生重点分類146/結合74/行14/記号16を別分母で採点し、全ページの旧基準を置き換えない。scope-readyの品質と5秒条件を満たしても全ページ/未知帳票/実保存の採用とはしない。
schema15条件、元入力15hash、固定scope/計測条件を確認。製品ソース・通常DB/schema・設定・863通常ファイル・781 artifact・旧証拠は保持し、既存OCR contractを維持。API0回、336/496、残160。製品採用不合格・通常未移行/無効を継続。
次は19=Sol / High、製品外adapter/packの受入、API0。20=固定30回、21=Astra / XHigh審査。製品接続・自動planner・HTTP所有/保存/取消・UI/マイク・全件回帰は未受入。各区切りで指示を待つ。以下は当時の記録。

## 2026-10-11：保留formの親競合検査（フェーズ17・製品外修復）

[フェーズ17記録](template-ai-phase17-2026-10-11.md)を現在地とする。外部コピーのcompact.pyでfield/formのrole・値所有・parent収集を共有し、不正formを保留しても収集可能な親宣言を保持する。異なる親の競合は関係単位を保留し、同じ親の独立fieldは維持する。保留formのparent facet保護も共通normalizerへ渡す。先勝ち・宣言削除・親の推測補充は追加しない。
固定12条件は修復前6失敗、修復後12/12通過。保存24応答の採点・受理/保留・意味投影とmanual変換・物理参照・既存graph完全no-opは保持。既知の保護欠落の修復であり、新API品質/速度・全不正入力・実保存の証明ではない。
schema/prompt/model/候補/snapshot/scope/入力/gold、Phase15/16証拠は保持。製品OCR/provider・mapping/crop/physical identity・受付snapshot・補正revision・確定/出力実装を変更せず既存OCR contractを維持。両通常DB全37表/schema・設定・863通常ファイル・781 artifact SHAを保持。API0回、336/496、残160。通常未移行・無効、製品採用不合格を継続する。
次の提案は18=Astra / XHighで性能方式と生成契約を設計審査、API0。実装・追加比較・製品接続へ自動進行しない。未知帳票、実HTTP所有/保存/取消、画面/マイク、製品回帰は未受入。各フェーズ区切りで停止する。以下は当時の記録。

## 2026-10-11：AIテンプレート採用審査（フェーズ16・不採用を維持）

[フェーズ16審査](template-ai-phase16-review-2026-10-11.md)を現在地とする。E4全ページ・任意表選択は速度/品質条件未達、D13も重点誤結合のため採用しない。保存24応答の固定request/source/scope hash、別実装による実binding採点、共通/manual意味投影・既存graph完全no-opを再確認した。重点採点の一致は全欄・未知帳票の正しさを保証しない。
新しい製品外診断で、不正form保留時にparent claimを落とす経路を確認。H1のラベルc9に対し保留formがc8、別fieldがc2を親と宣言すると、後者を受理する。通常の競合formなら両側保留される。Phase15 compact.pyのrejected_formでparents収集が欠ける。未修正であり、過去の「不正単位の全parent claim保持」をこの経路について訂正する。旧24試行の結果は変更せず、通常製品への影響は発生させていない。
次の提案は17=GPT-6.1 Sol / Highでこのguardを製品外修復し、API0で保存24案と関係する境界を確認すること。新schema/promptや比較の自動開始、保留宣言の削除・先勝ち・推測補完による救済は行わない。修復しても採用条件を満たした扱いにしない。
製品OCR/provider/prompt・mapping/crop/physical identity・受付snapshot・補正revision・確定/出力コードを変更しておらず既存OCR contractを維持。両通常DB全37表/schema・設定・863通常ファイル・781 artifact SHA・Phase11〜15証拠を保持。API0回、336/496、残160。通常未移行・無効。新規帳票、実保存/取消、画面/マイク、全体回帰は未受入。フェーズ区切りで停止する。以下は当時の記録。

## 2026-10-11：AIテンプレートcompact v4の固定比較（フェーズ15・採用不合格）

[フェーズ15記録](template-ai-phase15-2026-10-11.md)を現在地とする。製品外v4のform→独立field/records→繰り返し表、名前参照/mark共有、scope付き不変snapshotを実装。保留単位の所有claimも維持し、重複両側保留・同値手動facet・範囲外write拒否・実在する親だけのread-only参照を検証した。製品保存経路の代替ではない。
Phase13保存再生20件・境界63、新v4/scope83、追加28を通過。固定24送信（全ページ20＋選択4）のE4は中央値8.178秒対D13 10.905秒でも分類126/176・結合62/78・行10/14・記号8/16で採用未達。案なし1件のreadyはnull、比較ペナルティ60秒。重点誤結合0/欠落16、5秒以内0/10。選択E4も9.167/5.493秒で未達。速さだけで採用しない。
24案の共通/manual意味投影と既存graph完全no-op保持。新規割当UUID/pathの差と、既存ID/pathの保持を区別する。取得textに根拠のないmark選択肢は保留する保守的な試作制約で、画像内容の自動証明を実装済みとは扱わない。
通常製品OCR/provider/prompt・mapping/crop/physical identity・受付snapshot・補正revision・確定/出力コードは未変更で、既存OCR contractを維持した。両通常業務DB/schema・863通常ファイル・781 artifact SHA・Phase11〜14証拠・設定を保持。通常未移行・無効。API24回、336/496、残160。未知帳票、実HTTP所有/保存/取消、画面/マイク、製品回帰は未受入。
次は16=Astra / XHigh、原則API0で採用範囲審査。追加再設計/比較・製品統合へ自動進行しない。以下は当時の記録。

## 2026-10-11：AIテンプレートの高速出力契約（フェーズ14・設計のみ）

[フェーズ14設計](template-ai-phase14-design-2026-10-11.md)と[compact v4契約](contracts/template-analysis-v4/README.md)を現在地とする。provider wire形式を圧縮し、通常formを独立fieldへ、recordsを繰り返し表へ展開する設計。画像/座標・物理ID・保存先をAIに生成させない。
scope/write/contextOnlyを所有snapshotに固定し、古い候補や現在の選択による再解釈を拒否する。全値所有・役割・親の競合を展開後に検査し、不正単位のclaimを捨てて競合を隠さない。contextは所有表に依存してignoredへ分類し、文字/物理参照を案metadataへ保持する。
名前の参照解決は明示labelsの取得時textだけ。欠落や長さ超過を推測補充しない。親と直接ラベル、mark定義と各値所有を分離し、既存facet・同値手動意図・physicalDigest・revision・receipt・undo・明示保存を維持する。v3失敗からv4/旧方式へ自動fallbackしない。
保存10応答の意味宣言を保持した再符号化でJSON bytes40.28%減。これは速度/モデル精度の新実測ではない。v4 runtime・実API受理・保存は未検証、製品採用は未確定。製品OCR・mapping/crop・受付snapshot・補正/確定/出力コードは未変更で、既存OCR contractを維持した。
次は15=GPT-6.1 Sol / High、GPT-6 Luna / lowの同時対照、全ページ20＋任意選択4、最大24回。事前境界検証・hash固定必須。16=Astra / XHighで採用判定。今回API0回、312/496、残184。通常DB/schema・データ・設定・旧証拠を保持し、通常未移行・無効。以下は当時の記録。

## 2026-10-11：AIテンプレート候補方式の修正試作（フェーズ13・製品外）

[フェーズ13記録](template-ai-phase13-2026-10-11.md)を現在地とする。単体markの一意結合をmanual変換で保持し、不一致・複数markSetを拒否する。候補と入力をresource/version/page/revision/画像/generation/policy/digestへ束縛し、旧候補別渡し入口を廃止した。取得後のdict変更・古い候補・compile時の参照改変を拒否する。
context bodyはignoredとし、物理ID/表示文字列と案metadataを保持。所有表が保留ならcontext分類も保留する。手動meaning/parent/role意図と実在する見出しのread-only参照を検証した。製品のsnapshot/physicalDigest/facet/receipt/undo・明示保存を代替しない。
保存20応答のmanual意味投影保持と境界63項目（既存graph no-op20件を含む）が通過。Dの分類150/176は保存応答の再生値で、旧実API主結果142/176と区別する。H1/T1の競合・親判定と速度は未解決、採用不合格を維持。
所有bindingはfixture値。実DB保存・HTTP・全facet/lifecycle・画面・マイク・未知帳票・重い回帰は未受入。通常製品コード、OCR/mapping/crop/physical identity/受付snapshot/補正revision/確定・出力契約は未変更で、既存OCR contractを維持した。
両通常DB/schema・台帳・データ・旧証拠・設定を保持。API0回、312/496、残184。通常未移行・無効。次は14=Astra / XHigh、API0回で高速出力契約とprompt整合を確定する。製品接続は採用判定後。以下は当時の記録。

## 2026-10-11：AIテンプレート候補方式の再審査（フェーズ12・不採用）

[フェーズ12審査](template-ai-phase12-review-2026-10-11.md)を現在地とする。20保存応答の主採点は一致。Dの現実装は速度/意味/接続条件未達のため採用せず、製品接続へ進めない。
製品外manual変換で9試行57relationが欠落した。input限定の関係再構成が単体markを落とす。別コピーの一条件修正で20案の意味投影が復元したが、製品・既存状態・保存lifecycleの修正完了ではない。
試作resolverは候補snapshotDigestを検証しない。古い候補とセル追加後のpayloadでH2の値4セルが隣のラベルへ誤対応することを再現。主比較は同一payloadから候補を生成したため影響なし。候補/c参照をresource/version/page/revision/画像/geometryと束縛し、既存physicalDigest・facet・receipt等のguardを維持してから接続する。
context説明bodyのlabel化をignoredへ変えた感度分析は分類150/176。固定主結果142/176を改変せず、モデル精度・速度の新しい測定と区別する。値所有競合・直接親不一致の保留を弱めない。
製品OCR・mapping/crop・identity・受付snapshot・revision/出力契約は未変更、既存OCR contractを維持した。API0回、312/496、残184。両通常DB/schema・台帳・業務データ・設定を保持し、通常未移行・無効。
次はフェーズ13の製品外修正試作（GPT-6.1 Sol / High、API0）。製品接続は採用後へ延期。以下は当時の記録。

## 2026-10-11：AIテンプレート解析の候補方式比較（フェーズ11・採用不合格）

[フェーズ11記録](template-ai-phase11-2026-10-11.md)を現在地とする。既存物理セルだけから行列候補を生成し、Lunaの列用途・行範囲・局所表題を元のIDへ展開する製品外試作を比較した。新規矩形生成や製品保存を実装した工程ではない。
未知refは要求拒否、値所有競合は関係単位すべて保留。不正表のclaimを除いて競合を隠さず、行や親を推測補充しない。局所表題と上位階層を分離し、未解決を正しい結合として数えない。
実20送信でDは結合63/78・重点行14/14・記号16/16、T2/H2重点条件通過。しかし分類142/176・中央値11.356秒で採用不合格。context説明列のlabel化とgoldのignoredの差も未解決として保持する。B′も不合格。
短い境界27＋33と空状態manual compilerを検証。実保存・全facet/lifecycle・画面・未知帳票・全体回帰を代替しない。製品OCR・mapping/crop・physical identity・受付snapshot・revision/出力契約は未変更で、既存OCR contractを維持した。
通常業務DB/schema・両ソース・設定を保持、台帳だけ20予約追加。312/496、残184。通常未移行・無効を継続する。
次はフェーズ12、Astra / XHighで再審査、原則API0回。採用前の製品接続や追加再設計は開始しない。以下は当時の記録。

## 2026-10-11：AIテンプレート解析の採用審査・再設計（フェーズ10・製品未実装）

[フェーズ10記録](template-ai-phase10-review-2026-10-11.md)を現在地とする。A/B/Cの採用基準未達を維持し、製品実装へ直行しない。検証修正後の保存応答再生でもBは分類119/176・結合39/78・行3/14、Cは88/14/4で不合格。
次のD試作では既存セルの行列候補をsnapshotへ束縛し、Lunaの指定を元の物理IDへ展開する。候補は正解ではなく、座標/ID再生成・近接による値再割当・行の推測補充を行わない。新規矩形生成はこの比較の対象外。
局所表題を含む表本体と上位見出し接続を別の提案として生成する。必須表題の不正をnullで救済せず、未解決階層を完全な結合として採点しない。既存parentの暗黙削除はしない。
checklistはmultiple必須をschemaとvalidatorで一致させる。辞書だけの分類はセル保留、未知ref/所有不一致は要求拒否、重複値は関係単位すべて保留。保護済み見出しはread-only参照で依存を満たす。
共通APIの一意mark結合・行別cellLabelCellsとmanual adapterを維持し、既存のfacet・同値手動意図・revision・receipt・undo・明示保存を迂回しない。geometry probeや空状態compilerの成功を製品の保存・OCR対応保証にしない。
製品ソース・両通常DB・schema・設定は未変更、API0回、292/496、残204。既存OCR contractを維持し、通常未移行・無効を継続する。
次はフェーズ11の製品外比較（GPT-6.1 Sol / High、API初回24以内）、12の採用再審査（Astra / XHigh）。旧計画の11製品実装は13へ移し、採用を前提に開始しない。以下の履歴は当時の到達点として保持する。

## 2026-10-11：AIテンプレート解析の比較試作（フェーズ9・採用不合格）

[フェーズ9記録](template-ai-phase9-2026-10-11.md)を現在地とする。製品外コピーの共通compilerと実30送信を比較した。製品の保存契約や通常機能を変更していない。
Cは記号選択肢16/16でも分類77/176・結合11/78・行4/14・意味案6/10で採用基準未達。Bも不合格。全試行中央値A/B/C=11.219/18.449/15.555秒。
checklistは現行multiple必須。フェーズ8schemaのsingle許可は不足として記録し、製品の制約を緩めない。Bの辞書のみ分類の拒否範囲とmanual compilerのmark/行別ラベル接続も次審査で扱う。
固定した主比較と、別コピーの局所guard/manual変換replayを分離する。保存成功、人の修正時間、実マイク、未知帳票の精度を未検証のまま成功扱いしない。
API台帳だけ30予約追加、292/496、残204。通常業務DB/schema・製品ソースを保持し、通常未移行・無効。既存OCR contractを維持する。
次はフェーズ10、Astra / XHighの採用審査。現時点の採用方式はなく、フェーズ11の製品実装へ直行しない。

## 2026-10-11：AIテンプレート解析の修正設計（フェーズ8・未実装）

[フェーズ8設計](template-ai-phase8-design-2026-10-11.md)を現在地とする。意味構造から分類を導くv2、未出力unknown、依存関係に基づく単位保留を設計した。
共通meaning.bindの一意なmark対応と、RecordRow.cellLabelCellsによる行・列見出し保持を計画する。物理ID/geometry/targetPath/関連ID、手動意図、revision、既存保存経路を保持する。
未知/ページ外参照や所有snapshot不一致はページ全拒否、壊れた表は表全体保留。分類だけ・全除外を意味付け完了や保存承認へ読み替えない。
設計JSONのオフライン31確認のみ。製品provider/compiler/UIは未変更で、実API適合・精度/速度・部分成功の受入は未実施。
今回API0回、262/496、残234。通常DB未移行・機能無効。次は製品外の比較試作（フェーズ9、GPT-6.1 Sol / High、初回36回以内）、その後Astra / XHighで採用審査する。
フェーズ7の全ignoredを2件、診断の分類全数検査での初回拒否を4/6へ訂正し、診断1件の表3件出力も記録した。主比較集計と不合格は維持する。
過去の全件回帰結果を今回の成功証拠へ読み替えない。通常導入と大きな契約変更の実装はこの設計工程では行わない。

## 2026-10-10：AIテンプレート解析の実測・受入不合格（フェーズ7・通常無効）

[フェーズ7記録](template-ai-phase7-2026-10-10.md)を現在地とする。固定5帳票を旧全体/新全体/種類限定で各2反復し、実30送信を比較した。
新全体案は10件中6件拒否、通過4件中2件が全セルignored（2026-10-11訂正）。中央値7.827秒でも重点分類26/176・結合1/78で、品質条件を満たさない。
初期roleを送らない限定診断も6件全拒否。実画面1件は分類だけの案を表示し、反映・保存は行っていない。手直し/保存時間・実マイクは未受入。
製品コード・OCR処理・物理identity・mapping/crop・受付snapshot・revision/出力bindingを変更していない。通常業務DB/schemaと通常863ファイル・781 artifact SHAを保持。
共有台帳のみ37予約追加、262/496、残234。通常DB未移行・通常editing/解析無効。検証を弱めたり通常導入へ進めず、次はAstra / XHighで修正設計する。
全数分類/関連/部分成功の契約変更はこの工程で実装しない。速度の改善だけで受入完了とは扱わない。

## 2026-10-10：AIテンプレート解析の監査・回帰（フェーズ6・通常無効）

[フェーズ6記録](template-ai-phase6-2026-10-10.md)を現在地とする。反映と取り消しは値の一致に加え手動操作通番を照合する。
遅着反映は現ownerとrevision/context/interaction/catalog/scopeへ再照合。サーバー統合runはresource確認後analysisIdで追跡する。
復旧記録を保存できない間は自動保存・AI反映を保留し、受領済み業務receiptを失敗扱いへ戻さない。保留下書きの別画面復元は明示確認を必要とする。
frontend143件・build、実HTTP39、境界24、保存OCR72項目/290画像、XLSX95セルと2ページ編集を検証。backend全件の既存23失敗/34収集エラーは残り、全体成功・完全なOCR安全性検証とは扱わない。
OCR処理・保存済み値・物理identity・mapping/crop・受付snapshot・出力bindingを変更していない。通常DB全表・データSHA保持。
実API0回、225/496、残271。通常未移行・無効。実AI品質/速度と実マイク受入はフェーズ7。

## 2026-10-10：AIテンプレート解析の画面接続（フェーズ5・通常無効）

[フェーズ5記録](template-ai-phase5-2026-10-10.md)を現在地とする。解析・選択反映は下書きに限定し、確認後の共通manual-authoringで保存する。
解析中は自動保存と音声編集を保留し、手動facet・同値の再指定・後続入力を保護する。既知の解析復旧はGET、応答不明の書込は元要求を使用する。
分類だけの変更でLayoutCell.sourceを変更しない。意味の由来はfacet記録へ分離し、既存物理セル・crop・mapping/path・受付snapshot・出力bindingを維持する。
対象34テスト・build・実HTTP27条件と専用コピーの実Chrome保存を確認。全体回帰・複数ページ出力・AI品質/速度受入は未完了。
実API0回、225/496、残271。通常DB未移行・通常editing/解析無効、両通常DB全表・通常データSHAを維持する。

## 2026-10-10：AIテンプレート解析バックエンド（フェーズ4・通常無効）

[フェーズ4記録](template-ai-phase4-2026-10-10.md)を現在地とする。全体Responses・種類限定Decisionsはgpt-6-luna。
解析案は既存物理セルへ束縛し、分類全数・表の一意所属・行幅・親循環を検証してから下書きへ反映する。
反映APIから業務layoutを保存せず、共通manual-authoring・revision・receipt・undoを使う。
手動facet・由来不明設定を保護し、新規検出だけ既知の未確認分類を記録する。LayoutCell.sourceを意味の由来へ転用しない。
検証済み下書きの内部上限は5000操作・参照20000・相異なる物理セル1000。公開50操作・参照1000と保存layout上限は維持する。
解析中の範囲/世代/画像変更、cancel、期限、再起動の遅着結果を採用せず、APIを自動再送しない。
OCR provider・crop・受付snapshot・既存mapping/path・出力bindingを変更しない。移行は専用コピーだけ。
実HTTP・限定保存/取り消し・静的検査は確認したが、複数ページ/出力の全体回帰とAI品質・速度の受入は後続工程。
実API3回、225/496、残271。全体AI案2件は検証拒否。通常DB未移行・通常editing/解析無効を維持する。

## 2026-10-09：AIテンプレート作成の方式・API設計（フェーズ3・未実装）

[AIテンプレート作成フェーズ3](template-ai-phase3-2026-10-09.md)を次工程の設計とする。
全体Responsesと種類限定Decisionsから意味案を生成し、表の行列と単体欄の結合を二重の正本にしない。
AI案は検証して下書きへ反映し、既存manual-authoring・revision・receiptの保存を使う。
手動facet保護、由来不明の既存設定の保護、全体下書き向け内部compile上限の分離は実装予定であり、現行仕様へ読み替えない。
OCR provider、物理セル・crop・受付snapshot、通常環境の無効状態は変更しない。今回の実API使用は0回。

## 2026-10-09：音声受入と対象省略訂正（再設計フェーズ7・通常無効）

[フェーズ7記録](voice-editing-redesign-phase7-2026-10-09.md)を現在地とする。通常切替は保留。
無音の実経過時間をブラウザ側で再確認し、サーバーの1200ms境界を下げない。
対象省略の単一値編集は、最新receiptと同scope・contextRevision・interactionEpochの唯一の所有セルへ限定する。
未完了の別の欄の指示を以前の保存先へ移さず、元指示の明示列と行回答の対応を検証する。
共有command・revision・receipt・undoを使用し、OCR provider／mapping／crop／出力bindingを変更しない。
コピー保存と保存response再生・画像SHA・XLSX対応を確認。最新修正の実マイクと通常環境の移行は未実施。

## 2026-10-09：表示の待機・競合調整（再設計フェーズ6・通常無効）

[フェーズ6記録](voice-editing-redesign-phase6-2026-10-09.md)を現在地とする。
認証済みSSEの完了eventを保持し、欠落時は元request IDを照会する。編集を自動再送しない。
context／previewは並行取得するが、receipt・所有context・revisionの検査後だけ表示する。
表示更新は順序付け、先行取得失敗の復旧待ちを後続成功で消さない。手動draftとreceiptを保持する。
backend・補正payload・revision保存・mapping・association・crop・出力対応は変更していない。
対象33件／buildと固定resolverによる専用Chrome実保存を確認。制御測定は実音声の速度保証ではない。
通常editing無効・通常DB未移行・TTS無効。実マイク・全体回帰・通常切替はフェーズ7へ残す。

## 2026-10-09：画面・手動保存接続（再設計フェーズ5・限定実画面確認済み）

[フェーズ5記録](voice-editing-redesign-phase5-2026-10-09.md)を参照する。
v2手動OCR保存は全FieldReadingとreviewItemsを既存補正transactionへ渡し、欄集合・sourceRegionId・boundingBoxを固定する。
新規テンプレートセルのsubstitutionsは既存compilerの結果をreceiptへ返す。既存ID・対応を再生成しない。
AI枠は所有anchorを現在の表示座標へ投影し、resource／page／revision／interactionEpochを照合する。
手動選択・DOM focus・warning・scopeを枠のために変えない。保存済み・表示未取得はreceiptを保持し再保存しない。
限定コピーAPI・部品テスト・固定resolverと実保存のChrome操作を確認済み。通常editing無効・DB未移行・TTS無効を維持。
元見出しの可視判定、未保存が確定した範囲拒否後の再入力、同一IDの保存後選択保持を修正した。
通常移行・実マイク・全体回帰の完了を意味しない。

## 2026-10-09：音声取得・保留回答（再設計フェーズ4・通常無効）

[フェーズ4記録](voice-editing-redesign-phase4-2026-10-09.md)の通り、segmentは保存に使わず、全manifest／item／digestと所有bindingを検証する。
質問では全文・既知の対象と値・300秒質問期限／900秒指示期限を保持し、無意味な承認を値にしない。
未保存planは発声でholdし、訂正・scope変更・取消でfenceする。明示scope回答だけ同じ指示を再束縛する。
単一対象の差替えは直近receiptを検証し、旧対象の復元と新対象の同値保存を一transaction・一revisionで行う。
補正・revision・receiptは既存保存を使用。OCR読取・mapping・association・crop・受付snapshot・出力bindingを変更しない。
音声の原文とPCMは上限付きRAMだけ。失敗・未完了・切断で部分保存や自動録音再開を行わない。
通常DBはAPI台帳6件だけ更新、156/196、残40。通常無効／未移行、旧試験DBと855業務ファイルを保持。
59音声／保留境界と54共通API条件の限定検証は、全領域OCR安全性や実マイク受入の保証ではない。
画面はフェーズ5、統合は6、重い回帰・保存response／crop／paged／XLSX・実マイク・通常切替は7。

## 2026-10-09：Decisions／Responsesの接続（再設計フェーズ3・通常無効）

[フェーズ3記録](voice-editing-redesign-phase3-2026-10-09.md)の有限候補・原文Evidence・値の節を検証してから共通commandへ渡す。
候補全体・所有catalog・scope・行列の制約を維持し、最高scoreやモデルのtable refで重複表を一意扱いしない。
不正応答、timeout、候補不明、一部未解決なら群全体を未保存にする。障害による別モデル再試行を追加しない。
policy v2は明示完全一致だけ確率0.85／confidence 0.80、近似0.98／0.90、次点差0.20。小標本の暫定校正である。
意味付けはResponses low、値編集はnone。モデル用22操作を共有DTOから生成し、未検証の自由なpathを受け付けない。
引用した6万は文字列、六万は60000、先頭ゼロを保持。単位の一部の引用や別操作の値を拒否する。
OCR読取・mapping・association・crop・受付snapshot・出力bindingに変更はない。
通常DBはAPI予約13件だけを追加、150/196、残46。通常機能無効、DB未移行、実マイク・全体安全性の最終検証は未実施。

## 2026-10-08：再設計の共通command・v2 API（フェーズ2・通常無効）

[フェーズ2記録](voice-editing-redesign-phase2-2026-10-08.md)の通り、22操作と既存の補正／authoring実保存を共有部へ分離。
一指示の値・revision・履歴・receipt・command APPLIEDを同transactionで確定する。
同一session＋requestIdの再送は保存済み結果を先に取得し、異なるpayloadは拒否する。
scope／epoch／外部revision／取消は永続fenceで保存直前に検査。afterCommandIdは同sessionの説明可能な増分だけを認める。
raw指示と準備planはRAMだけ。再起動時は未保存指示と保留質問を中断し、保存receiptは復旧可能。
AI focusは安定したentity IDとrevisionの表示情報。手動選択・OCR判定・mappingを変更しない。
元の通常／試験DBは未移行、通常無効を継続。短いコピーAPI、代表的保存response・物理対応確認は全体安全性の完全検証ではない。
重い回帰、全22操作・paged・XLSX・実音声／実AIの最終検証はフェーズ7へ残す。

## 2026-10-08：音声・Decisions再設計の契約（実装前）

[再設計フェーズ1](voice-editing-redesign-phase1-2026-10-08.md)はediting.v2の実装基準であり稼働挙動の変更ではない。
有限候補はDecisions、複雑文・意味付けはResponses。手動／音声は共通の検証・保存へ接続する。
任意表選択、完全な候補集合、一指示の原子的保存、revision・所有・receipt復旧を必須とする。
AI枠／移動／scrollは一時UI状態であり、OCR判定・mapping・手動選択・scopeを変更しない。
数値表現、snapshot・crop・出力bindingを継承。通常DB移行／有効化は未実施。
物理identityの保持とDRAFTの明示意味編集を両立する。
以下の旧会話loop／60秒保留等は現行履歴であり、v2の目標値として使用しない。

## 2026-10-08：行・列指定の最終検証と追加API許可

[フェーズ3検証](voice-record-cell-phase3-2026-10-08.md)は行列/連続訂正の実マイクを通過し、数量表記の再試験待ち。
実OpenAI文字指示の行・列/欠けた列回答、保存responseとcrop、確定後XLSXセル対応を確認した。
全件backendは既知23失敗/34収集エラーを残し、全体成功とは扱わない。
利用者の追加100回許可を既存96回に加え、共有送信上限を196回とする。
旧台帳CHECKの100回上限は予約行・参照を保持して置換し、正の一意ordinalとtransaction予約を維持する。
197回目は送信前拒否、失敗/再起動で払い戻さない。通常の新編集schema/有効化とは分離する。
下記の100回予算記述は旧許可の履歴であり、現在の許可上限は本段落を優先する。

実マイクで60000が6万として保存されたため、音声 `ocr.set_value` に限定して純粋な整数の
数量表記をASCIIへ変換する。6万/六万/６万は60000、引用された「6万」や6万円は保持する。
各対象handleの元の指定値を変換根拠とし、複数セルの値を混同しない。明示ゼロ付き数字列の
先頭ゼロ保持も継続する。名称・手動値・既存OCR値・mapping・保存/receipt/revision契約は変えない。

## 2026-10-08：行・列指定の独立レビュー（フェーズ2）

各座標と発話の値を結び付け、モデルが別座標の値を返しても保存しない。
既知の値の解析不一致は既存の最大4要求内で修復し、上限で未保存終了する。
質問の種類と回答を60秒メモリに保持し、値質問が先の表・行・列回答を消さない。
欄名の助詞を削らず、列だけの指示や複合回答の明示表を保持する。
同名列の候補と保存通知には列番号を付ける。DB、OCR対応、補正・receipt経路は維持する。
[再現・修正・検証とフェーズ3の残条件](voice-record-cell-phase2-2026-10-08.md)を参照する。

## 2026-10-08：汎用の行・列音声指定（フェーズ1）

単一画像レビューの繰り返し表を、受付 `reviewStructure` の表示順による行番号＋列名/列番号で指定する。
番号は1始まりで空行・未割当列を含み、見出し行・行番号欄を除く。保存identity・mapping・cropは再生成しない。
表省略時は同じ連続音声sessionで実保存した直前表を優先し、欄がない場合は別表へ移らず質問する。
元の未保存値を保持し、表・行・列の不足分だけの返答で続行する。発話と異なる可視handleも保存を拒否する。
表文脈は同transactionで保存し、undo・複数表保存・選択/context更新・手動/外部revision・session終了で解除する。
通常DB移行・通常有効化・paged音声対応は含まない。
[実装、コピー検証、独立レビューと実マイクの残条件](voice-record-cell-phase1-2026-10-08.md)を参照する。

## 2026-10-08：音声で明示した数字列の表記（追加フェーズ3・進行中）

利用者が `ゼロゼロ七八九` の保存値を `00789` とするよう要求したため、音声の
`ocr.set_value` 解釈に限定した表記変換を追加する。command digest固定前に、発話と値に
明示ゼロがあり値全体が数字トークンの場合だけASCII文字列へ変換し、先頭ゼロを保持する。
近似候補による値の置換、名称/意味名/手動値の変換、OCR providerや既存保存値の再計算は行わない。
これは自動採番とは別の音声値解釈であり、既存の所有・receipt・revision・mapping対応を維持する。
完全一致の別名を部分一致より優先し、新しい値の検索だけでは対象変更を許可しない。
不足項目を同session・同binding・60秒のメモリへ保持する。対象質問への欄名だけの回答は
元の未保存値を保持して続行する。値質問の回答から別欄を選ばず、失効後に元指示を再実行しない。
[追加フェーズ3の変更・検証・未確認範囲](voice-target-autocorrection-phase3-2026-10-08.md)を参照する。

## 2026-10-07：欄名自動補正の新契約（追加フェーズ1）

利用者が近似一候補の自動補正、保存後の画面確認、読み上げなしを明示したため、直前の
「一候補でも聞き返して未保存」を変更する。従来の近似基準を満たす現在の所有候補が一つだけなら
対象検索結果のhandleを採用する。最高スコアだけの採用、複数候補/無候補/値不足の保存は行わない。
原指示と値を置換しない。近似だけでmappingや物理identityを再割当てする契約ではない。

連続入力/局所録音のTTS取得と再生を終了し、正式receipt通知と欄/パネルの短い強調で知らせる。
低動作設定は静的強調。選択・保存値・revisionを一時表示のために変えず、表示の遅い応答にも世代を照合する。
従来の読み上げ反響検査を、この読み上げのないUIの受け入れ条件として要求しない。
共通保存/undo/receipt、所有/ページ/局所範囲/turn/revisionのguard、OCR認識の契約は維持する。
DB/schemaの変更や通常有効化はない。静的検査/buildと独立レビュー/最終マイク検証を区別する。
[追加フェーズ1の記録と後続検証条件](voice-target-autocorrection-phase1-2026-10-07.md)を参照する。
[追加フェーズ2レビュー](voice-target-autocorrection-phase2-2026-10-07.md)で、可視性だけの対象許可を拒否し、完全一致優先・明示位置による近似絞り込み・強調残留を修正した。
近似も0件の検索は未保存で不足情報へ移行する。次の追加フェーズ3で全件回帰と実マイクを確認する。
以下の近似質問、TTSと反響に関する記載は変更前の履歴であり、この新契約を優先する。

## 2026-10-07：フェーズ19の近似対象質問と切替準備（進行中）

実マイクで認識名称の表記差が発生したため、近い欄名の質問を追加した。近似順位だけで保存せず、
一候補の対象質問への回答は元指示・同所有/同revision/同選択/同手動世代・期限内に限定する。
明確な変更指示への毎回承認は追加しない。新API有効時だけ旧一括/assistant writeを410で閉鎖する。
通常DBは未移行・新API無効。実TTS/echo/連続訂正と通常切替は未完了。
実試験後のSTT欄名ヒントは所有catalogの名称だけに限定し、現在OCR値・画像・全文会話を送らない。
音声停止後の遅い解析失敗は旧世代の表示を更新しない。保存開始後のreceipt復旧は維持する。
[進行中のフェーズ19](voice-authoring-phase19-2026-10-07.md)を参照する。


## 2026-10-07：フェーズ18の検証と音声停止境界

音声取得の開始・終了と遅い応答を同じ世代へ束縛する。停止した取得の終了通知、取消、訂正分類を新しい取得へ適用しない。録音終了中の停止では待機を終了し、旧音声を送信しない。保存済みreceipt・業務revisionを音声停止のために戻さない。

変更はfrontendの音声ライフサイクル2ファイルのみ。通常31テーブル・855ファイル、phase12の主要OCR境界8ファイル、72欄/290画像SHA、保存実応答25条件と実XLSX対応を照合し、既存OCR contractを維持した。全件backendテストは成功していない（旧仕様23失敗・34収集エラー、共有TEMPの1失敗は独立TEMPで再検証）。実マイク、通常移行、切替後の旧write拒否はphase19。[フェーズ18の証拠と限界](voice-authoring-phase18-2026-10-07.md)を参照する。

## 1. 目的と適用範囲

### 2026-10-07 連続取得と音声UI（フェーズ17・通常利用は無効）

[フェーズ17記録](voice-authoring-phase17-2026-10-07.md)の音声取得を既存controller/共通保存queueへ接続した。
deltaは字幕だけとし、commit item IDに束縛したcompletedだけを実行する。取得開始のページ/選択/手動世代を保持し、
先行する同じ会話の成功receiptだけでrevisionの進行を説明できる場合に限り後続指示を実行する。
新発話の取得・分類中は旧指示の保存を保留する。保存前取消しと保存後undoを分け、取消しだけで保存済みデータを戻さない。
TTSはサーバーの成功receiptまたは所有sessionの有効な不足情報質問だけを読み上げる。発話で再生を止めてもreceiptは変えない。

WS接続/最初の音声送信の予算予約と音声metadataを同一transactionに置く。接続5分、累計20分、一発話30秒、
保留最大2件/60秒を守り、失敗時の予約払い戻し・自動再接続・自動マイク再開を行わない。
新2テーブルはstream/itemのID・世代・時間・予約のみを保持し、音声と全文文字起こしは永続化しない。
手動OCRの全欄補正adapterも同じreceipt/履歴へ接続し、field集合・sourceRegionId・boundingBoxの変更を拒否して
既存correct/revision経路で保存する。生成mappingや受付snapshotを現在テンプレートから置換しない。

通常DBと新API無効状態を保持する。短いコピーAPI/ブラウザー確認は実マイク・実認識・全体回帰の代用ではない。
必須全体回帰は合意した18、利用者参加の実マイクと通常切替は19で行う。OCR安全性を完全には検証できていない。

### 2026-10-07 会話controller（フェーズ16・通常利用は無効）

[フェーズ16記録](voice-authoring-phase16-2026-10-07.md)の会話経路は、有限catalog/22操作の解釈結果を共通保存queueへ直ちに渡す。
承認操作は設けない。モデルはsession/turn/revision/request IDを指定せず、controllerが付与して要求digestを固定する。
モデル待機中にDB transactionを保持しない。保存前後の既存revision/対応/ページ/局所範囲検査を維持する。
曖昧な対象を最高スコアで選択せず、操作群全体を未保存として不足情報を質問する。質問記憶は60秒、世代と所有sessionに束縛する。

直前の単一値/名称の対象訂正は、元欄の復元と別欄への同値適用を一transactionで行う。
物理編集・複数変更・別値を過去のinverseから推測変換しない。訂正元IDを履歴に残し、undoは群全体を戻す。
保存後の中止・通信断・表示失敗で保存済みデータを戻さず、元receiptと正本revisionで復旧する。
全文会話を業務DBへ保存せず、タブjournalは型付き要求だけを保持する。
通常DBは未移行、新APIはdefault無効。連続取得/局所録音UIと通知は17、全体回帰は18、実マイク/通常切替は19。

### 2026-10-07 手動editorの共通保存接続（フェーズ15・通常利用は無効）

[フェーズ15記録](voice-authoring-phase15-2026-10-07.md)の手動アダプターを追加した。
意味差分は共通22操作へ変換する。枠・結合・印字修正・新領域はmanual sessionの許可項目だけを
既存authoringのprepare/persistへ渡す。モデルのツールに全graph・自由なpath・新座標を公開しない。
検出品質・既存path・結合前の物理履歴を手動snapshotで置換しない。物理変更を意味だけのundoで戻さない。

空表、未割当のマーク、名前なし、表の意味解除は有効な未完成DRAFTとして保存できる。
meaning.bind/unbindによる明示した接続変更で行所属を再生成しない。同じpathの表ラベル変更はrelation IDを保持する。
recordsの空rows/columnLabels、configure.name=null、markのmeaning=null/空optionsは、この下書き契約を表す。
表のrecordStructureVerifiedは現在の行の接続状態から導出し、OCR資格・ACTIVE化の代用としない。
geometry・所有・重複path・列数・ページ・識別参照・過去受付snapshotの既存guardは維持する。

手動書込はresource queueで直列化し、結果不明時は元キー/payloadのreceiptを照会する。
入力中・IME・drag中にflushせず、本人の成功応答でも後続入力を上書きしない。
競合で停止した入力は、保存版を明示確認するまで外部revisionへrebaseしない。
保存後の表示失敗を未保存と混同せず、復旧だけで保持入力を新要求として再送しない。
通常DBは未移行、新APIはdefault無効。連続音声の結合はフェーズ16以降、全体回帰は18、通常切替は19。

### 2026-10-06 共通編集APIの意味付け拡張（フェーズ14・通常利用は無効）

[フェーズ14記録](voice-authoring-phase14-2026-10-06.md)の13操作を追加し、共通APIは22操作となった。
仮想見出し・表所属・マーク・書類識別・書類名を最終graphで検証し、既存のauthoring保存と原子的receiptを通す。
この変更の境界は新編集APIであり、通常画面、OCR provider、crop、既存の保存フォーマット、過去の受付snapshotは変更しない。

- 行の既存handleは同じ物理セル配列に束縛する。順序変更で行ID・relation ID・targetPathを再生成しない。
  新しい未接続セルの保存先だけをserverが割り当てる。表の解除や行の除外は物理セルとその意味を保持する。
- 既存のreviewStructureはrecordLabelCellIdsを列単位で解釈するため、1列の複数ラベルを列配列へ平坦化しない。
  全ラベルはrelationに保持し、列の代表ラベルがそろわない場合はmappingの表示名から列名を導出する。
  recordStructureVerifiedはこの明示的な行と意味の対応を表す。layoutVerified・renderer資格・有効化の代用にはしない。
- マークは1set最大50選択肢。automatic/manualは同じ欄の保存済み領域を要求し、symbol/checklistは欄全体を使用する。
  別欄の領域・未対応composite・pagedのchecklist以外は拒否する。自由な座標を受け取らない。
- 識別と書類名の参照は最終graphの一意な通常text mappingへ解決する。識別labelは現行のmapping表示名を正本とする。
  依存不整合はtyped failureとして全体を保存せず、失敗receiptも再送で保持する。
- 見出しは循環・複数親・ページ越境を拒否する。ページと局所録音の境界は、セルだけでなく表・意味・識別・書類名の影響範囲にも適用する。
  undoは意味graphを新revisionへ戻し、品質確認と有効化は再計算・失効を維持する。

通常DBへのmigrationと有効化は行わない。短いAPI/DB・実XLSXページ検証と最終全体回帰を区別する。
§8の現行検証経路を使い、退役済みの旧verify_ocr_contract.pyや旧Googleテストを必須コマンドとして復活させない。

### 2026-10-06 共通編集APIの基本操作実装（フェーズ13・通常利用は無効）

[フェーズ13記録](voice-authoring-phase13-2026-10-06.md)の通り、有限handleと型付き9操作、session/turn世代、原子的batch/receipt、resource共有履歴と直前undoを実装した。OCR値は受付mappingとpreview対応が一意な文字欄に限定し、対応のない追加欄は保存済み値を保持した閲覧専用とする。対応情報のある欄の不一致は拒否する。補正とtemplate保存は既存serviceを使い、provider・crop・過去受付snapshotを変更しない。

`EDITING_API_ENABLED`はdefault false。新migrationは隔離コピーDBだけで確認し、実DBは未移行。旧write停止と新hand/voice同時切替は引き続きフェーズ19。基本操作の短い検証は最終回帰の代替ではなく、§8の全体検証と実paged境界は後続フェーズに残す。旧経路の契約は通常利用が切り替わるまで保持する。

### 2026-10-06 連続音声・共通意味付けAPIの次期契約（フェーズ12・未実装）

[フェーズ12統合設計](voice-authoring-phase12-design-2026-10-06.md)を、フェーズ13以降の編集APIと音声操作の設計正本とする。本段落は設計の追加であり、現行の実行契約を実装済みの新契約へ読み替えるものではない。

- 次期仕様では明示的な音声指示を共通の型付き操作へ変換し、対象が一意なら承認画面/固定句なしで保存する。現行の「モデルは対象を選ばない」「音声から直接保存しない」は旧経路の仕様であり、新経路はserver発行handle・capabilities・turn世代・revisionで制限する。
- 手動意味付けと音声意味付けは同じ保存APIと履歴を使う。一発話/一手動操作の複数変更は原子的な群。未完成DRAFTの保存と、不正graphの保存拒否を区別する。
- 既存の物理セル/page/region・crop・mappingの対応、受付snapshot、確定/有効化境界を保持する。新意味の明示作成や表所属変更による対応変更は履歴へ記録し、過去jobを再構築しない。
- 新undoは直前の手動/音声操作群を対象とする。構造全体のinverseを新revisionで適用し、過去の品質確認・資格・確定状態は復活させない。未実装の新inverseで旧履歴を実行しない。
- `readingMode=number`は自動採番。新APIの`sequence`と対応させ、通常の数字転記や識別設定の`valueType=digits`と区別する。pagedのchecklist許可と他の未対応mark制限は§13を維持する。
- 旧proposal自動承認、旧全layout書込による新API迂回、API予算のリセットは禁止する。HTTP/WSの予約、20分累計/5分接続の連続音声上限、旧write切替は統合設計§9〜10に従う。

フェーズ12は文書のみで、製品source/DB/実APIを変更しない。実装時の段階検証と最終回帰は統合設計§11〜12。元の契約を意図的に変える箇所と保持する不変条件を同設計に列挙した。以下のフェーズ4〜11の契約は、旧経路が稼働する間の正本として保持する。

### 2026-10-06 共通予算拒否の伝達（文之助フェーズ9）

SDKが送信前hookの予算超過・送信先拒否をAPIConnectionErrorへ包む場合、共通通信層で
元のAppErrorを復元する。OCRの接続失敗retryや音声の一般的なprovider失敗へ変換しない。
100回上限、送信前DB予約、失敗時の消費、文書別予約の非払い戻しは維持する。
この修正は認識の入力・model/prompt/schema・association・crop・補正・出力を変更しない。
保存済み実応答の5 callについて、正常・token不一致・欠落・重複・不明IDの計25ケースで
変更前後の送信引数・manifest・展開後の対応/値/判定が一致した。詳細は[フェーズ9記録](voice-assistant-phase9-2026-10-06.md)。

### 2026-10-06 音声接続と共通API予算（文之助フェーズ8）

[フェーズ8記録](voice-assistant-phase8-2026-10-06.md)の範囲で、音声と文章による指示を既存の4操作へ接続した。
対象IDはモデルに選ばせず、サーバーで検証した選択対象へ束縛する。STT・LLM・TTSの外部通信中はDBロックを保持しない。
提案生成・読み上げだけでは補正/authoringを保存せず、従来のrevision・所有関係・差分hash・challenge・receipt transactionを通す。
音声承認は同じ提示challengeの読み上げ完了後に開始する別録音turnと固定句を要求し、そのturnの消費も保存transactionに含める。
旧ボタン承認の要求digestは維持する。音声承認turnを指定しない再送に新しいnull項目を追加しない。

共通AI通信層 `app/ai` の `api_call_ledger` は、音声とOCRの実HTTP送信前に最大100回をDB予約する。
失敗・予約後の送信可否不明・再起動でも払い戻さず、SDK内retryとredirectを無効化する。
OCR providerの入力・model/prompt/schema・association・crop・出力変換は変更しない。新しい制約は送信予算の上限だけである。
音声に帳票画像・全layoutを送信せず、会話全文・生音声をDBと通常ログへ保存しない。
録音は上限付きのメモリ処理とし、サーバーでも内容形式・解読後の長さ・無音を検査する。

### 2026-10-06 永続undoと復旧（文之助フェーズ7）

[フェーズ7記録](voice-assistant-phase7-2026-10-06.md)のとおり、サーバー履歴からの逆操作を追加した。
現在revision=元操作afterRevision、現在対象値=保存後snapshot、編集可能状態を要求し、
対象FieldReadingまたはセルを共通補正/authoringで復元する。他欄・ID・crop・受付snapshotは維持する。
元履歴を削除せず、新revision・一意なundo_of_id・提案消費・receiptを同時commitする。
undoのundo、多段undo、手動編集・確定/再編集・公開をまたぐ巻き戻しは拒否する。
undoも差分表示・challenge・期限・context再検証を通し、過去の品質確認を自動復活させない。

承認済み要求だけをブラウザーjournalへ送信前に記録する。未承認案は画面を越えて復元しない。
結果不明時は元session/要求キーで照会し、未受付/PENDINGを失敗と断定せず同一要求を再送する。
同一originのタブ間はWeb Locksとstorage通知で協調し、DBのreceipt・revision照合を正本とする。
新sessionからresourceの履歴を取得できるが、他sessionのproposal/challenge/receiptを再送できない。
完了時にはブラウザーjournalからtoken・challengeを除去する。表示取得失敗を保存失敗に戻さない。
未保存入力・復元下書きは保持し、明示的な破棄後にだけ保存済み表示へ置き換える。
DB schema・既存データの一括変換、認識・association・crop・出力adapterの変更はない。

### 2026-10-05 提案・承認・適用（文之助フェーズ5〜6）

[フェーズ5〜6記録](voice-assistant-phase56-2026-10-05.md)を現在の接続範囲とする。
型付き手入力から、単一OCR値・DRAFTのセル名称・依存関係のない分類・所有選択肢名称を変更できる。

- 提案はサーバー正本から差分を計算する。対象はsessionに束縛したjob/version/page/cell/mapping/choice、
  resource revision、selection/local-edit世代。選択A→B→Aでもcontext世代が進み、古い案を復活させない。
- 提案の有効期限は120秒、sessionは30分の非操作期限。表示後のchallengeを要求し、単なる
  `approved=true`を受け付けない。challenge・hash・所有者・現在revisionを保存transaction内で照合する。
- 提示・承認情報の保存だけでは業務データを変えない。明示適用は補正/authoring・revision・履歴・
  逆操作payload・proposal消費・成功receiptを同時commitする。同じ要求キーの成功再送を現世代や期限より先に扱う。
- `LayoutCell.fieldLabel` は1〜100字の表示名。未指定はnullで、serializerは未指定keyを省略する。
  input mappingのlabelはfieldLabelを優先し、printedLabelText・ID・targetPath・座標を変更しない。
  fork・duplicate・package・結合partsで保持する。異なる表示名の記入欄を黙って結合しない。
- OCRの文字列は日付・数値へ推測変換しない。明示nullは空欄。対象の候補値と不確実理由だけを解消し、
  他欄・全reviewItems・sourceRegionId・boundingBox・受付snapshot・cropを維持する。
- templateの名称変更はlabel/headingのtext、inputのfieldLabelのみ。共通authoringが出力表示名と
  識別表示名を更新し、検証状態を失効させる場合は案に示す。接続・階層・繰り返し・marks・結合等の
  整理が必要な分類変更は拒否する。選択肢の追加・削除・並び替えは対象外。
- symbol/checklistの選択肢はlabelとvalueを同時更新し、両方を提示する。manual/automaticはvalueを保持する。
  保存済みページは明示page identityと元XLSXセルgeometryを検証し、ページ寸法・座標変換を変えない。
- 永続undo・再読込を越える復旧はフェーズ7、STT/LLM/TTSとAPI予算ledgerはフェーズ8。
  challengeは信頼するUIの提示処理との束縛であり、API単独で物理的なクリックの存在を証明するものではない。

### 2026-10-05 共通保存とrevision（文之助フェーズ4）

[設計書](voice-assistant-design-2026-10-05.md)に基づき、保存契約を意図的に変更した。
実装・検証・移行の範囲は[フェーズ4記録](voice-assistant-phase4-2026-10-05.md)を正本とする。

- TemplateVersionは整数revisionを持つ。authoring・mask・検証・公開等の更新をORMのCASで照合し、
  HTTP変更要求は`expectedRevision`を必須にする。geometry用の内容hashとは別の競合条件である。
- layout・mapping・識別項目・書類名は`PUT /template-versions/{id}/authoring`で一括保存する。
  設定ファイルは内容SHA付きの不変ファイルとして先に公開し、DB参照・品質・mapping sidecar・
  ページ設定を同じtransactionで切り替える。旧layout/field-mappings/print-layout-authoringのPUTは廃止。
- 単一OCRの補正・確定・再編集・再試行受付・再処理結果保存は同じrevisionを増分する。
  reopenにもbaseRevisionを要求する。過去のrevision番号・値・既存確定状態は移行で変更しない。
- 共通transaction内のサービスはflushまでとし、内部commitを拒否する。receiptの成功照会を
  現在revisionの照合より先に行う。操作・補正・履歴・成功receiptを同時にcommitする。
- provider、認識prompt、schema、association、crop座標、受付snapshot、出力セル対応は変更しない。
  生成・適用・undo・音声APIの接続は後続フェーズであり、この保存基盤だけで完成とは扱わない。

### 2026-10-04 汎用形式への非互換変換（現行仕様）

deploy版はOCR schema 2.0のみを受け付ける。値は `fields.<key>` に保存し、欄の意味は
受付時のmapping・reviewStructure・テンプレート設定で定義する。旧document/pedigree/
saleCalves/calvings/customFields構造は停止中の一回限りの変換で廃止する。旧構造や
欠落した受付snapshotを実行時に補う互換処理は設けない。詳細な変換結果は [互換廃止・汎用変換記録](generic-cleanup-2026-10-04.md) を参照する。画像帳票とXLSXページ帳票の入力経路の区別は残す。

- 変換は旧pathと新pathの対応を記録し、mappingId・region ID・crop・座標・列/行IDを保持する。
- 補正値・候補値・状態・alternatives・reviewReason・reviewItems・revision・確定日時を保持する。
  未設定かつ空の業種モデル初期値だけを除去する。手入力された値はラベル付きの汎用欄へ移す。
- 識別項目はテンプレートが指定する。全帳票共通の個体番号・10桁・一意性を要求しない。
- フィールド名から業種・単位・日付・前行の同上値を推測しない。同上は未確定候補として確認する。
  選択肢、数字桁数、連番は明示的な設定で扱う。
- 補正APIはfieldsとreviewItemsを受け取る。補正で欄集合、sourceRegionId、crop座標を変えない。
  revision競合は409、確定済み補正は拒否し、明示再編集を要求する。
- 確定はローカルtransactionで完結する。未確定XLSXは拒否する。画像帳票の出力は凍結した
  reviewStructure、XLSX帳票は受付時原本・adapter・bindingを使用する。
- Google認証・同期・検索・書類ライブラリ・業種候補辞書を起動、公開API、通常UIから除く。
- 検証は退避DBと保存済み実responseの比較、短いAPI integration、XLSX内容確認、frontend buildを
  優先する。廃止契約のテストは理由を記録して退避し、重複unitを追加しない。

この文書はOCRシステムの技術的契約の正本である。作業の優先順位・許可範囲・Git保護は [AGENTS.md](../AGENTS.md) に置き、詳細仕様を二重管理しない。

OCRはデフォルトで保護するが永久凍結ではない。明示された改善は再現根拠・比較・回帰検証を条件に実施できる（§10）。

正しさは文字列だけでなく、次の対応関係で判定する。

```text
document image → crop / physical region → physical cell identity
→ mapping identity → provider request → provider response
→ association validation → compact ID expansion → mappedCells
→ reviewStructure → ReviewPage → correction persistence → output cell
```

これは検証境界の列でありartifact書込み順ではない。文字列が正しくても別セルへの誤対応はOCR回帰である。文字認識誤りとmapping / association誤りを分けて評価する。

### 1.1 複数ページOCRの製品ゴールと完了条件

製品ゴールは、記入済みの複数ページ帳票から、各ページの明示的なテンプレートページと
読み取り欄へ対応付けたOCR候補を生成し、人間が結果・矩形・必要な補正を確認して
全ページを確定・出力できることに置く。

このゴールにおいて、手書き文字列の完全一致、スキャンと参照画像のピクセル完全一致、
または人間の確認なしに全筆画の正しさを自動証明することは完了条件ではない。
スキャンの回転、拡縮、平行移動、許容された射影歪みは位置合わせで吸収し、
矩形の恒久的な修正は既存DRAFTの明示的なテンプレート編集経路で行う。

ただし「完全一致を要求しない」は、値・ページ・矩形の推測再割当てを許可する意味ではない。
ページidentity、physical cell identity、mappingId、targetPath、sourceRegionId、crop、
受付snapshot、association検証、revision付き補正・確定、出力bindingは維持する。
位置合わせ・crop・associationに根拠が足りない結果は、隣欄へ推測して通すのではなく、
理由を保持して要確認または拒否とする。

§12の資格、入力identity・位置合わせ・crop対応検査、renderer隔離はこの方針だけで削除・緩和しない。
本契約は、未実装の自動保証を「複数ページOCRと人間レビューの操作フロー」全体の
完了条件へ無制限に拡張しないことを定めるが、既存gateを迂回する許可ではない。
候補OCRへ進める条件の変更は§12.9を参照する。それ以外の条件を変更する場合も、§10に従い、ページ別の根拠・
要確認への遷移・補正保存・出力対応を検証して明示的に変更する。

### 1.2 認識精度と通常運用コストの方針

通常運用の目的は、システムだけで手書き文字を完全一致させることではなく、利用者が確認・修正できる
有用なOCR候補を、複数ページの正しい欄との対応を保って提示することである。OCR結果は未確定候補であり、
利用者が全ページの値と切り出し画像を確認し、必要な手動修正を行った後の明示確定を正本とする。
低信頼・判定不能を完全一致に見せる補完、自動確定、確認工程の省略は行わない。

必要な精度の最低条件は、文字列の完全一致率ではなく、少なくとも次を満たすことで判定する。

- 入力ページが正しいテンプレートページへ結び付いていること。
- 位置合わせが既存閾値内で成立し、対象欄のcropとphysical cell / mapping / 出力先の対応が保たれること。
- 認識結果と低信頼・失敗理由がレビュー可能な形で提示され、利用者の補正がrevision付きで保存されること。
- 未確定結果を確定済み出力として扱わないこと。

文字単位の実用精度は、代表的な実記入サンプルで候補正答率と手動修正量を測定して評価する。
現時点で未測定の数値目標を達成済みとは扱わない。一方、完全一致の自動証明を通常利用の開始条件にはしない。

人間レビューを前提とする候補OCRでは、通常の画面表示、OCR受付、OCR実行のたびに、Office再描画、
正式資格の全再発行、全ページ画像の完全一致確認を繰り返さない。これらの重い処理は、初回準備、対象版・原本・renderer環境・証拠の変更、
証拠失効時など、再準備が必要な境界で明示的に実行する。通常利用では保存済み証拠の現在性確認と、
実入力ごとのpage identity・registration・crop correspondence・association検査を残す。
処理時間を短縮する場合も、失効条件を無視するcache、推測mapping、検査失敗時の継続によって実現しない。

## 2. 現在の実装境界と保護対象

以下は実装を照合する入口である。表のパスは `backend/app/` 基準（frontendのみリポジトリroot基準）。ファイル名だけでなく、対象データを生成・変換・保存する処理を保護する。

| 境界 | file / class・function | 保護する挙動 |
| --- | --- | --- |
| 受付・snapshot・retry | `services/job_service.py` — `JobService.create_ocr_job` / `retry` | 入力・templateSnapshot・fieldMappings・出力snapshot、冪等性 |
| job実行 | `jobs/runner.py` — `JobRunner`、`jobs/handlers.py`、`services/document_ocr_execution_service.py` — `DocumentOcrExecutionService.execute` | queue、attempt、progress、cancel / retry |
| XLSXページ定義・geometry | `domain/print_layout.py`、`services/print_layout_service.py`、`services/workbook_geometry.py` | 版所有、保存range、geometry version、page-local transform（§12） |
| paged受付・実行 | `domain/job_pages.py`、`services/job_page_service.py`、`services/paged_ocr_execution.py` | 明示binding、全ページsnapshot、ページ別artifact、部分失敗・retry（§12） |
| reference・環境認定 | `services/reference_qualification_service.py`、`services/renderer_environment.py`、`services/office_reference_renderer.py`、`services/renderer_raster.py`、`services/reference_crop_safety.py` | 原本保持、evidence失効、隔離、font、crop安全性、本番gate（§12） |
| template・rectangle / region検出 | `services/template_service.py` — `TemplatePreparationService.process`、`services/layout_service.py` — `LayoutService.detect` / `prepare_layout`、`services/template_authoring_service.py`、`layout_analysis/detector.py`、`segmentation/`、`template_recovery/` | layout、cell / region、ラベルと入力セルの対応 |
| 画像抽出・crop | `services/extraction_service.py` — `ImageExtractionService.process`、`extraction/region_cropper.py` — `RegionCropper.crop`、`image_processing/`、`extraction/` | alignment、前処理、padding込みcrop.bbox、fieldPreview |
| orchestration | `services/ocr_workflow_service.py` — `OcrWorkflowService.process` / `_create_provider` | provider生成・呼出し、DB・artifact保存 |
| request計算 | `services/ocr_request_preparation_service.py` — `OcrRequestPreparer.prepare` | mapping選択、region入力、request、初期mappedCells |
| provider / association | `ocr/openai_provider.py` — `OpenAIOcrProvider`、`_compact_request_ids`、`_build_request_manifest`、`_association_tokens`、`_expand_compact_item` | 送信画像、compact ID、token、応答検証・展開 |
| model / prompt / schema | `core/config.py`、`ocr/prompt_loader.py`、`ocr/transcription_models.py`、`domain/ocr_models.py`、`backend/resources/templates/generic-document-v1/`（root基準） | primary / fallback設定、prompt、wire / 内部schema。OpenAI転記はload_transcription_promptとcompact modelを使用 |
| response計算 | `ocr/deterministic_mapper.py` — `build_mapped_document`、`services/ocr_response_finalization_service.py` — `OcrResponseFinalizer.finalize` | field ownership、値・判定・警告、mappedCells、diagnostics |
| reviewStructure・表示 | `services/review_display_structure.py` — `build_review_display_structure`、`frontend/src/pages/ReviewPage.tsx` | 構造由来group、保存mapping、行移動 |
| 補正・確定・reopen | `domain/ocr_models.py`、`services/ocr_result_service.py` — `OcrResultService.correct` / `confirm` / `reopen`、`repositories/ocr_result_repository.py` | 原候補・補正・revision履歴・確定snapshot |
| XLSX出力 | `services/spreadsheet_template_output_service.py` — `SpreadsheetTemplateOutputService.snapshot` / `render`、`services/output_layout_template_service.py`、`services/excel_export_service.py` | 受付時原本・セルbinding、凍結reviewStructure、確定revision |

`backend/app/ocr/**` と上表のデータ契約はOCR-sensitiveである。Reactやテンプレート編集でも、見た目だけか、保存layout / mapping / cell associationを変えるかで分類する。

現行 `OcrResultService` は取得・補正・再編集・ローカル確定・XLSX取得を所有する。
表示画像は `ocr_result_preview.py`、補正値の型は `ocr_result_types.py` に置く。
旧同期facade、外部同期service、業種別の文書統合は廃止した。

単一画像の主要経路は `JobRunner → PipelineJobHandler → DocumentOcrExecutionService.execute → ImageExtractionService.process → OcrWorkflowService.process`。新しい`jobPages`形式は同じjob lifecycle内で`PagedOcrExecution`へ分岐し、既存の抽出・workflowをページ別に利用する。request/response計算へHTTP・DB・filesystem責務を持ち込まない。依存テストは直接importと特定のfilesystem呼出しの検査であり、推移的依存の完全分離は保証しない。

以下の既存ReviewPage・単一OcrResult・補正・確定・出力の実装説明はlegacy経路のもので、新paged結果に対応済みという意味ではない。§3〜7のidentity・association・値保護の契約は新経路にも適用し、未対応操作は§12に従い明示拒否する。

Quest / MRや非HTTP入口でも既存の受付・snapshot・job lifecycleを利用し、revision・cancel・retry管理を迂回しない。

## 3. Physical identity・mapping・crop

### 3.1 物理領域と認識結果の所有権

以下は物理セル側のidentityであり、認識文字列そのものではない。

- `mappingId`、`openAiMappingId`、`targetPath`、`sourceRegionId`
- `boundingBox`、crop / `fieldPreview`
- template上のcell / region、row / cell structural identity

同じ受付snapshot・入力に対する対応は決定的であること。理由なく別fieldへ付け替えず、値を移動・交換しても物理セルidentityは位置側に固定する。

### 3.2 Crop correspondence

`template cell ↔ sourceRegionId ↔ boundingBox ↔ crop` が一致し、意図した帳票領域を指すこと。`RegionCropper.crop` はpadding込みの実領域を `RegionCrops.bbox` と画像群に保持する。crop境界・canonical座標・alignment・画像SHAを対応させ、UIだけのscale / offsetで誤対応を隠さない。

値交換に伴ってcropを別位置へ移す、boundingBoxだけ隣セルへ変更する、sourceRegionIdとpreviewを不一致にする実装は禁止する。

### 3.3 推測補正の禁止

mappingのずれの帳尻合わせとして、空欄OCR結果の削除、array compaction、後続値の前後シフト、隣セルのコピー、配列index・応答順・文字内容だけによる再割当てを行わない。sourceRegionId・boundingBox・cropを値に合わせて変更しない。不確かな対応は安全側へ失敗させる。

別の帳票や別jobの値を、番号・ラベル・配列位置だけで統合しない。業種別の文書merge経路は廃止した。

### 3.4 手動追加した多角形

新しい多角形セルは `shapeType=polygon` とcanonical座標の `vertices` を保持する。
頂点の外接矩形を既存のx/y/width/heightと一致させ、3〜100頂点・重複なし・
面積あり・自己交差なしを検証する。矩形には頂点を要求せず、既存データを移行しない。

多角形の頂点はFieldMappingConfig、RegionConfig、受付snapshotへ引き継ぐ。
RegionCropperは既存のpadding込みbboxで切り出した後、多角形の場合だけ領域外を
白、ink_differenceを0にする。入力画像全体を変更しない。画像端の多角形のpaddingは
canonical領域内に制限する。矩形のpadding・切り出し画素・mapping保存形式は維持する。
空のverticesを矩形のconfig/snapshotへ追加して既存hashを変更しない。
provider・model・prompt・association方式は変更しない。

Canvasのハンド操作による明示的な配置修正では、編集可能なDRAFTの同じcell IDと
意味づけを保持して座標を平行移動できる。多角形の頂点、結合parts、所属行の外接矩形を
対応させ、ドロップ時に既存layout保存経路へ渡す。仮想見出しは移動後もOCR対象外。
元XLSXのセル・出力先を移動する操作ではなく、過去jobのsnapshotを変更しない。
§12のページ投影矩形の固定座標制約は解除しない。

対応範囲・境界テストは[テンプレート手動編集の拡張](template-authoring-extensions.md)を参照する。

### 3.5 単一ページ位置合わせの追加復旧

従来の位置合わせが `LOW_REGISTRATION_CONFIDENCE` で失敗した場合に限り、
双方向のdescriptor比率検査で相互に一致するORB特徴点から別候補を生成できる。
帳票名・項目名に依存する分岐や既存の一致数・inlier数・比率・残差閾値の引下げは行わない。
従来成功経路の画像画素と採用条件は維持する。

追加候補は既存の幾何条件に加え、ECCスコア、補正後の射影形状・向き・残差、
全体の輪郭一致、紙面3×3区画と受付snapshotの読み取り枠付近の印刷輪郭を検証する。
独立した画像輪郭検証には特徴点の対応表を流用しない。32 edge画素未満の領域は
検証根拠とせず、少なくとも4区画と、根拠がある全対象領域が既存の輪郭閾値を満たす必要がある。
空の記入欄だけで追加候補を認定しない。枠数・選択範囲を変換推定へ使わない。
印刷輪郭一致は手書きの認識正解率や、全筆画のcrop clearanceの保証ではない。

追加経路の最終変換はECCを合成したnormalized-image → template-image変換として記録する。
失敗時にもinput / template / config SHAと数値、比較画像をjob / attempt（pagedではpageも）
に分離保存し、切り出し・providerへ進めない。mappingId・targetPath・sourceRegionId・
保存矩形・paddingを再生成せず、過去jobのsnapshotや公開版を変更しない。
新printLayoutの認定gate解除には使用しない。

再現・実装範囲・検証の限界は[位置合わせ失敗調査](registration-investigation-20260911.md)を参照する。

### 3.6 スマホ撮影写真の明るさ補正と向き（2026-10-18・意図的な変更）

利用者の要望（暗い・ゆがんだ写真への対応）と破壊的変更の許可による。旧契約は「位置合わせ・切り抜きは
撮影した画素をそのまま使い、テンプレートの向きでだけ書類の輪郭を探す」、新契約は次のとおり。

- 単一画像・ページ別の抽出は `image_processing/photo_registration.register_document` を通す。最初の候補は従来と同じ
  向き・同じ輪郭正規化で、紙がテンプレートより暗い（中央値で20以上）か、むらが大きい（5〜95%点の差で20以上）
  ときだけ紙の背景で割って平坦化した画像を使う。その候補が不合格なら撮影したままの画像、さらに縦横を入れ替えた
  輪郭の90°・270°回転を試す。どの候補も既存の `TemplateAligner` の受入条件をそのまま通る必要があり、閾値は変えない。
- 輪郭検出用の縮小画像だけ、95%点が190未満なら明るさを伸ばす。
- 平坦化した候補で位置合わせできた場合に限り、位置合わせ済み画像へ雑音除去と輝度だけの濃度復元をかけ、
  手書き分離・記号判定・切り抜きはその画像から作る。ペンの色は保持する。
- テンプレートと同程度に白い入力は画素・変換・切り抜きとも従来と同一（複製DBの保存済み14ジョブで位置合わせ済み画像の
  SHA-256一致を確認）。採用した回転・平坦化・試行の一覧は品質情報 `photoRegistration`、失敗時は
  `registrationFailure.diagnostics.registrationAttempts` に記録する。
- mappingId・targetPath・sourceRegionId・保存矩形・padding・provider・model・prompt・schema・association・受付snapshotは変更しない。

合成スマホ写真40条件の前後比較（欄四隅の誤差）・実OCR・画面での確定/XLSXの記録は
[スマホ写真・スマホ画面・文字拡大](phone-photo-and-mobile-2026-10-18.md)。

## 4. Provider request・association・mappedCells

### 4.1 Request manifest

各画像とmappingの対応を送信前に確定し、最低限以下を追跡可能にする。

- batch / call番号、request order、compact ID、original mappingId、targetPath、sourceRegionId
- 元画像、準備後画像、実送信画像のSHA-256と一致関係
- association token、model、prompt version、schema version

`provider input image ↔ association token ↔ compact ID ↔ original mappingId ↔ targetPath ↔ sourceRegionId` の対応を保つ。配列順序だけを証拠にしない。

### 4.2 Response validationとcompact ID展開

受理前にcompact ID、association token、item count、duplicate / unexpected / missing ID、schema validityを検査する。配列位置、応答順、compact IDだけ、前後の文字、意味的なもっともらしさでは値を信用しない。

tokenが期待値と一致しない値は保存しない。`OCR_ASSOCIATION_MISMATCH` では既存の安全側動作を維持し、warningだけにして値を通過させない。現providerは不一致を除外し、対象mappingへ `raw=None` / `uncertain` と理由を付ける。欠落は `OCR_MAPPING_MISSING` として扱い、後続値をずらさない。

現 `_repair_single_compact_id` は単一要求・単一応答に限定され、その後のtoken照合も必須である。複数セルへの推測割当てに一般化しない。

展開は元mappingへの決定的変換とし、original mappingId、targetPath、sourceRegionId、physical cell ownershipを変えない。duplicateの処理も既存の競合検出・安全側判定を維持する。

### 4.3 mappedCellsとdiagnostics

request準備が初期 `mappedCells` payloadを生成し、response finalizerが認識結果を反映し、Workflowがartifactを保存する。件数やfield ownershipを表示都合で変えない。blankの無条件削除・index shift・neighboring value補完は禁止。件数変更には明確な仕様根拠が必要である。

`openAiMappingDiagnostics` は、明示的な置換設計・実装・テストなしに削除・縮小しない。call / batch / request order、compact ID、original mapping ID、targetPath、sourceRegionId、画像SHA、association token、raw response item、expanded item、mappedCells result、request ID、modelを追跡する。

### 4.4 設定も契約の一部

model、fallback、prompt / prompt version、structured-output schema、association schema、request形式、image detail、前処理、batch size戦略、provider SDK利用方式の変更もOCR-sensitiveとする。

provider・model・promptVersion・schemaVersion等、保持している識別子を記録し、コード未変更でも設定差分を追う。`associationVersion` 等を全jobで永続化済みとは仮定しない。

### 4.5 手書き抽出画像の同時送信（2026-10-18・意図的な変更）

利用者の明示要求（「手書きの時・分を読むこと」「空欄の印字を値にしないこと」）による変更。

- 旧契約：印刷の項目名（`printedLabelText`）を持たない記入欄は、元の色の切り抜き1枚だけを送る。
  受付時に `handwriting_png`（手書き抽出）を作り `openAiImageCount` には数えていたが、実際の送信内容には含めていなかった。
  そのため印刷の斜線・括弧・項目語（日付欄の「／」、区分欄の「( )」、連絡先の「電話」）が値として返り、
  丸で囲んだ選択肢と手書きの数字が同じ枠にある欄では選択肢だけが返った。
- 新契約：上記の欄（`candidate_type` なし、`handwriting_png` あり）に限り、元の画像の直後に
  `handwritingOnly:<compactId>` の文字と補助画像を1枚追加する。補助画像は、手書き抽出で墨のある画素を
  半径4pxだけ広げた範囲に元の色の画素を置き、それ以外を白にしたもの（`_restored_handwriting`）。
  抽出処理で欠けた線を元の画素で戻すため、記入の無い欄は真っ白になる。サイズが合わないときは抽出画像をそのまま使う。
  ASSOCバナーは元の画像だけに付け、補助画像には付けない。
- 指示文（`HANDWRITING_HINT_INSTRUCTIONS`）は補助画像を送る要求にだけ追加する。記入の有無は補助画像で判断し、
  文字の形は元の画像でも確かめる。記入が無ければ印刷があっても `s=b`。記入があれば丸で囲んだ印刷の選択肢と、
  年・月・日・時・分・番・号・斜線のような1文字の単位・区切りだけを値に含め、項目名の語句は含めない。
  補助画像の印刷の消し残しは元の画像で印刷と同じ位置・書体なら印刷として扱う。候補プールのある欄の判定は変えない。
- 記号（symbol_choice）・チェックリスト・管理値の欄、印刷の項目名を除外して手書き抽出を元の画像として送る欄は、
  送信内容・指示とも従来どおり。mappingId、association token、compact ID、sourceRegionId、boundingBox、crop、
  応答schema、受付snapshotは変更しない。request manifestには `contentHandwritingHintIndex`、
  `handwritingHintSha256`（送信した補助画像）、`handwritingSourceSha256`（手書き抽出）、`handwritingHintMatchesPrepared` を追加した。
- prompt versionは `mapped-compact-v2`（既定値と `.env`）。過去のjob・受付snapshotへは後付けしない。

検証（データの複製、GPT系の既存OCR経路で実送信）：同じテンプレート・同じ画像の変更前のjob結果（attemptの `result.json`）と比較した。
F06の日付欄で印字「／」6欄・「( )」1欄が空欄になり、記入した日付「4/1」「4/8」「4/15」と区分は維持。
F02の時刻欄（文字の欄）は「午前」だけから「午前10時30分」へ（最終形の指示で2/2回）。F01の未記入の電話欄は「電話」から空欄へ。
F08（191欄）は途中版で差分0。値の取り違え・欄の対応違いは0。表記の違い（全角空白・区切りの省略・単位の付加・✓とレ）は残る。
途中版で見つかった後退（抽出画像の欠けた線による「4」→「1」、ふりがなの脱落、記号欄の「午前」が要確認になる、
印刷の消し残しの読み込み、住所への項目名の混入）は、補助画像の作り方と指示の範囲指定で解消を確認してから最終形にした。
既存の契約テスト（tests/ocr_contract、test_openai_cost）は17件成功、4件は本変更と無関係の既存失敗（廃止した業種引数 `mother_individual_id`）。
詳細は [汎用化の記録](template-ai-generic-forms-2026-10-17.md) の「読み取り本体の変更」。

## 5. Review contractとUI / 業務状態

### テンプレート文字の自動配置

`LayoutCell.textVerticalAlignment` は表示用の `top` / `middle` / `bottom` / null。
新規XLSXは元セルの縦配置、画像は既存の文字検出領域の上下余白から求める。
文字検出がない場合はnullとし、自動表示では従来の上配置へfallbackする。
既存XLSXは同じ版の `spreadsheetPrintCells` に保存されたidと矩形座標・寸法が
一意に一致する場合だけ不足情報を補う。既存の配置指定は上書きしない。
自動ボタンは各矩形の横・縦配置を使用し、手動表示切替は保存値を変更しない。
これは表示metadataの追加であり、文字列・物理identity・mapping・crop座標・
provider契約を変更せず、過去jobのsnapshotや公開版のDB内容を書き換えない。

### 5.1 reviewStructureと行移動

テンプレートの新しい見出し階層はLayoutCellの `parentLabelCellId` に明示保存する。
見出し区分は既存の `parent_label` を使用し、親専用・仮想専用の区分を増やさない。
新しい接続は既存relation・targetPath・結合・cell IDを再生成せず、単一親と循環拒否を
守る。既存relationの親接続も循環検査に含める。仮想見出しは `virtual=true` の
名前付きmanual見出しで、配置用の座標をOCR領域として使用しない。
仮想見出し自身へのfield mappingは生成せず、ラベル文字候補OCRからも除外する。
囲い込み対象は現在の論理セルに限定し、結合前のpartsから子を再生成しない。
過去jobのreviewStructureや補正・確定値を新しい階層から再構成しない。

繰り返し群への見出し接続はrecordSectionsのparentLabelCellIdへ保存し、
個別列の意味づけへ置き換えない。同じcollectionの複数帯は従来どおり同じ親を持つ。
見出し階層の循環検証には、この群から見出しへの既存接続も含める。

表示上のgroup化とfield identity統合を混同しない。独立項目のgroup identityはlabel文字列でなく `parentLabelCellId + labelCellIds` に基づく。現在の `_logical_field_group_id` はlabel cell IDを重複除去・sortし、情報がなければ `field:{mappingId}` にfallbackする。

過去jobの表示は保存済みreviewStructure / fieldMappingsを基準とする。reviewStructureまたはrecordStructureがない結果は409で拒否する。現行TemplateVersionやラベルから勝手に対応を再構築しない。

認識結果に属する `raw`、current value、`normalized`、`status`、`alternatives`、`reviewReason`、warning / review item情報と関連metadataは、行移動時に一式として移す。物理identityは行位置側に固定する。行オブジェクト全体のswapや、文字列だけ移して警告を元位置に残す実装は禁止する。

汎用の疎な行では全列の安全な対応が成立しなければ行移動を拒否する。空文字のfieldとfield自体の欠落を区別し、存在する列だけの部分交換、仮field生成、mapping補完を行わない。

### 5.2 データ・業務状態・一時UI状態の区別

| データ / 状態 | 意味と所有権 |
| --- | --- |
| `raw` / current value / `normalized`、`status` / `reviewReason` / `reviewItems` / alternatives | 認識・検証・ユーザー補正に属する内容。OCR初期判定だけに限定されない |
| confidence相当情報、association evidence、diagnostics | 実装が保持する証拠。confidenceをユーザー確認済みの代用にしない。`FieldReading` に汎用confidenceフィールドがあるとは仮定しない |
| boundingBox / crop / field identity | §3の物理的対応。UIのselectedや編集状態とは独立 |
| `result.confirmed` | サーバーが返す、この結果のローカル確定・レビュー作業完了の正本 |
| selected / editing / loading / modal / popover visibility | 一時UI状態。再描画を理由に補正データや確定状態を初期化しない |
| locked / displayed warning | 業務状態と一時状態からの派生値。lockはrevision競合でも生じるため完了と同義ではない |
| Spreadsheet同期状態 | 外部出力の状態。ローカル確定と独立（§7） |

現 `FieldReading` は `readable` / `uncertain` / `blank` を持つ。`blank` はraw・normalizedがnull、`uncertain` はnormalizedがnull、`readable` はrawを必要とする。この意味・正規化をUI都合で変更しない。`reviewReason` は判定理由、`reviewItems` はpath・severity等を持つ確認情報であり、ポップアップ表示フラグではない。

明示的なユーザー補正は既存経路で許可する。`ReviewPage.updateReading` はfieldのpatchと該当pathのreviewItems除去を行い、backendが再検証する。確定表示やloading終了のためだけの一括消去・成功判定への置換は禁止する。

### 5.3 レビュー表示と状態遷移

テンプレートの自動保存では、保存中の一時状態だけで編集領域全体を無効化しない。
同じエディターの保存要求は直列化し、送信後の追加編集を古い保存応答で上書きしない。
ブラウザー下書きは最新の編集内容が保存された場合だけ消去する。保存後の関連取得失敗も
永続化状態が不明な失敗として扱い、元の値へ戻した編集を含めて下書きを保持する。
同一エディター内の工程切替は未保存の状態と保存キューを保持したまま実行できる。
工程切替を保存完了とは扱わず、移動先の追加編集を古い保存応答で上書きしない。
画面終了・再検出・明示保存・承認の完了境界、既存のAPI検証・revision照合を迂回しない。
実装範囲と検証は[テンプレートの非ブロッキング自動保存](template-background-save.md)を参照する。

作業完了に応じた表示はデータを壊さない派生値で表す。現ReviewPageの `reviewCompleted` / `displayedWarningPaths` も `result.confirmed` を基準にする。独立したローカルreviewed stateで正本を複製しない。

| 契機 | 維持する状態・UI |
| --- | --- |
| 未確定結果を取得 | 必要な警告・編集UIを表示し、自動確定しない |
| 確定レスポンスで `confirmed=true` | レビュー編集stateを閉じ、判定・mark / choice編集UIと作業としての要確認表示を終了する。修正値・判定根拠は保持 |
| 再取得・再マウント・loading終了 | 最新のサーバー状態に従う。同じ確定結果をOCR初期状態へ戻さない |
| 確定・保存API失敗 / revision競合 | 修正値と復旧手段を保持。lockやエラーを確認完了の代用にしない |
| セル選択・crop閲覧 | 確定後も閲覧を許可し、レビュー編集UIの再表示とは分離する |
| 明示的なreopen成功 | サーバーの `confirmed=false` に従って編集・必要な警告表示を有効化する |
| XLSX取得失敗 | 修正値・確定状態を保持し、原因を示して再取得できるようにする |

## 6. 補正・revision・再読取

### 6.1 補正と確定snapshot

`OcrResultRepository` は `raw_candidate_json`、`current_corrected_json`、`OcrResultRevision`、`ConfirmedResult` を区別する。表示のために原候補から補正済み値を作り直さない。

`correct` はREVIEW_REQUIREDを要求し、candidateを正規化・検証する。repositoryの `save_correction` はbase_revision条件付き更新、revision増分、source付き履歴を記録する。競合を上書きせず、mapping / sourceRegionId / boundingBox / field ownershipを保存・再取得まで照合する。

`confirm` はユーザーが見たbaseRevision、validation、受付時の識別項目と明示書類名を検査して確定snapshotを保存する。確定や正規化は不確実なOCRを自動的に正読へ昇格させる許可ではない。

`reopen` はbaseRevisionと確定snapshotの世代を検査し、jobをREVIEW_REQUIREDへ戻してconfirmed_atを解除する。OCR再実行はせず、result revisionと履歴を増分し、補正済み値・過去の確定snapshotを保持する。`confirm`も新revisionを作り、現在のConfirmedResultと追記専用のOcrConfirmationRevisionへ同時に記録する。

### 6.2 retry

`JobService.retry` は状態制約、受付入力・template snapshot、attempt管理に従う。
最新templateへ差し替えない。別OCRジョブは別の結果として扱い、旧業種のRESCAN_PAGE・
document-resolution・parity/indexによる文書mergeは公開APIと実行経路から廃止した。
`save_provider_result` の再処理履歴と補正履歴を区別する。新旧jobのID一致だけで値やcropを移さない。

## 7. ローカル確定とXLSXセル

### 7.1 現在の処理順と失敗

単一画像の確定はbaseRevision、validation、受付時の明示識別条件を検査し、XLSX生成を
成功させてから確定snapshotとjob状態を同じローカルtransactionで保存する。
`confirmed=true` の正本はサーバーのjob状態。Google / Drive同期と再同期endpointは廃止した。
XLSX生成失敗は未確定のまま値を保持する。commit後の応答消失は未確定と決め付けず再取得する。
確定済みの取得失敗だけで確認を解除しない。再編集は値・revision・履歴を保持する。

### 7.2 OCR値から出力セルへの対応

Spreadsheet template出力は受付時の `spreadsheetOutput` と `fieldMappings` snapshotを基準に、targetPathの値を `_destinations` と結合anchorへ解決する。UIの配列順や表示名で出力セルを推測しない。元XLSXと生成コピーを区別し、OCRテキストを式として扱わない既存処理を維持する。

`OutputLayoutTemplateService` のローカル保存layoutと `ExcelExportService` の出力も対応境界に含める。UX都合でbinding・sheet・元cell・確定revisionを変更しない。

### 7.3 Excel出力の元結合範囲（2026-09-05修正）

OCR identity契約は変更しない。Excel出力についてのみ、従来の左上座標の
列挙に加え、元XLSXの結合範囲を左上セルで代表する取込形式を許可する。
元XLSXから解決した範囲の和集合が、重複・欠落のない矩形の場合に限る。
空白に見える領域でも未選択セルを推測で追加してはいけない。

範囲展開を利用する新規出力snapshotはschemaVersion 2とし、各結合の
sourceRangesを保存する。元XLSXのSHA-256、sheet、保存したpartsから
出力時に一致を再検証する。現在はschemaVersion 2のみを受け付け、schemaVersion 1を拒否する。
parts、mappingId、targetPath、sourceRegionId、boundingBox、crop、
reviewStructure、補正保存およびprovider associationを変更しない。

出力コピーでは、選択した元結合範囲に完全一致する既存結合だけを解除して
再結合できる。従来拒否していた既存結合の包含を、この根拠がある場合に限って
許可する。部分交差、別結合との重複、数式消失、未承認の内部値消失は拒否する。
元XLSXは書き換えない。重複・欠落・部分交差・式消失の拒否は現在のschemaVersion 2でも維持する。

選択したliteral部品と元セル値の比較では、取込・編集時に除去される前後空白の差を
非空の文字列に限り許容する。比較だけであり、原本・保存partsを正規化しない。
内部空白、文字種・大小文字の差を同一視せず、数式とinput部品の内部値保護も維持する。
テンプレート名・特定ラベル・セル番地による例外は設けない。

ページ結合の版コピーでは、TemplateVersionServiceが付与する20桁のhex suffixを持つ
元セルIDも同じ元Excel座標へ解決する。その他の仮想分割IDへ範囲全体の所有を拡張しない。
ページ保存では取込元のcanonical矩形との一致も検証し、コピー後に元結合範囲を縮小しない。

この変更はExcel出力契約の拡張であり、OCR認識・物理identity契約の変更ではない。

### 7.4 汎用帳票の識別とXLSX出力（2026-10-04）

すべての現行テンプレートは `generic-document`。OCRの正本はschemaVersion `2.0` の
`fields` と `fieldLabels`、保存先は `fields.<key>`。旧階層path、単一documentIdentity、
CSV設定、業種辞書・年号・単位の推測は廃止した。旧補正payloadを無視して受理しない。
現行パッケージはformat/schemaVersion 2、旧package v1は拒否する。

識別情報は `documentIdentityFields` の配列に明示設定する。番号・名称・日付・組合せを
扱い、全帳票共通の番号・10桁・DB主キーの一意性を要求しない。明示されたdigits / exactLengthは守る。
書類ライブラリの検索・自動統合は行わず、読み取り後に設定した値とcropを利用者が確認する。
2026-10-16追記：確定済み結果だけを対象に、1利用者DB内で書類名・識別値による一覧と
構造化投影（`structured-document.v1`、`/api/v2/structured`、既定無効）を読み取り専用で提供する。
自動統合・推測値の保存は引き続き行わない。範囲は[設計](structured-qa-design-2026-10-16.md)§2。
2026-10-17追記（書類の系列、段階2）：利用者が受付時に**明示的に選んだ**確定済みの単一画像jobを
前の版として、新しいjobを `document_series_links` で結び付けられる（`predecessorJobId`、
alembic `document_series_v1`）。条件は、前のjobが確定済み（CONFIRMED・確定revision一致）、
同じ `templateVersionId` かつ同じ `fieldMappingHash`、paged以外、前のjobに取消以外の後継がないこと。
前の版の確定revisionと確定snapshotのSHA-256を受付snapshot（`documentSeries`）とリンクに記録し、
後継の確定時に一致しなければ409 `SERIES_PREDECESSOR_CHANGED`。後継のある版の再編集・削除は
409 `SERIES_HAS_SUCCESSOR`。前のjobの値・確定snapshot・cropは変更しない。構造化一覧は確定済みの
後継を持つjobを除き、版番号を付ける。段階2の後継（`mode=full`）は全欄を読み値を引き継がない。

**2026-10-18 意図的なOCR contract変更（書類の系列、段階3、`mode=blank_only`、新規の更新の既定）**：
§3.3「別jobの値を統合しない」・§6.2「新旧jobのID一致だけで値を移さない」の例外として、次の条件を
すべて満たす場合に限り、前の版の確定値を新しいjobへ引き継ぐ。旧契約は「別jobの値は一切引き継がない」、
新契約は「利用者が明示的に選んだ確定済みの前の版から、同一mappingの確定値だけを引き継ぐ」。
- 上記の受付条件（明示選択・同じ版とfieldMappingHash・確定revisionとSHAの固定）を満たす。
- 引き継ぐ欄は**受付時に固定**し、受付snapshot `documentSeries.carried`（mappingId→確定時の
  raw/normalized/status/alternatives/reviewReason）に保存する。対象は、前の版の確定値が同じフィールドキーで
  同じ `sourceRegionId`（=mappingId）に結び付き、statusが `readable` / `uncertain` の欄だけ。
  空欄、識別欄（`documentIdentityFields`）、mapping外の値（旧形式の追加項目など）は引き継がない。
- 位置合わせ・切り抜き・`mappedCells` の件数は全欄で従来どおり。providerへの要求からだけ引き継ぐ欄を除き、
  要求する欄の manifest・compact ID・association token の検査は変えない。引き継いだ欄は `mappedCells` で
  `sentToOpenAi=false`・`localResolution=carried_from_predecessor`、件数を `carriedFromPredecessorCount` に記録し、
  artifact `incrementalPlan` に引き継ぎ・要求のmappingIdを保存する。
- 引き継いだ読み取りの `sourceRegionId` は同じmappingId、`boundingBox` はこのjobの画像の切り抜きを使う
  （前のjobのcropを移さない）。以後の補正・確定・出力は通常のjobと同じ経路・検査を通る。
- 確定時、識別値が前の版の確定値・読み取り文字列のどちらとも一致しなければ、利用者の明示
  （`acceptIdentityMismatch: true`）なしに確定しない（409 `SERIES_IDENTITY_MISMATCH`）。
- 系列のないjob・pagedの結果は変わらない（保存済み画像の同一条件で変更前後を照合済み）。
未移行DBではリンクを作れず（503 `SERIES_NOT_MIGRATED`）、既存の確定・再編集・削除は従来どおり。
設計と検証は[追記分の取り込み](incremental-update-and-ask-gui-design-2026-10-17.md)§15。

画像帳票は受付時の非空fieldMappingsと凍結reviewStructureから独立項目・見出し・明細表を
出力する。列はcolumn ID、値はfield ID / mappingId / targetPathの一致で解決し、ラベルや
配列位置から推測しない。全enabled mappingが一度ずつ対応することを検証する。
同名列、明示空セル、途中・末尾の空行、未mappingの保存済み手動項目を保持する。
構造不足や不一致は409で拒否し、出力時に対応を補充しない。

値はnormalized（nullならraw）を文字列として出力し、先頭ゼロ・改行・式文字列を保持する。
列を編成する際も既存relation.targetPathと欄IDを保持し、保存先を再生成しない。
原XLSXがある帳票は受付時adapterと原本SHAによる経路を優先する。
未確定出力拒否、確定revision、所有原本・paged cell binding・書式保持を維持する。

変換済みの既存候補・補正・確定値は再計算しない。今後の新規OCRでは、例えば `26 9 27` を
業種別の生年月日と見なして西暦へ推測変換しない。既存保存値 `2014年9月27日` は保持する。
対応関係の維持と、この意図した正規化仕様変更を区別して報告する。

## 8. 検証基盤と変更範囲別の検証

### 8.1 現在の検証方法

保存済みの実provider response、停止時の退避DB、同一受付snapshot、crop SHAを比較し、
短いAPI integrationとXLSX内容照合を行う。検証script・fixture・ログは製品外に保存する。
旧 `scripts/verify_ocr_contract.py` と廃止仕様のテストは退役理由付きで退避済み。
その旧成功件数を現行schema 2.0の回帰成功として使用しない。
現行の条件・コマンド・結果・退役一覧は [互換廃止・汎用変換記録](generic-cleanup-2026-10-04.md) を参照する。

### 8.2 選択する検証

| 変更 | 必須検証 |
| --- | --- |
| 文書 | 現行コード・参照パス・文書間整合性、diff確認 |
| frontend表示・操作 | `npm run build` と関係する実画面操作。保存・競合・確定・再取得等の必要な遷移 |
| backend / DB / lifecycle | 短い隔離API integration、対象静的検査。導入・起動・停止・再起動への影響は実入口で確認 |
| OCR / mapping / association | 同じ保存responseからrequest・manifest・compact ID・応答・値・判定・所有権を比較。重複・欠落・不明IDを検査 |
| crop / template / 補正 / 出力 | mappingId・targetPath・sourceRegionId・boundingBox・画像SHA、revisionと保存値、XLSXセル対応を比較 |
| paged / renderer | §12の所有原本・ページbinding・実入力・geometry・隔離gateを変更範囲に応じて実測。単一画像の成功を代用しない |

ユニットテストの追加・全件実行は既定にしない。短い上記検証で未確認の具体的な境界だけ、
最小の既存テストや追加検証を選ぶ。必要時のpytestは [Artifact Storage Policy](../ARTIFACT_STORAGE_POLICY.md)
に従い、未使用のDドライブ絶対パスをbasetempに指定する。既存tempやユーザーデータを削除しない。

### 8.3 比較と報告

変更前のworktree・入力・受付snapshotと、変更後の同じ条件を記録する。意図しない値・ID・
crop・revision・確定状態の差分は回帰として調査する。意図した非互換変換は旧新仕様と理由を記録する。
未検証範囲、失敗、中断は成功と分ける。全体unit / E2Eを実行していなければ全件成功と表現しない。
必要な対応関係の回帰が残る場合は完了扱いにしない。

## 9. Golden・テスト保護・実API

### 9.1 比較データの要件

Golden replayでは同一画像・layout・manifest・provider responseからidentityも比較する。未整備部分に最低限揃えるケースは以下。

- independent fields：通常テキスト、数値、複数値の論理項目、空欄、grouping
- repeated section：3行以上・複数列、status / alternatives / reviewReason / warning、行移動と保存
- association mismatch：期待tokenと応答token不一致、値を保存せず隣接fieldへshiftしないこと
- long text / blank transition：文字あり→unknown→後続ずれ→空欄で見かけ上復帰する系列を検出すること

比較対象は存在するmappingId、openAiMappingId、targetPath、sourceRegionId、boundingBox、association result、raw、normalized、status、mappedCells構造、reviewStructure ownershipを含む。文字列だけの比較は禁止。

### 9.2 テストと期待値の保護

実装を通すためのtest削除・skip、assertion弱体化、都合のよいfixture変更、mismatchケース削除、Golden比較項目の削減は禁止する。失敗時はまず実装の契約破壊を調査する。

Golden expectedを現在出力へ合わせるだけの更新は禁止する。変更できるのは、ユーザーが要求した意図的なOCR契約変更で、新しい期待仕様が明確であり、回帰テストで証明する場合のみ。旧新契約・理由・影響を報告する。新しい再現ケースの追加と既存期待値の差替えを区別する。

### 9.3 実API利用

保存実response / Golden比較 → 短いAPI・出力integration → 未確認の境界検証 → 必要時のみ実APIの順で検証する。保存responseやmockで足りる条件にOpenAIを使わない。実API呼出しの既存許可・予算を守る。

model・prompt・schema・provider request・association方式変更、または固定fixtureだけでは評価できない精度課題では実API smokeを検討する。実施時は目的、API calls、image count、model、association mismatch、mapping誤対応、schema error、recognition-only errorを分けて記録する。未実測項目を0件と報告しない。

## 10. OCR改善と意図的な契約変更

### 見出しのない番号欄（2026-09-26）

記入欄の「番号を読む」は、2026-09-26の利用者の訂正要求により、自動採番の設定とする。
明示選択されたinputセルだけに `readingMode=number` を保存する。未設定の既存セルには
metadataを追加しない。保存先がない場合だけ、重複しない「番号」の項目と、見出しを
必要としない既存relationを追加する。設定済みtargetPath・cell ID・矩形は維持する。
保存時に番号欄をcanonicalのy、x、cell IDの順に並べ、1開始・1増分の
`sequenceNumber`をfield mappingへ固定する。region・受付snapshotにも保持し、
実行やページ抽出の際には数え直さない。これは文書ごとの欄番号で、文書間通し番号ではない。
番号欄はOpenAI/PaddleOCRの送信・検査対象から外し、画像内容に関係なく保存済み番号を
文字列のFieldReadingとして生成する。sourceRegionId・boundingBox・targetPathはその欄の
既存対応を保持し、mappedCellsに`automatic_sequence_number`と送信対象外を記録する。
番号欄だけならprovider呼出しは0回。番号を含む要求では帳票全体画像もproviderへ送らない。
①や先頭ゼロ等の印刷表記は転記しない。レビュー・手動補正・確定は既存経路を使用する。
旧行番号roleへ変換しない。model・応答schema・association・crop・出力先の対応は変更しない。
以前保存したsequenceNumberのない番号OCR版・受付済みjobは従来の転記動作を保持する。
新方式を適用するには編集用の版でレイアウトを保存し有効化する。既存jobへ後付けしない。
別形式のAI汎用定義では番号設定を保持できないため、黙って落とさず既存の未対応拒否へ含める。
今回の検証は明示要求に従い最小XLSXを使う実画面確認を中心とし、全体テストは実行しない。

### 記入欄内の記号判定（2026-09-25）

明示された記号読取の要求により、単一ページ経路の `markRecognitionMode` に
`symbol` を追加する。既存の `automatic`（印刷選択肢の差分検出）と
`manual`（利用者が選択）の意味・既定値は維持する。
UI上は記入欄の読み取り方法として文字・記号・印刷選択肢を選ぶ。
保存表現は既存の `role=mark` / `markSets` を使用し、新しい矩形roleを増やさない。

`symbol` は単一選択に限定し、欄全体の既存cropと候補ラベルを
`candidateType=symbol_choice` として既存のOpenAI batchへ渡す。
候補別のgeometryは互換保存用であり、記号の分類やcrop生成には使わない。
文字検出や選択肢別の差分検出結果を記号の認識結果として扱わない。
候補と完全一致した確実な応答だけを採用し、空欄・候補外・複数記号・不確実な
応答を強制的に候補へ補正しない。通常の確認・手動補正・確定経路を維持する。

model、応答schema、association token、mapping/sourceRegion identity、crop、
受付snapshot、revision、出力先決定は変更しない。新モードのrequest指示だけを追加する。
既存jobへの後付け移行は行わない。paged経路のmark拒否も維持し、ページ別編集では
記号モードの新規選択を無効にする。実画像での認識精度はoffline境界テストだけでは保証しない。
検証の範囲・結果は [記号判定の実装記録](verification/symbol-reading-20260925.md) を参照する。

### 単一ページOCRの文字読み取り選択肢（2026-09-29）

明示要求により、単一ページOCR画面の「文字読み取りも実行する」トグルを初期状態では隠し、
文字読み取りを必須とする。backend設定 `OCR_TEXT_RECOGNITION_TOGGLE_ENABLED` が `false` の場合、
frontendはトグルを表示せず、受付値を `ocrEnabled=true` に固定する。
`JobService.create_ocr_job` は同じ設定状態での `ocrEnabled=false` 受付を拒否する。
画像処理のみの経路やprovider処理自体は削除せず、既に受付済みのjobは受付時snapshotどおり実行する。

トグルを復活させる場合は `OCR_TEXT_RECOGNITION_TOGGLE_ENABLED=true` としてbackendを再起動する。
`/health` からその状態をfrontendへ渡し、従来の画像処理のみの選択を利用可能にする。
この設定はトグルを持つ単一ページOCR受付だけに適用し、paged OCR、mapping、crop、association、
補正・確定・出力、既存job snapshotの意味は変更しない。

### Template編集の版境界

mapping生成validationの失敗を空mappingへの置換保存に変換しない。
不完全なDRAFT layoutは保存できるが、既存fieldMappings・document identity・logical field
identityのpresent状態を保持し、layoutFinalized=falseとする。再取得は現在layoutの
mapping生成validationを正本にvalidForOcr=falseと対象fieldの警告を返す。
保持したmappingを読み取り確認・ACTIVE化・新規OCR受付で有効化しない。
frontendの未割当（保存先なし）と設定不完全（保存先あり・mark必須設定不足）を区別し、
共通fixtureでbackendとの判定一致を固定する。過去job snapshotは再検証対象にしない。
2026-10-05以降は統合authoring保存を使用する。設定ファイルの不変公開後に、DB内のlayout・config参照・品質・sidecar・準備jobのページ設定を同時にcommitする。失敗で残った未参照ファイルを有効な設定とは扱わず、旧設定ファイルを上書きしない。

公開済みTemplateVersionのlayout・field mappings・mask・汎用定義を直接変更しない。
ACTIVEからの編集は同じ原本のDRAFTを1つ作るか再開する。途中保存では版を増やさず、
承認時にactiveを原子的に切り替える。旧版は既存のARCHIVED状態へ移行するが内容は不変。
系列とlogicalFieldIdを継承し、mapping/cell/region/準備job/artifact identityは新版へ分離する。
logical identityのsidecarをprovider associationの代用にしない。
過去jobは受付snapshotを保持し、旧jobへlogical IDを後付けしたり新版から再構築したりしない。
詳細と意図的なlifecycle変更は [Template Versioning](template-versioning.md) を参照する。

### 契約変更の手順

2026-10-04のデプロイ版の依頼では、汎用化・最適化に必要な破壊的変更が明示的に
許可された。対象と検証方針は [AGENTS §1.1](../AGENTS.md#11-デプロイ版の汎用化最適化に関する許可2026-10-04)
を参照する。変更を必要とする具体的な問題、旧契約と新契約、既存データへの影響を示し、
両文書を同時に更新する。ユニットテスト追加は極力避け、保存response・実API・
短いintegration・出力比較で契約を確認する。許可の取得と検証結果は区別し、
未検証の変更を検証済みと報告しない。

OCRコア変更には、AGENTS §5の明示されたタスク範囲、再現可能な問題または測定可能な精度課題、変更前後比較、§8の検証が必要である。コード整理・抽象化・美観・一般化・speculative optimization・将来用途だけでは変更しない。

既存契約に戻す不具合修正は、不変条件の書換えを必要としない。発見した新しい境界違反は再現・原因を示して修正する。UXタスクで別のOCR不具合を発見しただけでは、依頼範囲を拡張する権限にはならない。

契約自体を変える場合は、ユーザーの明示要求に基づき、以下をすべて行う。

1. 旧契約と新契約、必要性、影響範囲を提示する。
2. この文書を更新し、新契約を証明する回帰テストを追加・更新する。
3. Golden fixtureへの影響を確認し、必要な期待値変更は§9.2に従う。
4. 必要に応じてmigration・実API smokeを実施する。
5. 検証結果と残る不確実性を報告し、意図的なOCR contract変更と明記する。

不変条件を暗黙に緩めない。不確かな対応を推測で通過させない。

## 11. Baselineと完了報告

旧文書には、Phase 0〜9で実Chrome E2Eまで確認したという履歴がある。記録内容は、manifest / raw / expanded / mappedCells対応（当時association mismatch・誤対応0件）、行移動時の物理identity固定と認識metadata移動、補正保存・再読込、displayName・独立項目group・同名別項目分離、セル直接編集・判定popover、疎な行の操作拒否とcorrection非発行である。

この履歴は現在のrevision / worktreeへの保証でも、今回の実UI再検証でもない。baselineは作業開始時のrevision・worktree・テスト・fixtureで記録する。

OCR-sensitive変更の完了報告には、次を含める。

- 変更ファイル、pipeline stage、影響するinvariant、原因と意図
- mapping identity、physical identity、provider association、crop / boundingBox、prompt / model / schemaの変更有無
- 変更前後の検証コマンド・結果、Golden / 保存response比較の結果と差分、backend regression、frontend検証
- 実OpenAI / Google API使用有無・回数、実測したmismatch / mapping誤対応 / schema error / recognition-only error
- `git diff --check`、未検証範囲、既存失敗・環境制約

OCR動作を変更していない場合は「既存OCR contractを維持した」と明記する。同じ画像から意図したセル・mapping・検証済みresponse・レビュー・補正保存・出力までの対応を証明できることが完了の基準である。

## 12. XLSX複数ページOCRの安全契約

本節は新printLayout / paged OCRの追加契約であり、§3〜10のprovider・model・prompt・schema・association・mapping・補正・確定・出力契約を緩めない。現在の対応状況と作業ルールは[AGENTS §9](../AGENTS.md#9-xlsx複数ページocr--reference-renderer)、実験・測定はそこから参照するPhase報告に置く。

### 12.1 版所有・保存range・geometryの互換性

`printLayout`はTemplateVersionが所有し、Template系列へ共有して公開版の意味を変えない。保存・解除はDRAFT制約とrevision再照合を要求し、検証fingerprintにページ設定を含める。準備jobの定義・画像hashは受付済みOCR jobのsnapshotと区別し、過去jobへ後付けしない。

ユーザー確定済みpage rangesを最優先し、版に保存済みの範囲をmanual breaks・目標ページ数・自動計算だけで再構成しない。再配置は明示的な編集として行う。行・列・元セル範囲を保持し、pixel矩形だけを正本にしない。

論理セル座標と物理ページ座標を区別する。元セル範囲からpage-local rectangleと基準画像への変換は、保存した用紙・方向・余白・全ページ共通のuniform scale・offsetに従う。連続編集ビューのrangeを物理ページそのものと扱わない。

geometry modelの変更を旧保存プラン・legacy canonical rectangle・preview・受付済みjob・OCR結果へ暗黙適用しない。旧X/Y画像合わせ補正も一律にuniform print scaleへ置換しない。旧データの移行が必要なら、§10に従い明示的な版変更・migration設計と比較検証を分離する。

2026-09-14追加の `wsl-calc-print-grid-v1` は、明示印刷確定APIで新モデルを選択した
DRAFTだけに適用する。固定runtime ID、原本/FODS SHAと固定inch出力を要求し、
fit/中央配置用の公称twipと、各寸法を整数化して累積する描画グリッドを分離する。
旧版のGET・通常保存で移行せず、既に新モデルの版は再確定で旧モデルへ戻さない。
canonical layout・元セル範囲・意味づけ・過去jobは保持する。印刷範囲が成立しない場合は
候補を拒否し、座標clampや許容値拡大で救済しない。
FODSだけではUNO寸法exportのoverflow不在を証明できないため、この下書きモデルと
artifact生成成功を本番資格へ昇格させない。詳細は[Calc印刷グリッド](verification/calc-print-grid-20260913.md)。

新Calcモデルの参照検証は、測定開始前・資格レポート保存前・保存済み資格のGETで、
所有原本/canonical layout/configと保存寸法からページを再計算し、全cell/fieldの元範囲・
bindings・座標・transform・ページ所属を保存planと照合する。不一致を再保存して修復せず拒否する。
描画上同じ `shrinkToFit` の省略/falseだけを比較時に揃え、identityや座標を正規化しない。
旧geometryモデルや過去jobへの適用、GETでのOffice起動、資格発行・本番gate解除は含めない。

### 12.2 ページを含むphysical identityと入力binding

新経路のfieldは`TemplateVersion → TemplatePage identity → source cell/range → mapping → page-local rectangle`を一意に追跡できなければならない。同じX/Yでも別ページのfieldは別の物理領域であり、ページ情報なしで保存・処理しない。

現TemplatePage identityは版IDと基準画像artifact IDに由来し、受付入力はJobInput UUIDで識別する。`templatePage → inputPage`は明示bindingで固定し、アップロード順を逆にしても同じ対応を維持する。配列index・ページ番号だけ・OCR文字・ラベルからidentityやfield所有権を推定しない。

### 12.3 受付snapshotと実行・retry

1 logical document/jobの受付transactionで全ページのbindingを検証し、少なくとも次をimmutable snapshotへ固定する。

- TemplateVersion、page identity・順序・ranges、元XLSXのidentity / SHA
- ページ別基準画像・参照元artifact・SHA、transform、source cell/rangeを持つfields、fieldMappings
- OCR設定とprovider / prompt / schema識別子、画像処理・registration等の実行設定
- 新OpenAI paged受付は[prompt本文と応答model schema](paged-transcription-resources.md)も
  内容・SHA付きで保存する。実行時の本文再読込や欠落snapshotの暗黙補充を行わず、
  現在の応答model schemaとの不一致は画像処理/provider呼出し前に拒否する。
  この検査だけでSDK/native依存等のruntime全体を固定・認定したとは扱わない。
- 入力ページidentity、binding、inputRevision、入力SHA

文書識別・書類名設定がある新規受付では、検証済み全体configを維持し、
`documentMetadataAdapter=paged-document-fields-v1`とページ別`documentContext`を固定する。
識別項目のページ所属はmapping IDとtargetPathの一致で決め、全体のgroup/役割/順序を変更しない。
読込時にも全体configからの所有権と各contextの完全一致を検証する。旧受付へ後付けしない。

実行・retryは受付snapshotを正本とし、現行TemplateVersionからranges・fields・mappings・基準画像・設定を差し替えない。全ページの入力・基準画像SHAとsnapshot整合性を最初のprovider呼出し前に検証する。保存画像を再生成して不一致を隠さない。

現実行設定が受付時と異なる場合、最新設定で続行せず明示拒否する。retryで成功ページを再利用できるのは保存結果のSHAとページsnapshotが一致する場合だけであり、同じ受付snapshotで失敗ページを再実行する。過去jobの再現性を新しいgeometryや認定artifactの適用で変更しない。

新規OpenAI paged受付は文書全体の`ocrCallBudget`と専用予算表を同じtransactionで固定する。
2026-09-22以降の新規受付は、各ページの受付mapping数とdocument identity項目数をproviderの
現batch規則で計算した`ocrCallBudgetPlan=paged-ocr-retry-reserve-v1`も固定する。
全ページ最低枠と同数の文書再試行余力を受付時に要求し、設定上限が合計必要数未満なら
catalogを利用不可、acceptを送信前拒否とする。ページ数・帳票名・固定件数では判定しない。
設定上限は引き続きhard ceilingであり、受付後のjobへ増額・再計算・予算追加を行わない。
全page/attemptで上限を共有し、送信前にpage ID・attempt付き予約と使用回数をcommitする。
失敗・応答消失・再起動・再試行で予約を払い戻さない。予算のない旧受付へ後付けしない。
未完了全ページの最低呼出し数が不足する場合は最初の送信前に拒否し、
optional再読取から後続ページの最低枠を保護する。予約済み回数と実送信済み回数を混同しない。
providerがSchema不成立・途中切れとして破棄した応答は、受付snapshot、batch、mapping、associationを
変更せず、設定済みretry回数と残予算の内側で再送できる。破損応答の値は保存・補完しない。
この再送も予約を消費するため、受付時の再試行余力が常に未使用のまま残る保証ではない。
既存の単一ページ予算契約とprovider association方式は維持する。
詳細・migration・失敗復旧は[文書全体API予算](paged-ocr-budget.md)を参照する。

### 12.4 全ページ処理と未対応操作

`jobPages`形式を単一入力経路へ渡さない。欠落・重複・余剰・unsupportedページを黙って除外せず、全fieldのページ所有権を検証する。ページ別artifactを分離し、単一OcrResultへ上書きしない。各ページでも既存のrequest準備・provider association検証・response確定計算の契約を守る。

全結果を`documentPages`へ集約し、一部失敗を文書全体成功と扱わない。現実装では部分失敗はjobをFAILEDとし、成功ページの証拠を保持する。OCR計算の全ページ成功もレビュー・確定・出力完了の代用にはならない。

未対応の操作は明示拒否する。Page 1だけのOCR・レビュー・exportへ縮退して成功扱いにしない。ページ設定を扱えないpackage出力等でもmetadataを黙って落とさない。

2026-09-13の本番化要求に基づき、専用`page-review`取得・補正APIと`PagedReview`を追加した。
全ページ成功と受付snapshot・page/input/reference・field/cropの一致を確認した結果だけを保存する。
legacyの`OcrResult`へ上書きせず、page IDをキーとする全ページの原候補と現在値を分離する。
全ページ共通revisionの条件付き更新と、編集page ID付き履歴を同じtransactionで保存する。
補正では受付mapping IDが当該ページに所属することを要求し、sourceRegionId・boundingBoxを
クライアントから受理しない。他ページの値・物理対応・原候補・認識diagnosticsを変更しない。
画像品質を含む既存の決定的検証を再適用する。現在のTemplateVersionやruntime設定で
レビューを再構成せず、成功ページの再実行・再公開で補正済み値を初期化しない。
保存中の閲覧・追加編集・ページ切替を維持し、遅い応答で新しい下書きを上書きしない。
通信断・revision競合では下書きを保持し、明示的な最新結果取得後に再試行できる。
続けて、受付bundle schemaVersion 2の文書全体snapshotと全ページ確定・reopenを追加した。
受付transactionでlayout全体（見出し・繰り返し・結合を含む）、config、reviewStructure、
logicalFieldIds、承認済み汎用定義、既存XLSX出力snapshot、page ID/source rangeによる出力bindingを固定する。
原本のSHAを確認したjob専用コピーを所有artifactとして保持し、元作成jobや現行版の変更を参照しない。
現在は受付bundle schemaVersion 2のみを実行・レビュー可能とし、schemaVersion 1を拒否する。必要な出力snapshotがない全ページ確定も拒否する。
新形式の実行では所有コピーの範囲・SHAも最初のprovider呼出しより前に検証する。

確定は全ページ共通baseRevisionと全page IDの明示確認、既存の値/画像品質検証を要求する。
PagedConfirmationへ全ページの現在値・文書名・受付bundle SHAを一括保存し、jobのCONFIRMEDと同じtransactionでcommitする。
OCR値・statusを自動昇格せず、単一ConfirmedResultへPage 1を代入しない。
paged経路では確定・reopenもrevisionを増分する。単一OCRにも2026-10-05から同じ増分原則を適用する。pagedと単一の保存モデル・API・受付identityは引き続き分離する。
これによりreopen後の古い確定/補正要求を拒否し、過去の確定snapshotと全履歴を保持する。
確定の成否が不明な通信失敗では再取得まで状態変更/編集を止めるが、通常の補正保存では追加編集を維持する。
競合で保持した下書きは確定済み値として表示せず、明示reopen後に編集値として復元する。
書類ライブラリは製品対象外。既存単一ページ用APIの拒否と本番受付gateは維持する。
確定済みpaged結果の読み取り専用投影（2026-10-16、`/api/v2/structured`）は同じ確定判定
（status・revision・snapshot・documentNameの一致）を使い、確定行の存在だけでは投影しない。
意図的な新paged保存/確定契約の拡張と残る制限は[全ページ確定](paged-confirmation.md)に記録する。
詳細とmigration適用状況は[ページ別レビュー](paged-review.md)を参照する。

識別項目のOCRヒントは当該ページの検証済みcontextだけから既存request準備へ渡す。
書類名候補は受付全体設定の順序で、全構成項目が非空readableの場合だけ生成する。
候補の更新で入力中の書類名・確定済み名を上書きせず、自動確定や書類間結合に使用しない。
詳細と互換性・制限は[ページ別の文書metadata](paged-document-metadata.md)を参照する。

新規受付は`documentSnapshot.outputAdapter=ooxml-paged-values-v1`も固定する。
専用XLSX取得は全ページ確定済みの指定revisionだけを対象とし、受付の所有原本SHA、
page ID・mapping ID・targetPath・sourceRange/sourceParts・anchorの対応を再検証する。
現行版や準備job、画面の配置・項目名から出力先を推定しない。生成はDB書込lockの外で行い、
応答前に同じ確定revisionと受付SHAを再確認する。出力失敗だけで確定解除・値変更を行わない。
新adapterのない旧受付は後付け更新せず出力を拒否する。詳細は[全ページXLSX出力](paged-output.md)。

### 12.5 原本保持とunsupported workbook

原本XLSXを不変とし、可能な箇所でSHAを照合する。図形・画像・controls・高度なOOXMLの削除、hidden行列の可視化、根拠のないused range切捨て、数式の値化、未対応要素を失うopenpyxl再保存、元pageSetupの破壊によって対応済みに見せない。原本を変更して同じ帳票と主張しない。

新renderer用の一時コピーは原本と区別し、検証済みadapterが保存page range・印刷条件を反映するための必要最小限の変更に限定する。未編集のセル・style・ZIPパートを保持し、対応を証明できない形式は拒否する。内部printLayoutの管理を、出力XLSXの印刷設定を変更する根拠にしない。既存出力契約は§7に従う。

新pagedの記入済みXLSXは別adapterによる結果成果物であり、元sheet名・非表示管理sheet・
印刷設定を保持する。全体をopenpyxlで再保存せず、明示所有セルの文字列値と検証済み手動結合だけを
OOXMLのbyte範囲で更新する。空白確定値は当該セルの元placeholderを消す。数式として書き込まず、
先頭ゼロ・改行・エスケープ様文字列を保持する。結合の範囲、内部値、他項目との重複を検査し、
同じ物理セルへ異なる値が要求された場合は拒否する。旧単一ページの出力仕様は変更しない。

離れたprint area、print titles / repeated rows・columns、header/footer、横複数ページ、hidden行列、shapes / controls / formulas、高度な書式・OOXML、未確認font、過大range等を推測処理しない。安全な対応が未検証ならunsupportedまたは未認定として本番への流入を止める。OOXML属性の未指定とライブラリdefaultも区別し、取得できない設定を推測で正規化しない。

明示的に追加した[印刷用帳票取得](template-print-files.md)は、対象TemplateVersionの保存済み
printLayoutを使う別成果物の生成である。横1ページ×縦Nページの保存rangeから成果物の
Print_Area・manual rowBreaks・印刷条件を設定できる。原本、保存版、過去job、既存の
OCR結果出力（§7）は変更しない。小数％倍率等、既存adapterで再現できない条件は拒否する。
この取得成功・PDF生成成功をreference資格や本番OCR利用許可に昇格させない。

### 12.6 Qualificationと本番gate

目視によるページ編集は[ページ単位の目視編集](page-authoring.md)に従う。
`draft-visible-cells-v1`は非表示行列を表示幅・高さから除き、セル内縮小を参考描画する
未認定の表示モデルである。保存済みlegacy/LOモデルを自動更新しない。原本の設定・セルを
書き換えず、元セルIDからページ表示座標を参照する。元セルの表示座標はlayoutへ保存しない。
2026-10-05以降は統合`PUT /template-versions/{id}/authoring?expectedRevision=...`でDRAFT/revisionを確認する。
通常矩形と結合partsへ展開した元セル集合は
重複のない既存集合の部分集合とし、残存元セルのphysical ID・canonical座標・形状を保持する。
既存LayoutServiceへ人間の区分・意味づけ・明示的な削除・同一ページ内の結合/解除を保存する。
結合親は元partsの外接矩形と一致し、元XLSXから解決したpartsの範囲は欠落・重複のない連続矩形に限る。
物理セルの追加IDは検証済み結合親と保存partsの復元だけを許可する。field.sourcePartsにcellId/sourceRangeを保存し、
表示位置や配列順から部品を推定しない。元範囲・内部値保護・ページ境界の検証をDB/config保存前に行い、
不正要求で保存済みlayout/config/planを変更しない。ページ境界・transform・参考画像を保持する。
物理枠追加・任意の移動/変形・ページをまたぐ結合は拒否する。これは2026-09-11の明示要求による
ページ手動編集契約の拡張であり、既存jobのcrop・mapping・provider association契約は変更しない。
ページfields/bindingsとlayout SHAを更新する。過去のqualificationは新版の証明に流用しない。
この表示モデルでは既存のqualificationモデル照合が不成立となり、本番gateは閉じたままとする。
Office adapterのhidden行列等の拒否条件、renderer isolation条件、OCR受付・実行・レビューの
制限は変更しない。追加の自動区分・自動意味づけは実装しない。

2026-09-12の明示要求により、保存ページ上の仮想見出しだけを追加・移動できる。
`virtual=true` の名前付きmanual見出しに `pageRange` を保存し、そのセルの矩形座標だけを
当該保存ページ内の表示位置として解釈する。元セルのcanonical座標とは混同しない。
ページ所属は版内の保存rangeに一致させ、配列indexや近傍セルから推定しない。
仮想見出し自身は元Excelセル、page.fields、field mappings、ラベルOCRの対象に含めない。
既存の物理IDから仮想への置換、仮想を含む結合、ページ不在・範囲外の配置は保存前に拒否する。
ページ範囲・用紙変更や解除で所属・位置が無効になる場合も拒否し、対象見出しの明示的な削除を要求する。
2026-09-21の明示承認により、改ページ保存要求の `pageHeadingMoves` で既存仮想見出しID・
変更前range・変更先rangeを指定した場合だけ、所属変更を改ページと同一transactionで保存できる。
名称・座標・親子接続・意味づけ・mappingは変更せず、ID重複/不在・旧range不一致・
物理セル・移動先不在・ページ外配置・revision競合・公開版への保存を拒否する。
未指定の見出しを推測移動しない。所属変更指定なしの既存拒否とページ解除の拒否を維持する。
新しい印刷成果物・正式資格は改めて取得し、この操作でOCR受付条件を解除しない。
旧セルのJSONへ未指定pageRangeを追加せず、旧layout hash・geometryの暗黙移行を行わない。
見出しの階層・繰り返し群への接続は既存の明示ID経路を使用する。本番gateは解除しない。
これはページ手動編集契約の拡張であり、既存OCR contractを維持する。

新規XLSX取込のlegacyプレビューでは、同じ一時コピーをOfficeで読んだ行高・列幅を
`quality_json.spreadsheetPreviewGeometry`へ保存し、元セル由来の矩形座標に利用する。
このsnapshotがない既存版の復元は従来計算を維持する。snapshotがある版の復元は保存寸法と
原本SHAを照合し、現在のOfficeで再測定しない。セル数・役割・merged cellsを推測変更しない。
2026-09-25以降の新規取込は`spreadsheet-preview-office-v2`を保存し、Officeのpt寸法を
画像pxへ換算した自然倍率を上限として、印刷可能領域に収まらない場合だけ縮小する。
中央配置設定を反映し、小さい帳票を用紙幅まで拡大しない。保存済みv1版は従来座標のまま
復元し、既存版・jobの座標を暗黙に更新しない。
これはprintLayoutのgeometry evidenceやqualificationを代替しない。
検証と制限は[プレビュー座標・ページ編集検証](spreadsheet-preview-geometry.md)を参照する。

reference imageの生成成功はqualificationではない。元XLSX、TemplateVersion、printLayout revision、geometry model / implementation、ページ別reference SHA、field geometry、renderer evidenceの対応を検証する。版・設定・画像・実装・環境の変更や証拠の欠落で有効性を失った結果を再利用しない。

依存ファイルのサイズ・時刻の一致だけで以前のSHAを再利用しない。
同一metadataでも内容が変わる場合を拒否する。[依存SHAの再検証](renderer-identity-freshness.md)を参照する。

Geometry / Reference QualificationとRenderer Environment Qualificationを区別し、両方の有効な証拠を要求する。network / resource isolation、実選択font、runtime依存、rasterizer、crop安全性・本番閾値の未認定条件を、座標精度のPASSで代替しない。hostにfontが存在することはOfficeの実選択fontの証明ではなく、fallback先を推測して認定しない。

2026-09-13の「承認する」により、新paged経路の検証を二段階へ分離する契約変更を承認した。
有効化時は、版/原本/全ページ参照画像のidentityとSHA、描画・座標・明示field/mapping、
定義されたcrop領域とページ境界、隔離環境の有効な証拠を要求する。
まだ存在しない記入画像のink clearanceは有効化の前提にせず、空のreferenceから成功と推定もしない。
実入力のpage binding/SHA、位置合わせ、crop対応と実筆画の安全条件は、受付snapshotの条件で
provider送信前に検証する。未対応・失効・欠落・不成立なら送信を止め、理由と対応するページを保持する。
有効化は各入力の読取成功を保証せず、失敗・再試行時の補正値とimmutable snapshotを維持する。

新しい資格APIの `safetyStages` は段階別の診断であって資格証そのものではない。
記入画像がない時点の `DEFERRED_UNTIL_INPUT` / `NOT_EVALUATED` は成功を意味しない。
診断codesが空でも `READY_FOR_QUALIFICATION` にとどまり、証拠検証・資格発行を迂回しない。
GETでは保存された段階status/booleanを信用せず、所有証拠を確認してから段階情報を再生成する。
実行時enforcementを未実装のままgateを開放しない。旧単一ページと過去jobをこの契約へ暗黙移行しない。

2026-09-14のPhase 3では、`wsl-calc-print-grid-v1` に限る正式なreference/environment
qualificationを追加した。environment判定はlease v2を資格証とはせず、現在のhost/runtime/font/
worker/bootstrap/native dependency identity、prepared graph、非特権・network/host filesystem隔離、
no-new-privileges、read-only Windows font mount、resource limits、effective unit controls、worker/broker
core guardを合わせて評価する。認定scopeは固定prepared-reference invocationであり、host全体の完全な
dependency closure、全dynamic load trace、実入力scanを含めない。このscopeに必要な証拠が欠落・失効・
不一致なら `environmentQualified=false` とする。

同Calc geometry判定は、原本/layout/configからのplan・全binding再計算、全必要field四辺の独立vector
観測、PDF整数座標一致、所有raster/vector証拠、150dpi・整数scaleの数理的表現誤差境界を同時に要求する。
過去実測に合わせた閾値変更はせず、X方向は `0.5 + 150/72/2000` px以下、Y方向はそれに
page-height換算の厳密な上限を加えた値未満とする。文字可視性、実scan registration/crop対応、実筆画
clearanceはこの資格に含めず、provider送信前検証へ残す。

旧 `REFERENCE_ANCHORS_INSUFFICIENT` は診断から削除しない。上記Calc geometry資格が完全に成立した版だけ
activation判定の非blocking診断へ分離する。legacy/不明geometry、edge・binding・plan・coordinate・
raster/vector証拠の欠落または変更では従来どおりblockingとする。

正式recordはTemplateVersion ID/revision、原本SHA、printLayout SHA、geometry model、prepared graph、
renderer/reference/evidence SHA、environment identity、各issuerと全体のimplementation fingerprint、policy、
issuedAt/expiresAt、未解決blockerへ束縛する。GETは現原本・plan・所有artifact・graph・実装・現在環境・
lease freshness・geometry integrityを読取専用で再評価し、補完・延長・DB修復をしない。変更・欠落・期限切れ
では再qualificationを要求する。この資格発行だけではDRAFTをACTIVEにせず、承認成功経路またはOCR受付gateを
開放しない。

2026-09-14のPhase 4では、既存TemplateVersion承認APIに限り、`printLayout`を持つDRAFTを上記の
正式資格へ接続した。paged版は要求時のprint revisionを必須とし、既存のtemplate mutation lockと
`BEGIN IMMEDIATE`の内側でPhase 3のGETと同じvalidatorを実行する。現在の原本/config/plan/bindings、
prepared graph、所有artifactとSHA、issuer/qualification実装、environment identityとlease期限を再検証し、
正式policy、environment/geometry両component、資格証、activation stageがPASSで未解決blockerがない場合だけ
既存の原子的なACTIVE切替へ進む。保存boolean、古いreport、fixture、期限延長を承認入力にしない。
printLayoutのないlegacy版は従来のmapping/document identity、layout verification/readinessを維持する。
paged版では旧`layoutVerified`/`xlsx-structure` booleanを偽装せず、正式paged資格をその代替承認条件とする。
承認は旧ACTIVEのARCHIVED化、対象版のACTIVE化、系列`active_version_id`、元準備jobの確定を1 transactionで
更新し、失敗時は全更新をrollbackする。ACTIVEになっても実入力stageは`NOT_EVALUATED`のままであり、
`PAGE_INPUT_IDENTITY`、`SCAN_REGISTRATION`、`CROP_CORRESPONDENCE`、`INK_CLEARANCE`とpaged OCR受付/provider
送信gateを開放しない。

2026-09-16の実Chrome検証では、Step 5が表示中TemplateVersion自身の正式資格をGETし、PASSかつ未失効で
blockerなしの場合だけ、取得済みcurrent revisionを`base_revision`として上記approve APIへ送信した。
承認成功後は既知の所有Template IDからauthoritative detailを再取得し、対象ACTIVE、旧版ARCHIVED、
`active_version_id`一致、他ACTIVEなしを確認してからUIを更新する。一覧summaryの省略された`versions`、
保存boolean、approve response単独をACTIVE表示の根拠にしない。対象実版のV2 ACTIVE化後もpaged OCR受付は
`PRINT_REFERENCE_NOT_VALIDATED`で閉鎖され、provider送信は発生していない。実証値とfrontend修正は
[GUI Migration Phase 5](verification/gui-migration-phase5-20260916.md)を参照する。

送信前の `CROP_CORRESPONDENCE` は、受付全ページ検証後、当該page/config/attemptと
所有全体画像・参照画像のSHA/保存範囲を再確認して行う。期待region集合・padding込みbboxと、
全5variantの実crop画素を照合する。抽出結果や保存PASS booleanだけでproviderを生成しない。
不一致は送信前に拒否し、ページ別の理由を保持する。検証成功もscan registrationの局所誤差・
実筆画clearance・本番資格の代替ではない。詳細は[送信前crop照合](verification/paged-crop-correspondence-20260913.md)。

`qualified` / `ocrReady` / `productionReady` / `environmentQualified` / `productionQualified`等の保存booleanだけでACTIVE化・production paged OCRを許可しない。認定対象のidentity・evidence SHA・失効条件と未解決条件を再検証する。fixtureのgate置換やmock通過は本番環境の証明ではない。

認定が不成立なら本番gateをfail-closedに維持する。renderer isolation / container設計は別infraタスクとして保留できるが、保留を理由に拒否を緩めない。再開時は実環境で正式なqualificationを行い、§10の変更手順と回帰検証を満たしてから本番開放する。

### 12.7 Renderer隔離とnative処理

2026-09-12に本番化と印刷用PDF取得の明示要求、およびWSL依存導入の許可を受け、
印刷用PDFに限り`PRINT_PDF_RENDERER=wsl`で選択する別rendererを追加した。
固定入力のOffice変換・PDF構造検査をbubblewrapの非特権・ネットワーク分離namespaceへ収容し、
systemd cgroupの資源上限と所有者leaseによる終了を要求する。非隔離fallbackは許可しない。
Windows版のgeometry/font evidenceをLinux版へ流用せず、PDF生成成功で本番OCRのgateを解除しない。
2026-09-13に同じ隔離境界での固定FODS寸法読取と、明示選択時だけWindows system fontsを
固定readonly mountする診断を追加した。fontconfigの解決probeをOffice実描画中の全font/runtime
証拠と同一視しない。新printAdapterの明示版選択だけが原本保持の拡張コピーを利用できる。
旧planのadapter/小数倍率/geometryを自動更新せず、新geometry測定値は旧座標へ流し込まない。
同日の明示確定APIでは、DRAFT/revision/原本/configを照合し、保存済みページ範囲を維持した
新WSL geometryと整数印刷倍率を版へ保存できる。元canonical layoutと過去OCR snapshotは不変。
PDFと全ページPNGを同一の隔離変換から生成し、SHA・寸法・全件数を確認する。
renderer実行中はDB mutation lockを保持せず、生成後の再照合前に版を更新しない。
印刷条件が同じ意味づけ編集では確定ファイルを再生成せず、条件変更時は再確定を要求する。
このAPIの成功やprintFilesの存在はqualificationの代替ではなく、既存ACTIVE/受付gateを解除しない。
実装・現時点の検証・未完了範囲は[複数ページ本番化](production-paged-ocr.md)を参照する。

同日の追加で、WSL runtime imageのSHAを明示した呼出しに限り、root-ownedの読み取り専用
SquashfsからOffice/Python/Popplerと設定を使用できる。内容を保持したdescriptorで前後SHAを
検証し、private mount namespaceと従来cgroup/bubblewrap条件を維持する。
image欠落・変更・mount失敗からlive runtimeへfallbackしない。未指定の旧経路は維持する。
Windows fonts・host bootstrap・kernel等を含む完全な環境認定とは区別し、既存版/過去jobへ
自動適用しない。詳細と残条件は[WSL runtime image](wsl-runtime-image.md)を参照する。

固定runtimeのWindows fonts経路では、readonly font mountの全regular file集合を
変換前後に内容hashし、追加・削除・変更を拒否する。候補fontだけやsize/mtimeだけで
集合identityを代替しない。既存live経路/保存reportへ後付けしない。
この一致は実描画glyph・文字対応や完全な環境資格の代替ではない。
[font集合の検証](verification/wsl-font-directory-20260913.md)を参照する。

`PRINT_WSL_RUNTIME_ID`を明示した新しい印刷確定では、寸法測定とreference生成へ同じIDを渡し、
runtimeImageとfontDirectoryIdentityの完全一致を保存前に検査する。証拠欠落や不一致で
下書き・config snapshot・成果物を更新せず、現在設定を既存版/過去job/保存済み取得へ後付けしない。
これは[印刷確定の環境固定](pinned-print-preparation.md)であり、本番資格やgate解除ではない。

固定runtimeの新呼出しはworkerソースを実行専用snapshotにし、Linux側のprivateな
read-onlyコピーを実行する。隔離側の前後内容SHAと起動側のSHAを照合し、
寸法測定とPDF生成の`workerIdentity`も保存前に一致させる。
欠落/変更を許容せず、旧reportには後付けしない。host bootstrap全体の認定とは区別する。
実装と失敗注入検証は[worker identity](verification/wsl-worker-identity-20260913.md)を参照する。

WSLのOffice/PDF/font workerと入力を保持するbrokerは、入力の読取・解析より前に
process-localなcore dump抑止を要求する。Linux pipe型core handler向けのsoft/hard limit 1と、
CORE limit変更を拒否する検証済みseccomp filterを併用し、getter・他resource操作は維持する。
変更拒否・実効値・filter/library SHAを両processの証拠として新しい変換応答に記録する。
証拠欠落・不一致・未対応ABIでは変換応答を拒否し、非隔離fallbackや全WSLの設定変更はしない。
旧保存reportへ成功証拠を補完しない。worker実装変更で以前の参照資格を失効させる。
これは[クラッシュダンプ境界](verification/wsl-core-dump-boundary-20260914.md)の追加保護であり、
環境全体の資格発行・本番gate解除・geometryやOCRデータ契約の変更ではない。

固定runtimeを明示する新変換では、root bootstrapのPython検索treeを変換前後で内容hashし、
実行ファイル・検索ZIPの有無・stdlib全entry・file linkの解決先を含む一致を要求する。
isolated/no-site/no-bytecodeの実値、root所有と非書込条件、件数/bytes上限、読取中の変更を検査する。
未知の検索経路、installation外へのlink、未対応directory link、欠落したlink先は拒否する。
Office/glyph/vectorの新応答に `bootstrapPythonIdentity` を保存し、寸法測定・参照画像生成・
再描画の間でも一致を要求する。旧reportには後付けせず、getterで現在の環境を成功として補完しない。
この境界は[Python起動入力の前後照合](verification/wsl-bootstrap-python-enforcement-20260914.md)であり、
さらにv2では、固定のhost起動ツール・Python拡張・libseccompのELF起動時依存を
`nativeDependencies` として同じ前後照合・保存・再描画比較に含める。
固定loaderの `--list` はroot所有のinstallation内ELFだけに使用し、帳票を渡さない。
解決不能/未知の出力、loader環境変数、preload file、所有権/権限違反、集合/内容変更は拒否する。
内容hashと依存graphに結び付く[ELF起動依存の照合](verification/wsl-native-bootstrap-enforcement-20260914.md)であり、
動的dlopenの完全trace・systemd設定・Windows/WSLの完全な環境認定を代替しない。

新しいWSL資格検証では、bootstrap証拠を持つ保存版について、帳票を渡さない独立した
環境照会で選択runtimeの実体SHA・全font集合・worker・Python/ELF起動依存・broker core guardを
再取得し、保存された参照画像の実行条件との完全一致を要求する。
この一致の証拠は版・所有artifact graphに結び付け、24時間の上限付きで別のJobArtifactへ保存する。
GETでは所有者・SHA・版・期限を先に検査し、その後に現在の実体を再読取する。
期限切れ・未来時刻・graph変更・現物不一致を、保存booleanや期限延長で通さない。
旧reportに新証拠を後付けせず、GETでDBを更新しない。
これは [環境identityの期限付き照合](verification/wsl-environment-identity-lease-20260914.md) であり、
scopeは `identity-freshness-only`。完全な環境認定証ではなく、これだけでACTIVE/送信を許可しない。

新規lease v2はWindows build/UBR/DisplayVersion、architecture、WSL launcher実体SHAと
version応答の生bytes SHAも記録し、Linux照会前後・GET再検査で完全一致を要求する。
旧leaseへのhost情報の補完はしない。これは資格記録取得時のhost更新検出であり、
元の帳票変換時のhost状態やOS依存全体を遡って認定しない。

保存済みWSL実行報告の読取では、installation identityの一致だけで隔離条件を認めない。
非特権UID、loopbackのみのinterface、通信拒否、no-new-privileges、host領域非公開、
固定したmemory/swap/pids/CPU上限、Windows font mountのreadonlyを型も含めて照合する。
新しいbootstrap証拠を持つreport、または隔離観測を一部でも持つreportでは全項目を要求する。
寸法測定と参照画像の片側だけから観測を削除して旧形式扱いに戻すことも拒否する。
観測のない旧reportは補完せず、現在の環境leaseを発行しない。
固定runtimeの新変換応答、保存graph、再描画比較、環境lease保存/読取へ同じ検査を適用する。
これは[実行時隔離証拠の検証](verification/wsl-execution-evidence-20260914.md)であり、
環境全体の資格発行・native動作全体の認定・ACTIVE/受付の成功経路ではない。

新しいCalcモデルの明示印刷確定では、同じread-only UNO documentから列幅・行高と
単一セルrange Sizeを観測し、そのdocumentをFODSへ出力する。観測は出力前後で一致させ、
要求したシート/行列prefix・原本SHA・FODS SHA・固定runtime/worker/fontに束縛する。
FODSだけでは判別不能なuint16桁あふれを、独立したwide寸法と累積座標の整数丸めで拒否する。
UNO欠落からCLI再読込へfallbackしない。要求prefixは行列合計20000以内とし、固定I/O・
network/resource/lease隔離を広げない。旧下書きの閲覧に新証拠を後付けせず、Calcの参照資格
検証には独立観測を要求する。これは寸法照合であり、本番許容値・環境資格・crop/inkの認定や
gate解除ではない。[同一読込の寸法観測](verification/uno-same-import-20260914.md)を参照する。

固定runtimeの新しいOffice/glyph/vector変換では、所有unitの時間・終了・file-size等の
実効設定をbrokerで確認するまで、子は入力解析を待つ。成功記録をroot-owned directoryへ
atomicに公開し、子はreadonly mount・file不変・実RLIMIT_FSIZEを検査する。
欠落/不一致/起動失敗/時間切れは解析前に停止し、所有者切断時の既存停止境界も維持する。
保存/bridge/再描画/leaseのunitControlsは完全な検証を要求し、旧reportへ成功を補完しない。
これは[解析前の実効設定検査](verification/wsl-unit-admission-20260914.md)であり、
環境全体の資格発行・geometry認定・ACTIVE/受付の成功経路を代替しない。

WSL印刷確定版の参照検証は、その版に保存した原本・印刷XLSX/PDF・測定report・全ページ画像の
artifact所有者と内容SHAを照合する。Windows版Officeで再生成して代替しない。
検証実装/版/成果物の変更で失効し、保存前の再照合と既存geometry/crop条件を維持する。
これは[保存成果物の参照検証](prepared-reference-qualification.md)としてPhase 2証拠を構成し、
Phase 3の正式issuerはこの所有証拠を現在条件で再評価する。保存booleanや成果物一致だけで
environment資格へ昇格させず、資格が成立してもACTIVE/受付gateを解除しない。

固定環境を記録したWSL版の明示参照検証では、保存印刷XLSXを同じruntime IDで再描画し、
入力/環境条件と全ページの順序・寸法・画像bytes/SHAを照合する。
比較用画像は同じsource jobの別artifactとし、元印刷成果物/意味づけを置換しない。
GETでの証拠再検査にOffice実行を含めず、保存済み一致booleanだけでは通過させない。
詳細は[再描画比較](prepared-reference-replay.md)。これは環境/字形/crop資格の代替ではない。

固定環境を持つ WSL 版の明示参照検証は、所有された保存 PDF を専用 glyph helper に渡せる。
PDF/font parse は固定依存・read-only code/input/font 集合・network/cgroup/lease 境界内に置く。
全使用文字 code の Unicode・source/embedded glyph・metrics/hints を照合する。
結果を版・印刷成果物 graph と同じ source job の別 artifact に束縛し、GET で所有者・内容 SHA・
入力・実装 identity を再検査する。表示 boolean だけで未検証条件を解除しない。
この一致で除けるのは glyph の未検証条件だけで、宣言 face と異なる実 face には fallback の
未認定条件を残す。環境全体・文字位置・可視性・crop・手書き OCR の安全性へ流用しない。
既存版/原本/印刷 PDF の暗黙置換、旧 report の補完、本番 gate の解除は行わない。
詳細は[字形検証](renderer-glyph-validation.md)を参照する。

固定環境を持つWSL版では、各fieldの四辺と最終PNGの観測を版所有artifactとして追加保存できる。
不足する罫線の測定コピーは元印刷ファイルから再生成可能な最小変更に限定し、文字プログラム・
font resource・用紙・環境の一致、測定線近傍以外の画素不変を要求する。原本・印刷成果物・
保存座標やmappingを置換しない。GETでは所有者/内容/実装/入力graphを再検証して計算し直す。
元のanchor診断を削除せず、座標観測 `MEASURED` や暗い画素の存在だけで可視性・本番の許容値・
環境を認定しない。詳細は[項目別四辺の保存証拠](verification/prepared-field-geometry-20260913.md)。

細い破線の観測には、隔離内で純粋なstroke命令1個だけを抑止したPDF測定コピーと、
全ページRGB差分を追加できる。元のdecoded contentは当該1 byte以外保持し、用紙・数値・
文字/font resourceを照合する。元PNGとの画素identityと対象外差分を再検査し、GETでも
保存summaryではなく所有されたhelper証拠から再計算する。既存raster診断は消さない。
subpixelのpaint componentと実線交点候補は別に記録し、交点候補だけで成功としない。
破線片に描画寄与がなく、他の実線の独立したink/contrast観測でも裏付けられない場合は
未検証を維持する。これは[描画寄与の観測](verification/stroke-differential-20260913.md)であり、
資格発行・本番許容値・cropや文字の可視性の認定ではない。

同じ線が複数欄を横断する場合は、明示field/bindingsと辺の範囲に限った
subpixel描画寄与を追加検査できる。欄外componentを当該欄の要件に含めないが、
既存端点guard・画素/contrast閾値は広げず、欄内に未観測componentがあれば拒否を維持する。
実測したink欠落や差分証拠の欠落・不完全・ゼロを救済しない。元の全線診断を保存し、
GETで所有証拠から再計算する。これは[欄別観測](verification/field-raster-scope-20260913.md)であり、
独立したgeometry誤差・可視性・環境・本番資格を証明しない。

独立した単一segmentのsubpixel実線では、同じ固定helperの校正PDF生RGBを再検証した場合に限り、
Splashのstroke adjustmentから予測した画素位置を別の補助観測として使用できる。
校正の入力/code/runtime identityと全256ケースを要求し、元の全線観測・geometry誤差は保持する。
画素探索・端点guard/閾値の拡大・座標の変更はせず、予測画素にもinkがなければ未検証を維持する。
校正なし・不一致、破線・複合path・curve・clip・shear等を推測で救済しない。
保存/GETでは所有helper証拠の生RGBから再計算し、保存summaryを成功根拠にしない。
これは[細線の丸め位置の観測](verification/splash-stroke-quantization-20260913.md)であり、
環境資格・本番許容値・cropや文字可視性・ACTIVE/受付gateの認定を代替しない。

保存済みCalcモデルの参照検証では、原本に束縛された寸法・ページ/field範囲から整数グリッドを
再計算し、固定した整数point PDF出力と観測四辺の数値同一性を追加照合する。
保存rectangleの丸め不一致・min=1拡張・未観測/曖昧な辺・未対応座標系を成功扱いにしない。
保存/GETとも所有vector/rasterから再計算し、計算/照合実装のSHA変更で旧資格を失効させる。
旧geometry・座標・意味づけを移行せず、既存診断の削除や画像fit、許容値拡大を行わない。
これは[整数PDF座標との対応](verification/calc-pdf-coordinate-precision-20260914.md)であり、
数値一致だけで本番許容値・可視性・環境・crop・ACTIVE/受付を認定しない。

AppContainerの通信拒否probeが通っても、実Officeの変換・IPC・filesystem・lifecycleが同じ隔離条件で成立する証明にはならない。Job Objectの資源制限とnetwork isolationも同一視しない。隔離失敗時に無制限processや広い権限へfallbackしない。

Office互換性のためのbinary patch・hook・独自再ビルドは通常修正として行わない。必要性、隔離への影響、配備・再認定方法を別設計として提示し、明示的な変更範囲とする。

未信頼PDFのnative parse / raster失敗をbackend本体へ波及させない。新reference rendererのPDFium helper process境界を維持し、無条件にbackend内native処理へ戻さない。helper自身のnetwork・資源・入出力・依存関係も認定対象とする。timeout・cancel・親終了時は所有processと一時領域だけを回収し、他jobやユーザーのOffice processを終了しない。

### 12.8 Geometry誤差とcrop安全性

§12.6の承認済み二段階契約では、以下の実筆画・scan registrationの検査は実入力を得た
OCR実行時の責務である。テンプレート側のcrop領域定義・対応・参照geometryの検証は有効化前に残る。
検査の時期を分けても、clearance証拠の欠落をPASSに変えてはいけない。
自動的に筆画安全性まで認定する経路では、その証拠なしにproviderへ送信してはいけない。
人間による全ページ確認を前提とする候補OCRの送信条件は、後続の§12.9で明示的に分離する。

geometry PASSはcrop safety PASSではない。field寸法、罫線・筆画、内容と境界・隣接内容とのclearance、paddingを区別して評価する。独立したclearance証拠がなければ未検証とし、空の基準画像や人工fixtureだけから実手書きの安全性を推定しない。padding増加や閾値緩和でgeometry誤差を隠さない。

`logical geometry → reference image`の誤差予算と、`reference image → scan registration`の誤差予算を分離する。前者のずれを既存X/Y画像合わせで吸収して認定せず、後者を未測定のまま実OCR精度が保証されたと扱わない。DPI間は物理単位とraster誤差を区別し、数値の根拠・対象条件はPhase evidenceに保存する。

### 12.9 人間レビューを必須とする候補OCR受付 — 2026-09-21

複数ページから文字候補を生成し人間が確認する製品ゴール、および最短開放案への
「実装開始して」の承認に基づく、§10の意図的な新paged受付契約の変更である。
旧単一ページ・既存受付snapshot・認識器・位置合わせ閾値・矩形・mappingを変更しない。
過去のPhase報告にある「受付は常時閉鎖」は当時の状態として保持する。

- catalogとacceptは同じvalidatorでACTIVE、現layout SHA、全ページの対応項目、
  現在の正式reference/environment/geometry資格を要求する。acceptはmutation lockと
  transaction内で再検証する。保存booleanや旧`layoutVerified`で資格を代用しない。
- 原本・plan・所有artifact・prepared graph・実装・現環境・lease期限は既存正式資格validatorで
  再検証する。失効・不一致・DRAFT・ARCHIVEDは拒否し、資格を自動更新しない。
- 新受付bundleには`paged-reviewed-candidates-v1`、`reviewRequired=true`、
  `inkClearanceVerified=false`と受付時の資格binding/期限/report SHAを保存する。
  過去jobへ新しい資格・policyを後付けしない。retryは既存immutable snapshotを使う。
- `PAGE_INPUT_IDENTITY`、実scan registrationとその失敗拒否、provider生成前の
  `CROP_CORRESPONDENCE`、association、文書全体API予算は引き続き必須である。
  対応不明・位置合わせ失敗・crop改変を人間レビュー予定という理由で通さない。
- **変更する条件は未実装の全筆画clearance自動認定を候補OCRの前提にしない点**である。
  `INK_CLEARANCE`の未検証診断や正式資格の実入力`NOT_EVALUATED`は削除・PASS化しない。
  資格のstage記述は全入力安全性の認定要件として維持し、このcandidate policyは
  その完全認定ではない。空reference・fixture・crop画素一致を筆画完全性の証拠にしない。
- 入力画面と専用レビューに未認定範囲を明示する。結果は未確定候補として公開し、
  各ページの切り出し画像・値を確認/必要な補正後、全ページの明示確認とrevision付き確定を
  要求する。未確定のXLSX出力、自動確定、旧単一結果へのPage 1転用を追加しない。

変更前後の契約テスト、正式資格失効/改変/版状態の拒否、snapshot・crop・review/confirm/outputの
回帰検証を行い、fixture通過と実OCR品質は分けて報告する。

### 12.10 環境更新後の候補OCR準備確認 — 2026-09-21

正式資格の完全再発行と、人間レビュー前提の機能開放を分離する承認に基づく、§10の
意図的な有効化/受付条件の追加である。§12.9の正式資格経路は保持し、以下の別経路を追加する。
過去の正式資格をPASSへ書き換えたり、環境hashの比較を無条件に省略したりしない。

- `paged-candidate-readiness-v1`は、固定runtimeと所有印刷用XLSXの明示的な再描画を要求する。
  旧描画との差分を許すのは`bootstrapPythonIdentity.nativeDependencies`だけである。
  両実行の証拠を検証し、runtime・font・worker・Python tree・隔離/資源制限・core guard等の
  残りの実行identityは完全一致を要求する。無制限実行へのfallbackはない。
- 新しい全ページPNGのSHA・サイズ・順序が保存済み基準画像と一致することを要求する。
  原本/config/layout/print plan/prepared graphと各所有artifactを再検証し、保存済みの
  glyph/geometry測定証拠を現実装で再計算する。過去のPASS booleanを根拠にしない。
  座標誤差・画素差の許容値を広げず、異なる基準画像・mappingを自動採用しない。
- 別の所有artifactに新描画証拠、許容した環境差分、現host、実装fingerprint、版/revision、
  発行/期限を保持する。期限は24時間。GET/有効化transaction/受付transactionでは
  現在の環境・原本・artifact・版・実装・期限を再照合するが、Office再描画は行わない。
  環境観測自体のコストは残る。検証中の変更・期限切れは拒否する。
- 結果は`CANDIDATE_READY`、`qualified=false`、`qualificationIssued=false`、
  `reviewRequired=true`であり、正式認定とは区別する。保存booleanだけでは利用を許可しない。
  有効化/受付は同じvalidatorを使い、受付時にcandidate証拠のbindingをsnapshotへ固定する。
- 準備確認の発行はDRAFT/revision照合下で行う。保存layout、draft内容、原本、print artifact、
  mapping identity、geometry、旧正式資格証を変更しない。版fork時はcandidate参照を引き継がない。
  新しい正式資格の発行に成功した場合のみcandidate参照を外し、正式資格を正本に戻す。
- ACTIVEの期限切れ証拠を自動更新する機能は追加していない。既存の編集用DRAFTからの
  準備/検証/承認手順を用いる。旧ACTIVEの不変性を回避して更新しない。
- 証拠不足、native以外の環境差、異なる描画は引き続き停止する。初回のgeometry/glyph検証を
  省略する経路ではない。正式資格経路・既存診断は削除しない。

実入力のpage identity、registration、crop correspondence、association、API予算、補正revision、
全ページの明示確認と未確定出力拒否は§12.9どおり維持する。文字認識精度や筆画完全性は保証しない。
実装と実環境の検証は[候補OCR準備確認](verification/candidate-readiness-20260921.md)を参照する。

### 12.11 候補準備と通常利用の検証分離 — 2026-09-21

§1.2の通常運用コスト方針と、候補準備後の画面表示・有効化・catalog・OCR受付が
geometry/glyph再計算とWSL環境照会を繰り返していた実装の再発防止に基づく、§10の意図的な
候補利用契約変更である。明示的な候補準備では§12.10の再描画、全ページ画像一致、
geometry/glyph再計算、現在host/runtime確認を引き続きすべて実行する。

- `paged-candidate-readiness-v2`は、準備時に完全検証した版・原本・print plan・実装・
  prepared graph・renderer report・replay・geometry/glyph証拠の参照を、版所有の候補artifactへ固定する。
- 通常の資格状態GET、有効化、catalog、受付では、版/revision、layout・plan、config・実装fingerprint、
  候補artifact本体、原本XLSX、renderer reportの内容SHA、全参照artifactの所有者・登録SHAを照合する。
  geometry/glyph証拠の再計算、replay画像の全再読込、WSL/hostの現在照会、Office再描画は行わない。
- 候補の利用期限は固定24時間ではなく、対象版・原本・layout・print plan・config・対象実装・
  候補artifact・証拠参照のいずれかが変わるまでとする。準備時の24時間値は環境観測の記録として
  保持するが、保存済み基準画像を使う通常OCRの期限には流用しない。
- 既存`paged-candidate-readiness-v1`はDBやartifactを書き換えず、旧発行実装fingerprintと
  保存済みreplay/差分記録を検証してv2利用結果として読む。旧証拠へ新しいPASSを後付けしない。
- OCR受付transactionでは、実際にsnapshotへ複製する各基準ページPNGの内容SHAを引き続き再確認する。
  受付後の実入力identity・registration・crop correspondence・association、全ページレビュー、
  revision付き補正、未確定出力拒否は変更しない。
- 準備後にrenderer/WSL環境だけが変わっても、固定済み基準画像・mapping・crop定義は変更されないため、
  通常利用を再準備へ戻さない。新しい印刷準備や正式資格発行で現環境を使用する場合は、
  その操作の完全検証を改めて要求する。

候補artifact・原本・renderer report・版bindingの改変は通常利用でも拒否する。
軽量化を理由にreference PNGの受付時SHA確認や実入力検査を省略してはならない。

### 12.12 新規テンプレートの通常OCR準備 — 2026-09-22

「通常利用者に資格審査を操作させず、GUIでページ範囲を指定し、OCR結果を確認・修正する」
という要求と実装承認に基づく、§10の意図的な有効化/受付契約の変更である。
§12.9〜§12.11の正式資格・旧候補経路と測定記録は保持するが、通常OCRの初回準備には要求しない。

- `paged-candidate-readiness-v3`を追加する。保存済みの所有原本、config、layout、print plan、
  全ページのreference画像と描画レポート、mappingのページ対応を既存validatorで確認する。
  DRAFT・revision照合・mutation lockを守り、版所有の準備artifactだけを追加する。
  geometry/glyphの測定証拠、過去の正式資格、Office再描画による同一性証明、現WSLの再照会は
  この準備の前提ではない。既存描画レポートの実行条件・隔離証拠の整合性は検査する。
- 戻り値は`CANDIDATE_READY`、`reviewRequired=true`、`pageBindingsVerified=true`、
  `qualified=false`、`qualificationIssued=false`、`geometryQualified=false`。
  対応関係の検証と印刷精度の正式認定を混同しない。未測定値をPASSへ書き換えない。
- 新policyと`prepared-pages-human-review-v1` markerをfingerprintへ固定し、原本、config、
  版/revision、保存planと所有artifact参照、準備artifactの変更で失効する。
  通常利用はv2同様に内容SHA/所有bindingを検査し、固定時間による再審査は要求しない。
  v1/v2の保存artifactをv3へ読み替えない。v3の検証意味を変更する際はpolicy/markerを更新する。
- 各ページに対応するenabled mappingが存在し、全mappingが重複なくページへ所属することを
  有効化前に既存`page_config`でも検証する。未対応の複合/mark設定、項目のないページを
  勝手に変換・削除して通さない。
- 通常UIは「このテンプレートでOCRを使う」の一操作で、必要なら既存の印刷見本生成、
  v3準備、既存approve、サーバーのACTIVE再取得を順番に行う。生成済み見本は再利用する。
  新たな描画には従来の隔離・資源制限を適用する。Officeなしの代用描画へfallbackしない。
  未保存状態では開始しない。失敗は現在画面に表示し、競合時の承認を自動再送しない。
- 印刷範囲は既存の区切り線ドラッグとページプレビューで指定する。mm入力は任意の詳細設定。
  見出しの所属変更はページ候補から明示選択し、IDや接続を推測で付け替えない。
  画像選択画面はcatalogの受付可否を正本にし、独立した正式資格GETで別画面へ戻さない。
- legacy単一ページ、実入力identity/registration/crop correspondence、association、
  API予算、受付snapshot、補正revision、全ページの明示確認、未確定出力拒否は変更しない。
  正式なgeometry/glyph認定を省く結果、印刷と基準矩形の一致や文字・筆画完全性は保証しない。
  利用者が切り出し画像と文字を確認し、必要な補正後に確定する製品ゴールを維持する。

今回の検証は明示要求に従い対象テスト・build・実画面確認に限定する。
全体テスト、実WSL再描画、実OCR品質評価は別であり、fixture通過をその証明にしない。
詳細は[通常OCR準備の検証](verification/reviewed-ocr-entry-20260922.md)を参照する。

## 13. チェックリスト（2026-10-04）

ユーザーの明示依頼により `markRecognitionMode=checklist` を追加した。紙のペン記入、印刷/セル文字のチェック記号、Excelセル内チェックボックスを対象とする。従来の automatic/manual/symbol の判定方法は変更しない。

- checklist は `mappingType=mark` / `markSelectionMode=multiple`。候補は1〜50件、同名不可。候補名に選択値の区切り文字 `・、,;` / 改行を含めない。
- 設定した欄全体のalignedColor cropを送信し、チェックされた候補ラベルを候補順に `・` で連結する。未選択は `blank` / null、曖昧・候補外・対応不明は `uncertain`。空の読取可能応答を未選択へ変換しない。
- プロンプトは checklist の画像だけ、手書きと印刷のチェック印を対象とし、選択値を `v` に返す。ASSOC、compact ID、返却対応検査は従来どおり。
- 補正は既存revision経路で保存する。確定前に受付snapshotの候補と照合し、判別不能・未知の候補・重複選択を拒否する。checkboxをfalseへ落として確定しない。
- ページ別実行は、従来のtextに加えchecklistだけを許可する。page field bindingから欄全体のrectangleを取得する。選択肢ID/値を保持し、選択肢rectangleは欄全体へ設定する。これは画像差分検出用の範囲ではない。その他のmark・複合項目の未対応制限は維持する。
- XLSX直接入力はACTIVEな単一ページSPREADSHEET_TEMPLATEに限定する。同じsheet/行列寸法/結合/記入欄以外の値を検査し、受付対象のセルだけを描画用コピーへ転記する。原入力xlsxとSHA、セル内checkbox数を記録する。原本を書き換えない。
- Excelのセル内checkboxは、OOXMLのFeaturePropertyBag→XFComplement→XFControls→Checkboxとboolean cellの対応を検証する。候補が1つのchecklist欄だけに割り当てる。書式のないTRUE/FALSEをcheckboxと推定しない。
- セル内checkbox原本の出力では、確定値をbooleanへ変換し、所有するsheetのcell XMLだけを変更する。他のZIP partと原sheet名を保持する。手動結合との併用は拒否する。通常の文字チェックリストは既存XLSX出力を使用する。
- 旧フォーム/ActiveX、外部リンク、数式を含む直接入力、paged XLSX直接入力は未対応として停止する。画像にしたチェック欄は通常の画像OCRで確認できる。paged rendererの資格・入力検査を緩和しない。

検証の範囲と残る制約は [チェックリストOCR](checklist-ocr.md) を参照する。実筆記・実撮影画像の精度は、合成検証画像やExcel描画画像の結果では証明しない。

## 14. テンプレートの線接続操作（2026-10-05・範囲訂正）

ユーザーの追加指示により、接続方法を置換した前回変更を撤回し、線で結ぶ操作の改善に限定した。

- 項目名から記入欄へドラッグし、離したときに反映する。複数欄の選び方は従来の `meaningLinkLaneCells` を使用する。選択一覧や追加の接続確認ボタンを要求しない。
- 描画時と確定時は同じ `meaningDragSelection` を使う。終点セルの中心への補正、役割による絞り込み、方向・経路の対象判定を共有し、強調表示と実際の対象が食い違わないようにする。
- 接続線は丸い端と白い縁取り、視認できる一定の表示太さにする。ドラッグ中は接続先の枠と件数を表示する。これらは一時UI状態であり、セル座標・crop・値・保存先を変更しない。
- 2026-10-05追加最適化: 項目名のhover時に接続点とgrabカーソルを表示する。接続中は原画像の文字を見やすくするため認識文字の重ね描きを抑え、仮想見出しは表示する。保存した文字は変更しない。
- セル内のヒットを優先する。セル外では項目名/記入欄の矩形までの距離を表示pxで測り、8px以内を吸着候補にする。最短2候補の距離差が2px未満なら対象を決めず、利用者に欄の中へ移動するよう示す。多角形の外側への吸着は行わない。吸着はポインターの解釈だけに適用し、セルの座標・形状や既存の接続を変更しない。
- ドラッグ中の表示更新と端から32px以内の自動スクロールはrequestAnimationFrameで処理する。スクロール後のcanvas座標から候補を再計算する。マウスを離す・Esc・pointer cancel・ウィンドウ非アクティブ化で停止し、4px未満のクリック移動は接続変更を行わない。
- 保存先の推定・重複回避、relation作成・並び順、既存保存APIは従来の経路を使用する。前回追加したID維持方式への置換も撤回した。利用者の接続操作によるrelation ID更新は従来の仕様であり、OCRのmappingIdと混同しない。
- Esc、pointer cancel、工程・ページ・ツール変更で未確定の線を破棄する。取り消しは直前のrelationと親関連に加え、recordStructureVerified / allowCrossAxisLabelsを元の値へ戻す。確認状態を再計算して変更しない。
- 既存テンプレートや受付snapshotの一括変換は不要。provider・prompt・OCR実行・補正revision・XLSX出力は変更しない。

検証範囲は [テンプレート作成GUIの変更と検証](template-authoring-ux-2026-10-05.md) を参照する。前回の選択パネル方式の記録は製品外に退避し、現行仕様として扱わない。
