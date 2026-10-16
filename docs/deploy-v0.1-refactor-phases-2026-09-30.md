# v0.1 デプロイ前改修フェーズ実行指示書

作成日: 2026-09-30

対象: C:\Users\hanam\Desktop\llm-ocr-deploy
状態: 2026-10-01、デプロイ用コピーで選択された改修のPhase 0〜7を実行済み。達成状況、既存テストの呼出し修正、検証限界は [実行記録](deploy-v0.1-execution-2026-09-30.md) を参照。Phase 1の運用条件は設計メモを作成した段階であり、採用決定・本番配備は未実施。

## 1. 実行条件

- 使用モデルは **GPT-6.1 Sol、Reasoning effort: Max** とする。フェーズ間でモデルを切り替えない。
- 作業場所はデプロイ用コピーだけとする。元の C:\Users\hanam\Desktop\llm-ocr は編集しない。
- コピー側には作業開始前から追跡ファイルの変更・未追跡ファイルがある。作業開始時に git status と差分を記録し、既存変更を今回の改修と混同しない。reset、revert、checkout、staging、commit、pushで既存状態を変更しない。
- 本書の作成段階ではコード、テスト、設定、依存関係、DBを変更・実行していない。
- チェックリストで「要る」とされた項目を対象とし、「要らない」は追加しない。「保留」は決定済みと扱わない。
- 既存画面や作業ツリーがレビュー時から変わっている可能性がある。各指摘は現コピーで再現・確認してから修正する。既に解消している場合は二重修正しない。

## 2. v0.1の完成条件と変更境界

v0.1は帳票管理システムではなく、Excel帳票OCR変換ツールとして扱う。完成条件は次の基本フローを対応範囲内で完了できること。

1. .xlsxをアップロードする。
2. Excel帳票からテンプレートを作る。
3. テンプレートを選択し、帳票画像を読み取る。
4. OCR候補を確認・修正する。
5. 確定した値をExcelへ反映する。
6. 処理済み.xlsxをダウンロードする。

対象外とする機能:

- Google Drive / Google Sheetsへの入出力・同期、OneDrive等の外部ストレージ連携
- OCR結果の履歴管理・横断検索、OpenSearch、OCR結果保存専用RDS、管理ダッシュボード
- カメラ撮影、ホーム画面の最近の処理履歴・稼働状況表示、OpenAI画像送信の新規事前通知
- あらゆるExcelレイアウト・レンダラー間差異への完全対応
- 要求されていないOCR精度改善、mapping一般化、provider・prompt・model・schema変更

外部ストレージ連携をv0.1で提供しないことと、クラウド上の利用者認証をどうするかは別の判断である。Google Drive OAuthを外すことだけを理由に、認証を不要と決めない。ジョブ進行中の状態表示・復旧は、履歴ダッシュボードの対象外と混同せず既存job lifecycleに従う。

## 3. 保護する不変条件

特にOCR結果サービスを分割するときは、docs/ocr-safety-contract.mdを正本として参照する。以下をUI整理や依存分割の名目で変更しない。

- physical cell、mappingId、targetPath、sourceRegionId、boundingBox、cropの対応
- 受付時snapshot、mappedCells、reviewStructure、field ownership、provider association
- raw/current/normalized、status、reviewReason、reviewItems、補正revision、確定snapshot
- result.confirmedによるローカル確定と、Google同期等の外部反映状態の分離
- 失敗・再取得・再マウント・revision競合時のユーザー補正値の保護
- 受付時に固定した出力binding、元XLSXの保持、確定前の出力拒否
- paged OCRとlegacy single-page OCRの経路分離、ページidentity、snapshot、retryの意味

既存OCR契約の意図的な変更は本計画の範囲外。新たな不変条件違反を見つけた場合は、再現・原因・影響を記録してそのフェーズを止める。別のOCR改善として勝手に範囲を広げない。

## 4. フェーズ一覧

