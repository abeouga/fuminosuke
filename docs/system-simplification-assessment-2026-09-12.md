専門家A（保守性・模擬評価）：**簡素化は可能です。** 主な対象は、一つの画面やサービスに集まった責務と、画面をまたぐ保存状態の管理です。ファイル分割だけで総行数が減るとは限りませんが、変更時に理解・検証する範囲を狭められます。

専門家B（OCR品質・模擬評価）：セル・mapping・cropの対応、保存中の追加入力保護、revision、過去ジョブのsnapshotは必要な複雑さです。これらを削って短くする案は採用できません。描画の整理と、保存・変換ロジックの変更を分ける必要があります。

専門家C（性能・運用・模擬評価）：テンプレート一覧の取得には件数に比例してHTTP要求が増える構造があります。ここは性能改善の具体的な候補です。Canvas描画や下書き保存にも計測候補がありますが、現時点で実際の遅延原因とは断定できません。

# システム簡素化・最適化候補の調査

調査日：2026-09-12。対象：`C:\Users\hanam\Desktop\llm-ocr`、HEAD `17dd6a47` と調査時点の未コミット変更。

## 客観的まとめ

- **3者の一致点：** OCRの安全な対応関係を保ったまま、責務の集中と重複した実装手順を減らす余地があります。
- **優先順位の相違：** 保守性ではレイアウト編集の分割、品質では保存失敗・競合の検証、性能では一覧取得の要求数削減を優先します。
- **判断条件：** 日常の問題が「変更しづらい」「操作が難しい」「表示が遅い」のどれかによって着手順が変わります。今回、実操作時間と処理時間は測定していません。
- **推奨：** まず小さい共通処理と表示責務を整理し、次に保存状態を整理します。Driveと保存APIの再設計は、別の段階で扱うのが合理的です。

「最適化」を、①保守の簡素化、②通信・計算量の削減、③利用者の操作削減に分けます。この文書は①を中心に、コードから根拠を確認できた②を含めます。③の具体的な工程廃止には、実際の作業頻度と利用状況の確認が必要です。

## 1. 調査範囲と限界

- コード、設定、DB、既存文書は変更していません。今回追加する成果物はこのMarkdownのみです。
- 開始時点で追跡済み17ファイルに変更がありました。レイアウト編集、テンプレート画面、出力サービス、関連テストなどの既存差分を含む状態を調査しています。
- 実装の静的調査、ファイル規模の集計、Python ASTによる関数長・同一関数本体の確認、既存テストと契約の参照を実施しました。
- 全行の精査、動作テスト、ビルド、実ブラウザー操作、性能測定、DB操作、migrationは実施していません。現在のOCR正常動作を保証する調査ではありません。
- OpenAI・Google API呼出しは各0回です。認識誤り・mapping誤対応などの実測はしていません。
- 本文のファイル位置は調査時点のものです。進行中の変更で行番号は変わり得ます。
- 判断の正本は [AGENTS.md](C:/Users/hanam/Desktop/llm-ocr/AGENTS.md) と [OCR Safety Contract](C:/Users/hanam/Desktop/llm-ocr/docs/ocr-safety-contract.md) です。

## 2. 確認した複雑さ

以下は空行・コメント込みの物理行数です。`useState` / `useEffect` はファイル全体の呼出し表記数であり、単一コンポーネントの状態数や実行コストではありません。

