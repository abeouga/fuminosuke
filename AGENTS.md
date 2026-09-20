# Repository Agent Instructions

## 1. 現在の開発方針

現在は、検証済みのOCR correctness・association・mappingを維持しながら、人間側の操作体験を改善するフェーズです。優先順位は次のとおりです。

1. 帳票の物理セルからOCR結果、レビュー、保存・出力までの対応関係を維持する。
2. レビューUX、テンプレート作成UX、修正・確定フローを改善し、不要な操作を減らす。
3. 状態遷移とエラー復旧を改善し、ユーザー修正値を保護する。
4. 将来のQuest / MR利用に備え、UI・HTTPと既存OCRユースケースの境界を保つ。

現在のOCRは完成・永久凍結ではありません。正常動作は作業開始時に証拠で確認するbaselineであり、過去の成功報告を現在の保証として扱わないでください。要求されていないOCR最適化・一般化・リファクタリングは行いません。

## 2. 最初に読むものと作業分類

この文書は作業方針・変更境界・文書ルーティングの入口です。詳細な技術契約の正本は [OCR Safety Contract](docs/ocr-safety-contract.md) です。

- 最初に依頼の目的、変更候補、`git status`、既存差分を確認する。
- OCR結果、review状態、template layout、補正保存、確定・再読取、出力対応へ影響し得る変更の前に、Safety Contractを最後まで読む。
- 表示だけの変更か、データの生成・変換・保存を変える変更かを実コードで分類する。frontendファイル内でもmapping生成や補正payload変更はOCR-sensitive。
- ユーザーがread-onlyや特定ファイルへの限定を指定した場合は、その範囲を優先する。

## 3. Default Change Boundaries

UX/UIタスクは、依頼された表示・操作・一時UI状態の範囲で最小限に実装します。React側で解決できる問題のために、backend service、DB schema、OCR pipeline、provider、prompt、model、schema、mapping、association、physical identityを変更しないでください。

バックエンド変更が必要な場合は、原因のfile/function、フロントエンドだけでは解決できない理由、影響する契約・検証を示します。既に明示されたタスク範囲内なら必要最小限を実装し、OCR改善など別目的への拡張が必要なら根拠を報告して指示を求めます。

「コードが綺麗になる」「抽象化・一般化できる」「速くなりそう」「将来必要になりそう」だけでは保護領域を変更しません。Quest / MRへの備えも、新しいOCR経路やjob lifecycle迂回の理由にはしません。

## 4. OCR Core Protected Area

保護対象はファイル名だけでなく、以下を生成・変換・保存するすべての処理です。実装対応表はSafety Contract §2を参照してください。

- provider、model設定、prompt、structured-output schema、request manifest、compact ID展開、association検証
- rectangle / region detection、画像前処理、boundingBox、crop / fieldPreview
- template cell association、physical identity、mapping identity、受付時snapshot
- `mappedCells`、`reviewStructure`、field ownership、再読取時の対応関係
- `FieldReading`の値・判定、`status` / `reviewReason` / `reviewItems`の意味
- 補正済み値、revision、確定snapshot、OCR出力からSpreadsheetセルへの対応

保護対象の挙動を変えない表示編集は可能です。単に同じファイルに存在するという理由だけで、UI修正にOCRコア変更を含めないでください。

## 5. OCRコア変更を許可する条件

OCR精度・OCRロジック・対応関係の改善を明示的なタスクとして扱い、変更前に以下を満たしてください。

1. 目的と変更範囲がユーザーの依頼で認められている。
2. 再現可能な不具合、または同一帳票・保存response・fixtureで評価可能な精度課題を示せる。association / mapping / crop不具合は誤った対応を具体的に示す。
3. 期待結果とbaselineを定義し、同じ入力・条件で変更前後を比較できる。
4. Safety Contractの影響箇所を確認し、Safety Contract §8の必須検証を変更前後で実行できる。

原因調査は安全なread-onlyで進められます。証拠や検証環境が足りない場合は、先に再現・fixture・環境を整え、推測修正をしないでください。不変条件を維持する不具合修正と、契約自体を意図的に変更する作業は区別します。後者にはユーザーの明示要求とSafety Contract §10の手順が必要です。