| Phase | 内容 | 完了の目安 |
| --- | --- | --- |
| 0 | 現コピーのbaselineと再現確認 | 既存差分と対象症状を区別し、受け入れ条件を固定 |
| 1 | v0.1クラウド運用の設計ゲート | 未決事項を一覧化。コード・DB・infraは変更しない |
| 2 | 画面操作上のデータ保護と有効化後の状態確認 | ナビゲーション、承認結果、プレビューdialogの受け入れ条件を満たす |
| 3 | テンプレート見本画像の取得最適化 | 計測で不要な一斉取得を確認し、遅延取得・cache・同時数制限を実装 |
| 4 | LayoutEditorの段階的分割 | 既存helperを再利用し、純粋関数から順に責務を移す |
| 5 | OcrPageの段階的分割 | 画面機能単位で分離し、受付payloadと状態遷移を維持 |
| 6 | OcrResultServiceの段階的分割 | 既存API・transaction・OCR/出力契約を維持 |
| 7 | 影響範囲に限定した最終検証と整理 | 必須検証、差分確認、テスト資産の整理基準を満たす |

Phase 1の利用者間データ分離・保存方針が未決のままでも、独立したUI修正は進められる。ただし、保存先、tenant境界、保持期間を前提にするbackendやdeployment設定の変更は保留する。

## 5. Phase 0 — baseline、対象確認、受け入れ条件

1. デプロイ用コピーで git status --short、git diff --stat、対象ファイルの差分を読み、開始時点の状態を記録する。既存の変更を今回の作業に取り込んだり、上書きしたりしない。
2. 次の対象コード・テストを読み、チェックリストの症状が現在もあるか確認する。
   - frontend/src/App.tsx、frontend/src/pages/OcrPage.tsx、frontend/src/pages/TemplatePage.tsx
   - frontend/src/App.test.tsx、frontend/src/pages/OcrPage.test.tsx、frontend/src/pages/TemplatePage.test.tsx
   - frontend/src/components/LayoutEditor.tsx、frontend/src/components/layoutAuthoring.ts、frontend/src/components/headingConnections.ts、frontend/src/components/LayoutEditor.test.tsx
   - backend/app/services/ocr_result_service.pyと、既存のreview / output関連テスト
3. 既存テスト・mock・保存responseで再現できる症状は、その入力・状態・期待結果を短く記録する。記録上の古い不具合だけを根拠に変更を始めない。
4. 選択された各変更について、変更前の画面挙動、request回数、保存値または既存関数出力を必要な範囲で記録する。計測対象が遅いと確認できなければ、性能改善コードを追加しない。
5. 各phaseの変更前後で同じ条件を比べられるよう、既存の代表テストと検証手順を選ぶ。baseline目的の全テスト再実行はしない。

**停止条件:** 指摘が再現しない、作業ツリーの既存変更と修正箇所が競合する、または受け入れ条件を既存OCR契約と両立できない場合は、その項目を変更せず原因と判断材料を報告する。

## 6. Phase 1 — クラウド運用の設計ゲート

チェックリストはクラウド運用の設計を「要る」としているが、次の決定は未完了である。v0.1の設計メモまたはADRに選択肢・推奨案・保留理由を整理する。本文書作成時点では決定しない。

- デプロイ先と運用主体
- v0.1の利用者認証と、ユーザーごとのデータ境界
- テンプレートの永続保存先、共有単位、版管理
- アップロードしたExcel・画像・一時ファイルの保持期間と削除時点
- OCR完了後に残す情報。v0.1はOCR業務データの恒久保存を必須にせず、出力後は原則破棄する方針
- jobの途中状態・失敗状態を復旧するために必要な最小保存期間

SQLiteを共有DBとして本番採用するか、別DB・job実行基盤・object storageを導入するかは、上記を決めるまで未確定とする。OpenSearch、検索用RDS、履歴機能は追加しない。設計メモができても、認証・tenant isolation・retentionの選択を暗黙に確定したことにはしない。

## 7. Phase 2 — 画面操作上の不具合

### 7.1 入力中・送信中の画面移動

対象はApp.tsxのnavigation guardとOcrPage.tsxの選択・送信状態の接続。単にOCR画面のstepを進めただけの状態は、実際に入力を失う状態と区別する。

受け入れ条件:

- ファイル選択後、または送信開始後に別画面・読み取りrootへ移動するとき、破棄確認を表示する。
- 「この画面に残る」を選ぶと、選択ファイル、選択テンプレート、現在step、処理中jobの状態が保持される。
- 「破棄して移動」を選んだ場合だけ、画面の一時入力を破棄する。jobを暗黙にcancelせず、既存job監視・復帰経路を維持する。
- 画像未選択など入力損失がない状態では不要な確認を出さない。
- OCR結果、mapping、サーバー業務状態をnavigation用一時stateで代替しない。