| 対象 | 行数 | 観察 |
| --- | ---: | --- |
| `frontend/src/components/LayoutEditor.tsx` | 7,235 | `useState` 40、`useEffect` 24。描画、操作、保存、意味づけ、帳票固有処理を含む |
| `frontend/src/pages/ReviewPage.tsx` | 2,821 | 結果取得、編集、競合復旧、確定、表描画を含む |
| `frontend/src/pages/TemplatePage.tsx` | 1,251 | ファイル内の複数コンポーネントで `useState` 47。作成・管理・編集・保存手順を含む |
| `frontend/src/components/PortableBackupDialog.tsx` | 1,371 | バックアップ関連の画面と状態がまとまっている。詳細な分割設計は今回未実施 |
| `frontend/src/styles.css` | 1,751 | 同じセレクターを複数の位置で再定義 |
| `backend/app/services/google_drive_service.py` | 6,691 | 保存先、出力、ライブラリ同期、package、workspace snapshotを含む |
| `backend/app/services/ocr_result_service.py` | 2,030 | 結果・補正・確定など。保護範囲が大きく、初手の整理対象にはしない |

補助集計では、frontendの本体46ファイルで23,181行、backendの`app`配下211ファイルで52,854行でした。frontendは `.ts` / `.tsx` / `.css` を対象に `.test.` を除外した数です。規模は調査箇所を選ぶ指標であり、欠陥や低速性の証明ではありません。

Python側では、8行以上の関数について、異なるファイル間でASTの関数本体が完全一致する例は今回の探索では見つかりませんでした。変数名違い、部分重複、意味的な重複は検出対象外です。「backend全体がコピーの重複で膨らんでいる」とは断定できません。

## 3. 優先順位

P1は小さく着手する候補、P2は境界テストを整えて段階的に行う候補、P3は計測・追加設計後の候補です。リスクは実装時の相対評価です。

| 優先 | 候補 | 主な効果 | リスク | 最初の変更単位 |
| --- | --- | --- | --- | --- |
| P1 | A. アップロード処理の共通化 | 重複削減 | 低〜中 | XHRの通信部分だけ |
| P1 | B. CSSの重ね書き整理 | 表示変更の追跡を容易にする | 中 | sidebarの一領域だけ |
| P1 | C. LayoutEditorの表示責務分割 | 読む範囲・変更範囲の縮小 | 中 | ツールバー・表示部品だけ |
| P1 | D. ReviewPageの操作処理の分離 | 保存・確定の順序を読みやすくする | 中〜高 | JSX内の処理を名前付き関数へ移す |
| P2 | E. テンプレート保存状態の集約 | 状態・失敗箇所の理解を容易にする | 高 | 現在のAPI順序を保った保存制御 |
| P2 | F. 一覧取得の要求数削減 | 通信・応答待ちの削減候補 | 中 | 一覧で必要な版情報の取得方式 |
| P2 | G. Google Driveの責務分割 | 影響範囲・検証範囲の縮小 | 高 | package / workspace系から1領域 |
| P3 | H. 型定義の二重保守削減 | 変更漏れの予防 | 中 | 1種類のAPI入出力型 |
| P3 | I. 描画・下書き保存の計測 | 操作遅延の削減候補 | 中〜高 | 表示集計の再計算だけ |

## 4. 各候補の具体案

### A. アップロードの共通部分を一つにする

**確認事実：** [api.ts:41](C:/Users/hanam/Desktop/llm-ocr/frontend/src/api.ts:41) の `uploadPortableRestore` と同ファイルの `uploadTemplatePackageInspection` は、XHR作成、Cookie付与、進捗通知、HTTP判定、401通知、FormData送信をそれぞれ実装しています。復元側にはabort時の独自処理があり、完全に同一ではありません。

**提案：** URL、応答型、エラーメッセージ、abort時の扱いを渡す小さなアップロード関数に通信部分だけを集約します。現在の公開関数は残し、呼出し側の変更を抑えます。

**効果と制約：** 認証・エラー処理の修正箇所を減らせます。アップロード速度の向上は期待根拠がありません。バックアップの復旧手順とpackageの衝突判定は統合しません。

**完了条件：** 成功、401、HTTP失敗、通信断、復元の中止、進捗通知が従来どおり。`PortableBackupDialog.test.tsx` と `TemplatePackageDialog.test.tsx` に加え、不足しているXHR境界を確認します。