## 6. UXと状態管理の原則

- OCR・補正データ、サーバーの業務状態、一時UI状態を分ける。ポップアップや強調表示を消すためにOCR値・判定・警告根拠を削除しない。
- 明示的なユーザー補正では、既存の補正・revision経路で値や判定を更新できる。「データ保持」を補正禁止と解釈しない。
- レビュー完了はサーバーの `result.confirmed` を正本とする。選択、編集中、loading、lock、表示用warningは完了判定の代わりにしない。
- ローカル確定とGoogle Sheets / Drive同期を分ける。`confirmed=true` の結果を同期失敗だけで未確認へ戻さず、既存の再同期経路を使う。
- confirm / reopen / retry / sync failure、再取得・再マウント・loading終了時の状態遷移を確認する。確定APIエラーでもDBが未確定とは限らないため、値を保護してサーバー状態を確認する。
- 閲覧（セル選択・crop・結果表示）とレビュー編集を区別する。確定済みの編集UIを終了しても閲覧を維持する。
- エラー・revision競合で修正値を失わない。OCR初期値や新しいテンプレートから補正済み結果を勝手に再構築しない。

## 7. 検証の選択

詳細なコマンド・対象テスト・限界はSafety Contract §8を参照します。全作業で `git diff --check` と差分範囲確認を行います。

| 変更範囲 | 必須検証 |
| --- | --- |
| 文書のみ | 実コード・参照パスとの照合、文書間整合性、差分確認。動作テストは記載した仕様の確認が必要な場合のみ |
| UX/UIのみ（OCRデータ契約を変えない） | frontend対象テスト、frontend build。状態変更は成功・失敗・再取得・再オープンなど関係する遷移を固定 |
| 共通UI・複数画面に及ぶ状態変更 | 上記にfrontend full testsを追加 |
| backend変更（OCR-sensitive以外） | 関連unit / integrationと該当静的検査。共有service・DB・lifecycle変更はbackend full testsも実行 |
| OCR core / mapping / association / crop / 補正・revision・出力対応変更 | 変更前後の `scripts/verify_ocr_contract.py`、不足する境界テスト・保存response比較、backend full tests。frontendにも影響すれば対象テスト・buildを追加 |

専用スクリプトは現worktreeに存在しますが、全領域・Golden replay・frontend・backend全体を網羅するものではありません。欠落・実行不能なら関連する既存テストを実行し、未検証範囲と「OCR安全性を完全には検証できていない」ことを明記します。回帰があれば完了扱いにしません。

テスト通過のための削除・skip・assertion弱体化・都合のよいfixture変更は禁止です。既存の失敗と今回の回帰、対象テストと全体回帰、mockと実UI/API確認を区別してください。

実外部APIは保存response・fixtureで検証できない場合のみ検討します。既存の利用許可・予算の範囲を守り、不要なOpenAI / Google API呼び出しは行いません。

## 8. Git保護と完了報告

既存の未コミット差分を保持し、開始時と終了時のstatus/diffを確認します。明示指示なしのstaging、commit、push、reset、revert、checkoutによる変更破棄、無関係なformat/refactorは禁止です。

完了報告には変更ファイル、原因・変更境界、検証コマンドと結果、未検証範囲を記載します。OCR-sensitive変更ではSafety Contract §11の報告事項も含めます。OCR挙動を変えていない場合は、根拠とともに「既存OCR contractを維持した」と明記してください。

## 9. XLSX複数ページOCR / Reference Renderer