既存のApp.test.tsxのunconfirmed review警告と、OcrPage.test.tsxのreading setup guardケースを先に確認する。既存テストが既に期待挙動を覆うなら重複を足さない。

### 7.2 テンプレート有効化後の状態確認

対象はTemplatePage.tsxのapproveVersion/onApproved経路と、親側で系列・versionを再取得する処理。

- 承認POSTが成功した事実と、一覧再取得が成功した事実を混同しない。
- 状態表示は承認後の権威あるAPI応答から対象versionのACTIVE状態を確かめて更新する。古いprops/stateのtemplates配列だけを根拠に失敗判定しない。
- POST後のGET/list失敗時は結果不明として表示する。approve POSTを自動で再送しない。
- 別系列・別versionを選択したり、DRAFTをACTIVEとして表示したりしない。承認済みなら、その事実と再読み込み可能な導線を保つ。

TemplatePage.test.tsxに承認中ロック・revision競合・既存の再取得検証があるか確認し、欠けた振る舞いだけを補う。

### 7.3 拡大プレビューdialog

対象はOcrPage.tsxのocr-template-preview-dialog。

- 開いた時にdialog内へfocusを移す。
- Tab / Shift+Tabをdialog内に留め、Escapeまたは閉じる操作で閉じる。
- 閉じた後は開いたボタンへfocusを戻す。
- aria-modal、label、背景クリックによるcloseを維持する。
- 表示目的のdialogにOCRデータやtemplate layoutを変更する操作を追加しない。

既存dialog実装や利用中UI部品で同じ動作が保証される場合は再利用する。追加依存は導入しない。

**最小検証:** 関係するApp.test.tsx、OcrPage.test.tsx、TemplatePage.test.tsxの既存テストだけを実行する。Appの共通navigation stateを変更した場合はAGENTS.md §7の共通UI条件を適用し、必要なfrontend full testsを省略しない。E2Eは短いテストで境界を確認できない場合に限定する。

## 8. Phase 3 — 見本画像fetchの計測と最適化

現状のOcrPage.tsxにはtemplateCards変更時にsourceJobIdを持つ全versionのartifactsをPromise.allで取得するeffectがある。最初に現在の再描画・再取得回数をmockまたはローカル計測で確認する。

実装条件:

- 利用者が必要とするversion/cardのpreviewだけを必要時に遅延取得する。
- cache keyは少なくともversionIdとsourceJobIdに結び付ける。sourceJobId/versionが変わった古いpreviewを再利用しない。
- unmount、version切替、list更新後の遅いresponseで新しいselectionを上書きしない。
- 取得結果なし・失敗時に読み取り操作を妨げず、既存のplaceholder/error表示に収める。
- 複数previewの取得が残る場合は同時数を有界にする。遅延取得で同時数が自然に1となる経路に不要なqueue/一般化機構を追加しない。
- api.artifactsのrequest数と初回preview表示時間を変更前後で同じ条件で比較する。測定結果なしに「高速化した」と報告しない。
- OpenAI、Google等の外部APIは呼び出さない。

OcrPage.test.tsxのtemplate card previewケースを利用する。request数やstale responseの挙動を既存テストが覆わない場合だけ、最小の回帰ケースを追加する。frontend buildはUI変更のphase群の最後に一度実行する。

## 9. Phase 4 — LayoutEditor分割

ファイル全体を一度に作り直さない。既に存在するlayoutAuthoring.tsとheadingConnections.tsを先に棚卸しし、同じ純粋関数を複製しない。

順序:

1. LayoutEditor.tsx内の候補関数を、React state、DOM、canvas context、API、clockに依存する関数と、入力から出力が決まる純粋関数に分ける。
2. 既存のpure helper moduleに属する関数だけを小分けに移す。geometry、targetPath、relation、merge、identityを変えるついで修正をしない。
3. 各移動で既存LayoutEditor.test.tsxの該当ケースを保ち、移動先moduleから直接importする既存テスト方式に合わせる。
4. pure helperが安定してから、canvas描画、meaning controls、mark/choice編集等の表示責務を小さいcomponentへ分ける。
5. LayoutEditorのpublic props/ref、autosave、draft復元、revision、保存中の追加編集を維持する。機械的なmodule分割だけで中間APIや汎用frameworkを増やさない。

境界条件:

- cell ID、relation ID、targetPath、field identity、cellMerges、polygon vertices、recordGroupsを再生成しない。
- 編集中layoutをOCR結果や過去job snapshotへ反映しない。ACTIVE版を直接変更しない。
- 保存payload、validation、DRAFT/revision経路、未対応状態の拒否を変更しない。
- helperの出力・例外・境界値・参照同一性が仕様に含まれる場合は保持する。

**最小検証:** frontend/src/components/LayoutEditor.test.tsxと、編集したhelperに既に対応するテストだけを実行する。layout保存形式・mapping生成・identityへ実質変更が入った場合は、UI-only扱いにせずAGENTS.md §7のOCR-sensitive検証へ分類する。

## 10. Phase 5 — OcrPage分割

大きな画面を次の責務候補に分ける。実コードに既存境界があればそれを優先し、表示だけの薄いcomponentを過剰に作らない。

- template選択とpreview
- 画像fileの選択・検査・保持
- OCR受付前のmapping/readiness表示
- job進捗・失敗・再試行導線
- 結果画面への移動と親Appへのnavigation guard通知

OcrPageまたは既存containerをpublic facadeとして保ち、App・TemplatePageとのprops契約を一括変更しない。受け入れ条件:

- 選択file、versionId、step、mapping readiness、job idの所有者を明確にする。
- 非同期fetchの古いresponseが新しいselectionを上書きしない。
- 受付payload、snapshot、mappingId、targetPath、sourceRegionId、retry semanticsを変えない。
- empty / null / unavailableの意味を変えず、OCR前の読み取り準備状態を「保存済み補正」と誤認しない。
- Phase 2のnavigation guardとPhase 3のpreview cacheを再検証する。

**最小検証:** OcrPage.test.tsxを主対象にし、Appとの受け渡しを変更した場合だけApp.test.tsxを追加する。frontend buildはPhase 2〜5のUI改修が揃った時点で実行する。共通UI/stateに該当する場合はfull tests条件を適用する。

## 11. Phase 6 — OcrResultService分割

backend/app/services/ocr_result_service.pyは補正・確定・同期・出力・previewが同居する。最初に各public methodのcaller、transaction/session所有者、commit位置、外部副作用、例外境界を表にし、移動順を決める。

分割候補:

- 読み取りpayload・record/review structure・field preview
- correction / reopen / confirm / confirmed snapshot
- spreadsheet edit / sync / rename / status
- XLSX/CSV exportとoutput rendering

既存routeが呼ぶOcrResultService facadeとmethod signatureは原則維持する。最初からroutesやDB schemaまで変えない。分割は一責務ずつ行い、巨大なclassを複数の同じ大きさのclassへ移すだけにしない。

守るtransaction / 状態境界:

- correctionは既存revision照合・再検証・履歴保存を経る。
- confirmのDB commit、document reconciliation、confirmed snapshot生成の順と失敗境界を維持する。
- local confirmedとexternal spreadsheet sync statusを分ける。同期失敗だけでconfirmedを未確定へ戻さない。
- sync retryの冪等性、operation ID、account/ownership/conflict保護を迂回しない。
- exportは受付snapshot/output bindingと確定revisionを使い、表示順や名称からdestinationを推測しない。
- 元XLSX bytes、結合範囲、他sheet、書式、印刷設定の既存保持を壊さない。
- legacy単一ページとpaged専用review/output経路を混ぜない。

Google Drive/Sheets向けの新機能・新しい同期動作は追加しない。既存同期methodを構造上分離する場合も、今回のv0.1で利用者に提供する機能と、互換用に残る既存経路を区別する。外部同期コードの削除・停止は、この責務分割へ混ぜず個別の影響確認を要する。

**OCR検証ゲート:**

- 本phaseは補正・確定・revision・outputを扱うため、OCR-sensitiveとしてAGENTS.md §7とSafety Contract §8に従う。関連unit/integrationに加えて、backend full testsが対象となる変更分類ならその必須条件を省略しない。
- ARTIFACT_STORAGE_POLICY.mdはpytestのbasetempにDドライブ上の一意な絶対パスを要求する。backend/.venv、frontend/node_modulesはこのコピーには含まれていないため、実行環境を先に確認する。
- デプロイ用コピーのscripts/verify_ocr_contract.pyはPhase 0で安全な `--basetemp` 必須指定へ更新した。未使用のDドライブ絶対パスを渡す。テスト対象・assertionは維持する。過去のscript・別コピーを実行するときは出力先を再確認し、Cドライブ上に生成しない。
- 外部APIではなく既存fixture/mock/保存responseを使う。実API呼出しを新たに行わない。