### B. CSSを「後ろの定義を探す必要がある状態」から整理する

**確認事実：** [styles.css:13](C:/Users/hanam/Desktop/llm-ocr/frontend/src/styles.css:13) で定義された `.side-footer` に対し、152、594、757行付近などで追加・上書きがあります。35行付近のレスポンシブ指定も関係します。

**提案：** sidebarについて、基本定義と画面幅別の差分を近くに集めます。まず最終的に適用されているスタイルを確認してから移し、重複セレクターを機械的に削除しない方針です。

**効果と制約：** 表示修正の見落としを減らせます。CSSファイルを分けるだけでも読みやすくなりますが、読込順が変われば見た目が変わります。速度改善より保守性の改善です。

**完了条件：** デスクトップと既存breakpointの前後で、sidebar、アカウント操作、文字折返し、非表示条件が一致。frontend full testsとbuildに加え、実画面比較が必要です。

### C. LayoutEditorから表示部品を先に分離する

**確認事実：** [LayoutEditor.tsx:915](C:/Users/hanam/Desktop/llm-ocr/frontend/src/components/LayoutEditor.tsx:915) 以降の編集画面に、保存制御、Canvas描画、選択、意味づけ、見出し・レコード操作が集まっています。同じファイルには `MarkCellEditor`、`GenericChoiceCellEditor`、意味づけ生成・正規化などの関数もあります。

**提案：** 最初はツールバー、表示用アイコン、属性パネルなど、表示とイベント受渡しだけを行う部品を外へ出します。描画関数は表示座標を受け取る境界を確認して次段階へ回します。`layoutAuthoring.ts` や `pageAuthoring.ts` という既存の分割先もあり、その責務に沿わせます。

**効果と制約：** 一つの表示変更のために7,000行超を追う必要を減らせます。ただし状態全体を各部品へ大量に渡すだけでは改善が小さいため、必要な値とイベントを限定します。

**変更境界：** `normalizeLayout`、`repairDuplicateCalvingMarkRows`、保存先割当、結合、cell ID生成は表示処理ではありません。これらの整理を同時に含めません。

**完了条件：** 選択・表示・ショートカット・工程移動が一致し、保存payloadが変わらないこと。該当する既存 `LayoutEditor.*.test.tsx` とbuild、Canvasの実操作比較で確認します。

### D. ReviewPageの保存・確定処理をJSXから外す

**確認事実：** [ReviewPage.tsx:1283](C:/Users/hanam/Desktop/llm-ocr/frontend/src/pages/ReviewPage.tsx:1283) のdecision barに、表示分岐、修正保存、差分判定、確定API、応答反映、失敗復旧が一行のJSXとして含まれています。

**提案：** まず修正保存と確定をそれぞれ名前付き関数へ移し、判定・API順序・payload・エラー処理をそのまま維持します。表示部品の分割や専用hook化は、その後に必要性を評価します。

**効果と制約：** 成功と失敗の流れを追いやすくなります。単なる移動段階では状態数や通信数は減りません。`result` と編集中の `draft` は用途が違うため統合しません。

**完了条件：** 修正後のrevisionで確定すること、競合時に入力を保持すること、同期失敗でも確定を取り消さないこと、確定後もcropを閲覧できること。`ReviewPage.test.tsx` とbuildで確認し、補正・確定の意味やpayloadまで変える場合はOCR-sensitive検証を追加します。

### E. テンプレートの保存を一つの制御単位にまとめる

**確認事実：** [LayoutEditor.tsx:1655](C:/Users/hanam/Desktop/llm-ocr/frontend/src/components/LayoutEditor.tsx:1655) が自動保存、操作lock、送信後の編集保護、保存完了待ちを扱い、[TemplatePage.tsx:987](C:/Users/hanam/Desktop/llm-ocr/frontend/src/pages/TemplatePage.tsx:987) の `onSave` が以下を順番に行います。