- legacy single-page OCRと新しいXLSX `printLayout` / paged OCRは別経路です。旧canonical rectangle・preview・X/Y画像合わせ補正・過去jobを新geometryで再計算しません。`lo-import-twip-v1`は新規printLayout用で、旧保存プランのgeometryも維持します。
- `printLayout`の所有者はTemplate系列ではなくTemplateVersionです。ページ編集はDRAFTとrevision照合を守ります。現在のGUI対象は横1ページ・縦Nページです。
- 新経路は1 logical document/jobに明示的なページidentityと入力bindingを持ちます。配列indexをidentityにせず、受付時のimmutable snapshotから実行・retryします。現在のTemplateVersionからranges・fields・mappings・基準画像・OCR設定を差し替えません。
- ページ別実行基盤と専用レビュー・補正・全ページ確定/再編集・記入済みXLSX取得経路は存在します。書類一覧への統合は未対応です。新結果は`PagedReview`/`PagedConfirmation`と受付page identityを使用し、旧単一OcrResultへ流しません。[ページ別レビュー](docs/paged-review.md)、[全ページ確定](docs/paged-confirmation.md)、[全ページXLSX出力](docs/paged-output.md)の適用・検証状況を確認してください。
- 新paged出力は受付時に固定したadapter・所有原本SHA・全ページの明示cell binding・確定revisionを使用します。取得時に現行版から出力先を補充しません。元sheet名・他sheet・書式・印刷設定を保持し、原本を再保存しません。未対応の旧受付に新adapterを後付けしないでください。
- ページ別の文書識別ヒントは全体設定を検証してから所有mappingへ限定します。識別groupや役割をページごとに付け替えず、書類名候補で入力中の名前を上書きしません。[文書metadata](docs/paged-document-metadata.md)を参照してください。
- 新paged OpenAIの呼出し予算は文書全体・全attemptで共有し、送信前にDB予約します。失敗や再起動で払い戻さず、後続ページの最低枠を保護します。[文書全体API予算](docs/paged-ocr-budget.md)を参照してください。
- Geometry / Reference QualificationとRenderer Environment Qualificationは別条件です。2026-09-13の明示承認により、有効化時のテンプレート/環境検証と、OCR実行時の実入力/位置合わせ/crop検証を分離します。前者は原本・全ページの描画/座標・mapping/crop領域の定義・隔離環境の証拠を要求し、記入済み画像を要求しません。後者はprovider送信前に実入力を検証し、未検証なら停止します。描画成功・fixture通過・保存booleanだけではどちらも認定できません。詳細はSafety Contract §12.6/§12.8を参照してください。
- `wsl-calc-print-grid-v1`の新printLayout版は、保存booleanではなく現在の正式reference/environment/geometry資格、原本・graph・artifact・実装・lease期限を承認transaction内で再検証した場合だけACTIVE化できます。legacy版は従来readinessを維持します。ACTIVE化後もpaged OCR受付は`PRINT_REFERENCE_NOT_VALIDATED`でfail-closedであり、実入力のidentity/registration/crop/ink検査とprovider送信は別gateです。作業都合のフラグ変更やmockで解除しません。
- AppContainerではローカル接続probeの通信拒否を実測していますが、LibreOffice 26.2.3.2のXLSX→PDFはWindows named pipe IPC互換性の問題で成立していません。Officeの完全sandbox化が済んだとは扱いません。
- 新reference rendererのPDFium parse/rasterは専用helper process内です。native処理を安易にbackend内へ戻しません。helperのnetwork isolation、実font・runtime依存、実帳票crop clearance等も認定の残課題です。
- renderer isolation / container方式の再設計は別infraタスクとして保留できます。gateを閉じたままlegacyや他の安全な機能開発を継続できます。再開時は下記Sandbox Closureの残条件と実コードを照合し、正式なqualificationを経てから本番開放を設計します。

この領域の変更前にはSafety Contract全体（特に§12）と、以下を読みます。Phase報告の旧記述は実コードと後続evidenceで再確認し、最新仕様として無条件に採用しません。

- [XLSX Print Layout](docs/xlsx-print-layout.md) / [Multi-page OCR foundation](docs/multi-page-ocr.md)
- [Reference Renderer Qualification](docs/reference-renderer-qualification.md) / [Geometry Calibration](docs/geometry-calibration.md)
- [Renderer Environment Qualification](docs/renderer-environment-qualification.md) / [Renderer Sandbox Closure](docs/renderer-sandbox-closure.md)

## 10. テンプレート手動編集の拡張

多角形・見出し階層・仮想見出し・同名項目の連番化は
[テンプレート手動編集の拡張](docs/template-authoring-extensions.md)を参照します。
多角形のmaskは新しい多角形だけに適用し、矩形のcropとsnapshot形式を維持します。
見出しの整理で既存の意味づけ・targetPath・結合・IDを再生成しません。
別形式のAI汎用定義への変換やページ単位編集で、新metadataを黙って落とさないでください。