Safety Contract §8の必須scriptが安全に実行できなかった場合、関連テストが通っても「OCR安全性を完全には検証できていない」と報告し、検証完了とは表現しない。

## 12. Phase 7 — テスト選択、テスト後の整理、完了条件

### 12.1 最小テスト選択

テストは変更のある境界だけを選ぶ。コード量が増えたという理由でtestを追加しない。既存テストで欠ける再現可能な不具合に限り、最小の回帰testを追加する。

| 変更 | 最小対象 |
| --- | --- |
| navigation / selected file / dialog | App.test.tsx、OcrPage.test.tsx |
| approval / ACTIVE再取得 | TemplatePage.test.tsx |
| template preview取得 | OcrPage.test.tsx |
| LayoutEditor helper / component | LayoutEditor.test.tsxと該当helper test |
| OcrResultService read/preview | test_ocr_result_previews.py、必要なreview API境界 |
| correction / confirm / revision | test_ocr_confirmation_revision.py、必要なintegration test |
| spreadsheet sync / output | test_spreadsheet_edit_sync.py、test_spreadsheet_template_output_contract.py、必要なreview API境界 |

frontend変更は対象testとbuildを使う。App共通navigation stateや共通UIを複数画面で変える場合はAGENTS.md §7のfrontend full tests条件を適用する。OCR-sensitive backend変更は関連testとAGENTS/Safety Contractが要求するscript・backend full testsを適用する。これは全体testの一律再実行ではなく、変更分類上の必須gateである。チェックリストの「既存作業ツリーの全体テスト再確認: 要らない」は、無関係なbaseline full rerunを要求しない意味で適用する。必須gateを省略する許可とは扱わない。

E2Eは短いテストで境界を検証できないときだけ対象シナリオに限定する。E2Eを実行して成功した場合、AGENTS.mdに従い実行条件と結果artifactを保存する。OpenAI/Google実APIはfixture/mockで不足し、かつ明示許可がある場合以外は呼び出さない。

### 12.2 pytest artifact

- 全pytestはARTIFACT_STORAGE_POLICY.mdに従い、未使用のDドライブ上の一意な絶対basetempを指定する。
- C:\Users\hanam\Desktop\llm-ocr-deploy\backend\.pytest_cacheが残っていても、過去テストの成功証拠とは扱わない。
- repo内に.pt-*、.pytest-*を作らない。既存tempや利用者データを流用・削除しない。
- script実行が許可された場合も、script内部の出力先を事前に確認する。

### 12.3 「使い古したテスト」を残さないための整理

- phase中の一時probe、使い捨てscript、仮fixtureは、結果を記録した後に当該phaseの作業用artifactから取り除く。
- しかし、一度実行して通ったという理由だけで、既存testや再現可能な不具合の回帰testを削除しない。将来同じ不具合を防ぐtestは使い捨てではない。
- 恒真・内部構造だけを見るtest、今回の実装を通すためだけのtest、既存testと完全に重複するtestは追加しない。
- 既存testが本当にobsoleteまたは重複で削除候補となる場合は、何の契約を検証していたか、現在どのtestが代替するかを確認し、coverageを落とさず削除理由を報告する。test削除・skip・assertion弱体化でgreenにしない。
- E2E成功時の再現情報と結果artifactはAGENTS.mdの保存要件を優先し、テスト後の一律削除対象にしない。

### 12.4 完了判定

- 選択した機能フローが受け入れ条件を満たし、未選択機能を追加していない。
- 既存OCR contractを変更していない場合、変更境界を示して「既存OCR contractを維持した」と報告できる。
- 必須の最小対象test、build、backend検証の結果と、未実行・失敗・環境制約を区別する。
- git diff --checkと終了時git status / diffで既存変更を保護する。
- temp test、使い捨てprobe、誤って作られたartifactを確認し、永続的に残すべき回帰testと区別する。
- staging / commit / push / reset / checkoutによる既存変更破棄を行わない。

完了報告には、変更ファイル、目的と変更境界、実行したコマンドと結果、未検証範囲、削除または保持したtestと理由を記載する。成功したtest数だけでOCR safetyやproduction readinessを主張しない。