1. layout保存、またはrevision付きページ編集保存。
2. field mappings再取得。
3. 保存されたmappingから書類識別項目を照合。
4. 書類識別項目・書類名設定を含むfield mappings保存。
5. ページ編集ではprintLayoutを再取得。

前半が保存済みでも後半が失敗し得るため、現在の処理は「layoutは保存されたが、書類の見分け方は保存できなかった」を扱っています。これは調査で確認した設計上の事情であり、現在不具合が再現したという意味ではありません。

**提案：** 第1段階では、この手順を画面から名前付きの保存制御へ移します。編集内容、送信時内容、保存応答、一時表示状態の所有者を明示し、現在の直列化と未保存判定を維持します。単一の `busy` にまとめるだけの整理は避けます。

**第2段階の候補：** 保存回数や部分失敗が実運用の問題と確認できた場合に、版・revisionをまとめて扱うbackend APIを別途設計します。現状は [LayoutService:330](C:/Users/hanam/Desktop/llm-ocr/backend/app/services/layout_service.py:330) がmapping保存とlayout保存を調整し、[TemplateManagementService:236](C:/Users/hanam/Desktop/llm-ocr/backend/app/services/template_management_service.py:236) がYAMLも保存します。HTTPを1回にしても、DBとファイル保存が自動的に一つのtransactionになるわけではありません。

**完了条件：** 保存中の追加入力、古い応答、工程移動、後続GET失敗、revision競合、途中設定の保存、下書き復元を固定します。`LayoutEditor.autosave.test.tsx`、`LayoutEditor.pages.test.tsx`、`TemplatePage.test.tsx` は確認対象です。mapping生成・保存APIを変える段階は高リスクで、OCR-sensitive検証が必須です。

### F. テンプレート一覧の「1件ごとの詳細取得」を見直す

**確認事実：** [App.tsx:89](C:/Users/hanam/Desktop/llm-ocr/frontend/src/App.tsx:89) の `refreshTemplates` は一覧GETの後に全テンプレートの詳細GETを `Promise.all` で実行します。N件ならコード上の取得数は **1 + N要求** です。[templates.py:421](C:/Users/hanam/Desktop/llm-ocr/backend/app/api/v1/routes/templates.py:421) の一覧は `versions=[]` を返すため、現在の詳細取得には理由があります。同時refreshの共有は既に実装されています。

**提案：** 一覧で実際に必要な版情報を整理し、必要な情報をまとめて返す読取APIを検討します。大量のquality metadataまで無条件に一覧へ含めず、詳細画面に必要な情報は別取得に残します。承認・版編集後の更新タイミングも維持します。

**効果と制約：** 一覧表示を1要求で満たせる設計ならHTTP要求数を削減できます。ただしDB検索もまとめなければbackend内へ件数比例の検索を移すだけになります。通信量・JSON生成量・表示時間の比較が必要です。

**完了条件：** 10・50・100件など同一データ条件で要求数、応答量、一覧表示完了時間を比較。表示する版、DRAFT再開、ACTIVE切替、一覧更新、取得失敗時の再試行が従来どおり。`App.test.tsx` と `TemplatePage.test.tsx`、関連APIテスト、frontend full tests/buildを確認します。

### G. Google Driveサービスを用途別に分割する

**確認事実：** [google_drive_service.py:57](C:/Users/hanam/Desktop/llm-ocr/backend/app/services/google_drive_service.py:57) のクラスには、workspace snapshot、template package、出力先、Spreadsheet出力、ライブラリの同期・移動・名前変更が集まっています。`create_spreadsheet_from_xlsx` は1421〜2309行の889行です。

**提案：** 外部から呼ぶ入口を維持し、内部を「共通認証・通信」「workspace snapshot」「template package」「library同期」「Spreadsheet出力」へ順に分けます。一度に全領域を移すのではなく、package等の一領域から始めます。

**効果と制約：** 各機能の変更時に確認する範囲を狭められます。ただし共通lockとアカウント状態があり、分割先ごとに独立した認証状態を持たせる設計は危険です。現在の `_drive_operation` とアカウント切替の直列化を維持します。

**削減対象にしない処理：** 出力前後のownership・保存先・operation ID・revision照合、失敗後の再試行と補償処理。長い関数には外部変更・部分成功を扱う必要な分岐が含まれるため、同じ対象を二度取得するだけで不要と判断しません。

**完了条件：** アカウント切替、移動途中の失敗、再試行、重複出力防止、古いrevisionでの更新拒否を既存テストで確認。`test_document_library_and_drive.py`、`test_template_package_drive_sync.py` などに加え、共有service変更としてbackend full testsと静的検査が必要です。出力対応へ影響すればOCR-sensitive検証も追加します。

### H. API型の二重保守を限定的に減らす

**確認事実：** [types.ts:382](C:/Users/hanam/Desktop/llm-ocr/frontend/src/types.ts:382) にTypeScriptの `LayoutCell`、[models.py:58](C:/Users/hanam/Desktop/llm-ocr/backend/app/layout_analysis/models.py:58) にPydanticの `LayoutCell` が存在します。類似した構造を両側で定義しています。今回の調査では、型不一致の発生までは確認していません。

**提案：** まずAPI入出力型を一種類選び、backend schemaとの照合または型生成の適用可否を試します。UIだけの選択状態や表示座標型は手書きのまま分けます。

**効果と制約：** フィールド追加時の変更漏れを減らせます。一方、生成工程という保守対象が増えます。全型を一括生成する案は初手にせず、optionalとnull、alias、dict部分の型を保てることを先に確認します。

**完了条件：** 同一fixtureで入出力の意味が一致し、既存の通信形式と保存形式が変わらないこと。型生成でruntime validationやOCR契約テストを代替しません。

### I. 描画と下書き保存は、計測してから最適化する

**確認事実：** [LayoutEditor.tsx:1914](C:/Users/hanam/Desktop/llm-ocr/frontend/src/components/LayoutEditor.tsx:1914) では、各record sectionの描画時にrecord groupsをfilterして件数を数えています。また `layoutSignature` はlayout全体のJSON化で、1130行付近のeffectは変更されたsignatureをlocalStorageへ保存します。既に `useMemo` やID別Mapを利用している箇所もあります。

**提案：** まず同一帳票で、描画時間、編集から描画までの待ち時間、JSON化時間、localStorage保存時間を分けて測定します。遅延が確認された場合だけ、section別件数を一度集計して描画で参照するなど、表示側の計算を減らします。

**効果と制約：** section数をS、group数をGとすると、この件数集計部分は毎描画のO(S×G)走査から、集計O(G)と参照O(S)へ変えられる候補です。画面全体の高速化率は未測定です。セルが少なければ効果は小さい可能性があります。

**完了条件：** 実帳票と規模を増やしたfixtureで操作時間を比較し、表示・選択が一致。JSON比較を編集カウンター等へ置き換える案や下書き保存の遅延化は、未保存判定・復旧可能性を変えるため別評価にします。

## 5. 維持する構造・初手では行わない変更

- **OCR計算と永続化の境界：** [ocr_workflow_service.py:43](C:/Users/hanam/Desktop/llm-ocr/backend/app/services/ocr_workflow_service.py:43) からrequest preparationとresponse finalizationを呼ぶ分離が既にあります。[依存方向のテスト](C:/Users/hanam/Desktop/llm-ocr/backend/tests/ocr_contract/test_phase6_architecture_dependencies.py) も存在します。ファイルを減らすために再統合する利点は今回確認できません。
- **状態の意味の区別：** [job_state.py](C:/Users/hanam/Desktop/llm-ocr/backend/app/domain/job_state.py) に状態遷移表があります。レビュー確定、通信中、選択中、同期済みを一つの状態へ潰しません。
- **旧版と新しいページ処理：** 過去jobのsnapshotと新しいprintLayoutを統合して再計算しません。未認定ページOCRの拒否も簡素化のために解除しません。
- **汎用帳票と牛台帳固有処理：** 固有処理を適切な場所へ分ける余地はありますが、既存帳票が利用する機能を未使用と推測して削除しません。
- **基盤の全面置換：** 新しい状態管理ライブラリ、マイクロサービス、別queue基盤などを追加する必要性は、この調査では確認できません。現在のプロセス内queue自体は小さな実装です。
- **二重保存の即時廃止：** DB・YAML・artifactは用途と互換性が異なります。保存先を減らす場合は参照元、復旧、過去データ、package互換性の別調査が必要です。

## 6. 実施する場合の順序と検証

1. **小さい変更で比較基準を作る。** AまたはBを一つ選び、既存テストと実UIのbaselineを確認します。同じ変更に保存ロジック整理を含めません。
2. **表示責務を分割する。** C、次にDを行い、振る舞いを保持した移動を独立した変更単位にします。単なる行数削減を成果指標にしません。
3. **保存状態を明文化する。** Eの成功・部分成功・失敗・競合を整理し、APIの順序を変えない範囲から着手します。
4. **実測できる最適化へ進む。** FとIは通信数・操作時間を変更前後で比較します。遅延が小さい場合は優先度を下げます。
5. **大きなサービスを段階分割する。** Gは一領域ずつ。Hは型の二重保守で実際に生じる手戻りと、生成工程の費用を比較して採否を決めます。

実装時のコマンドは変更範囲で選択します。以下は**今後の検証例であり、今回の実行結果ではありません。**

```powershell
# frontend/ を作業ディレクトリにして実行
npm test -- src/components/LayoutEditor.autosave.test.tsx src/pages/TemplatePage.test.tsx
npm run build
# 共通UI・複数画面の状態変更では全体も実行
npm test
```

```powershell
# リポジトリroot。OCR-sensitive変更では変更前後で実行
.\backend\.venv\Scripts\python.exe scripts/verify_ocr_contract.py

# backend/ を作業ディレクトリにして実行
$simplificationRunId = [guid]::NewGuid().ToString("N")
.\.venv\Scripts\python.exe -m pytest --basetemp=".pt-simplification-$simplificationRunId"
# 以下はDriveサービスを変更した場合の対象例
.\.venv\Scripts\python.exe -m ruff check app/services/google_drive_service.py
.\.venv\Scripts\python.exe -m mypy app/services/google_drive_service.py
```

OCR-sensitive変更では、上記だけでなく同じ画像・layout・保存responseによるmapping ID、source region、crop、review ownership、revision、出力セルの比較も必要です。専用スクリプトはfrontend・backend全体やすべてのGolden replayを網羅しません。確認できない部分は未検証として残します。

## 7. 今回の成果物と判定

今回の追加ファイルは本書だけです。本タスクではコード・設定・DB・既存未コミット変更を編集していないため、**変更範囲の意味で既存OCR contractを維持した**と判断します。これは動作テストによる再保証ではありません。

検証結果：ローカル参照先19件の存在・行番号範囲を確認し、`git diff --check` と新規文書の `git diff --no-index --check` は通過しました。開始時と終了時の既存差分のSHAは一致しませんでした。調査中、本タスクの書込み以外に、少なくとも `docs/template-background-save.md` と自動保存・テンプレート画面の既存テストの差分が増えています。そのため、worktree全体が開始時と同一という保証は行いません。これらは変更・巻戻しせず、並行更新として保持しています。

現時点で最も根拠があるのは、**巨大な画面の表示責務分割、保存制御の見通し改善、アップロードの重複削減、一覧取得の要求数削減**です。処理速度の改善率や開発工数の削減率は未測定であり、数値の保証はしません。
