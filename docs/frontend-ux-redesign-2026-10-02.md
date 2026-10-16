# フロントエンドUX再設計

対象: llm-ocr-deploy。実装モデル: GPT-6.1 Sol Max。
目的: 帳票を大きく表示し、認知負荷を抑えながらv0.1のテンプレート準備・OCR・確認補正・確定済みXLSX取得とデータ契約を維持する。

2026-10-02の追加指示: Google認証、保存先設定、持ち出しバックアップ、開発情報、書類ライブラリ、検索をデプロイ版の画面から除去する。既存クラウド設計書のv0.1範囲とも一致する。上部メニューはホーム・テンプレートだけにする。

## 操作方針

- 帳票を主領域、選択した欄の設定を隣接領域にする。通常操作と補助設定を分ける。
- 主要ボタンはアイコンと動詞を併記する。実行・選択中・補助・破壊操作の見た目を区別する。
- 選択→設定→確認の順を画面に表し、前の工程・次の工程と保存状態の位置を統一する。
- 意味づけは「項目名から記入欄へつなぐ」「記入欄を選んで保存先を指定する」の2経路を案内する。未設定欄の巡回、現在の対象、保存先の行・分類を分かるようにする。
- 欄・mapping・targetPath・revision・保存キューの生成や採用規則を変更しない。追加指示で除外された機能以外の既存操作を維持する。

## 確認した問題

1. 編集画面では説明、状態表示、5工程の見出し、全補助操作が縦に並び、帳票が初期画面の下部に押し出される。
2. 要対応パネルが固定表示され、戻る操作と説明に重なる。
3. 主操作と文字配置・拡大鏡の調整が同列に並び、作業対象を選ぶ前から判断が多い。
4. 操作モードの選択状態が見た目中心で、ボタンのaria-pressedが不足している。
5. 意味づけの保存先一覧には同名の項目が並ぶ。選択値は変えず、行・分類などの表示で区別する必要がある。
6. ホームは大きい説明領域が中心で、作業開始と準備の関係が分かりにくい。

## フェーズと完了条件

- [x] 1. 共通操作基盤: ボタンの役割・アイコン・フォーカス表示、上部2項目のメニュー、XLSX取得へ進むホームを実装。追加指示前の折りたたみメニューは撤去し、旧Google認証・検索・バックアップのUIテストを対象外仕様として廃止。Appの現行11件で画面遷移と不要APIを呼ばないことを確認。
- [x] 2. テンプレートと意味づけ: 帳票中心の配置、工程・主操作・補助操作の階層、選択対象に応じた案内、保存先の識別、未設定欄の巡回を実装。App・LayoutEditor・TemplatePageの175件とbuildが成功。実画面で表示設定、拡大鏡、補助操作、10行の同名保存先の識別を確認。
- [x] 3. 読取・レビュー・出力: 入力→実行→確認→保存/確定の前後関係、アイコンと操作位置を統一。Googleへの遷移・再同期・出力先設定を除去し、確定後のXLSX取得へ集約。変更対象10ファイルの191テストが成功。単一・複数ページの補正・確定表示をブラウザーでも確認した。
- [x] 4. 回帰確認: frontend全40ファイル・499テスト、buildが成功。幅390pxと1440px、通常表示1046pxで確認。メニューと書類名欄の幅崩れを修正した。保護領域277ファイルのハッシュに変更なし。

各フェーズの受入条件を確認してから次へ進む。追加指示で除外した機能以外を到達不能にした場合、保存・対応関係が変化した場合、既存テストを弱めないと通らない場合は未完了とする。

## 初回の変更・検証境界（実API検証前）

- 開始時のfrontendとGit差分、保護領域のハッシュを `D:\llm-ocr-archive\ux-redesign\20261002-frontend-v1` に保存済み。
- backend、API型、OCR計算、mapping生成を変更する予定はない。UIの制御・選択・表示と、既存ハンドラーへの導線を対象とする。
- UI確認で既存の「テンプレートを編集」から編集用のv2下書きが作成された。終了時に268個のfork用ID対応を置換したレイアウトが公開版と完全一致することを確認し、既存の却下APIでこの検証用下書きのみを終了した。公開版v1を再び選択し、画像準備へ進めることを実画面で確認。公開版と確定済み結果は変更していない。
- APIの上限は今回100回。実OpenAI/Google呼出しは0回。
- 一時的な検証スクリプトは作業終了時に除去し、必要な回帰テストと結果artifactを保持する。

## 最終変更ファイル

| 対象 | 変更内容 |
| --- | --- |
| `frontend/src/App.tsx`、`pages/HomePage.tsx` | ホーム・テンプレートの2項目に整理。Google認証、検索、ライブラリ、保存先設定、開発情報、バックアップの入口と関連ポーリングを除去 |
| `frontend/src/workbench.css`、`main.tsx` | 帳票の表示領域拡大、主操作と補助操作の階層、フォーカス表示、狭い画面の配置 |
| `pages/TemplatePage.tsx`、`components/LayoutEditor.tsx` | 要対応欄の折りたたみ、工程の現在位置、未設定欄の巡回、同名保存先の行番号表示、選択した欄の案内。Google出力先・配布パッケージの入口を除去 |
| `pages/OcrPage.tsx`、`hooks/useOcrJobProgress.ts`、`components/OcrTemplateSelector.tsx`、`OcrJobProgressPanel.tsx`、`OcrPageBindings.tsx` | 読取の前後操作とアイコン。処理完了後はアプリ内レビューへ移動 |
| `pages/ReviewPage.tsx`、`PagedReviewPage.tsx`、`components/OcrReviewResults.tsx`、`ExcelExportActions.tsx` | 画像と表を広く表示。保存・確定・再編集・XLSX取得の導線を整理。Google再同期、開発情報、コピー出力を除去 |
| 上記に対応する既存テスト10ファイル | 新しい入口と表記へ更新。未設定欄巡回・同名項目の区別・除外API非呼出しを検証 |

変更はfrontend25ファイルと本書。今回の開始時からの差分正本は、証跡ディレクトリの `ux-only-final.diff` と `ux-files.json`。既存の未コミット差分は保持した。

## 最終検証と証拠の区別

- `npm test`: **40ファイル、499件成功**。補正保存、競合時の入力保護、再取得、確定・再編集、ページごとの画像・mapping対応、確定版のXLSX取得要求を含む。
- `npm run build`: **成功**。JS 582.48 kB、gzip 177.89 kB。500 kB超のchunk警告は残る。今回の作業でbundle分割は行っていない。
- `git diff --check`: **成功**。開始時・終了時のstatusと今回のみの差分を記録した。
- 最初の全体実行では、警告欄を開く操作の不足、旧ラベル・DOM隣接位置の期待を新仕様へ更新した。対応関係のassertionは維持。別途MaskEditorの非同期dirty通知のassertionが1件失敗したが、製品・テストとも無変更の単独再実行と最終全体実行では再現しなかった。タイミング依存の可能性は残る。初回失敗ログも保持。
- 実アプリ: 5180/8010で稼働。ホーム、テンプレート選択、画像準備、意味づけの選択・表示設定・拡大鏡を確認。幅390pxで横はみ出しなし。通常幅1046pxの意味づけ画面では帳票幅約680px、上端約428px。
- レビューの実ブラウザー確認は**検証用レスポンスと合成帳票画像**を使用。単一ページの値103の保存・確定、複数ページの値111の保存・別ページへの移動・全ページ確認前の確定拒否・確定後の保持・再編集を確認。実OCRの成功を証明するものではない。XLSXバイナリーの実ダウンロードは今回のブラウザー確認では行っていない。
- backend/app、frontendのAPI実装・型を含む**277ファイルが開始時ハッシュと一致**。OCR provider、prompt、schema、mapping生成、crop生成、保存payload、revision/確定規則の変更はない。既存OCR contractを維持した。backend全テスト・実OCRは今回再実行していない。
- SysOverRayの設定 `EnableGoogleIntegration=false` を確認。起動時のGoogle OAuth / Sheets無効化設定はそのまま使用する。クラウドへの公開・デプロイは今回行っていない。
- 一時ブラウザー検証用の `__ux-check.html` / `__ux-check.tsx` は証跡側に保存してfrontendから除去。意味のある回帰テストと実行ログは保持する。

### 証跡

`D:\llm-ocr-archive\ux-redesign\20261002-frontend-v1`

- `frontend-full-final-tests.log`、`frontend-final-build.log`
- `protected-check-final.json`、`draft-cleanup-check.json`、`draft-cleanup-result.json`
- `template-meaning-desktop.jpg`、`review-desktop.jpg`、`review-mobile.jpg`、`paged-review-desktop.jpg`、`paged-review-mobile.jpg`
- `browser-verification.json`、`ux-only-final.diff`、`ux-files.json`

## 追加実施: 実APIでのレビューUX確認（2026-10-02〜03）

この節が追加実施分の現状です。上記の499テスト・実API 0回・backend変更なしは初回分の記録です。

### 製品方針

特定の牛繁殖台帳に限定しません。書類の識別情報は、利用者が書類を見分けるための番号・名称・日付などの組合せです。DBの主キーのような一意性を保証するものではありません。「この書類で間違いありませんか？」の確認を残し、受付時のテンプレートで指定された項目名・型・画像を表示します。

Google認証、保存先設定、持ち出しバックアップ、開発情報、書類ライブラリ、検索の入口は設けません。上部メニューは「ホーム」「読み取り」です。テンプレートの追加・編集は読み取り画面内から行います。ホームには直前の確定結果だけを表示し、XLSXへ戻れるようにしています。

### 完了した調整

- [x] 識別情報: 固定の牛名・10桁入力を全書類へ要求せず、受付snapshotにある識別項目を表示。旧牛用テンプレートでサーバーが既に要求する個体識別番号の確認は維持。識別設定の取得失敗時は入力を保持して再取得し、対応不明の欄を推測で補わない。
- [x] 意味づけ・行列設定: 「設定内容プレビュー」に設定済みの列、行、保存先、接続の欠落を表示。押すと既存の物理セルを選択。旧設定で列ラベルがない場合も、保存済みの行と項目を表示する。プレビューからmappingを生成しない。
- [x] 結果表示: 原画像と結果表、選択中の表・行・項目を対応させ、横に続く列への操作を追加。長文の改行、Enterで入力反映、Shift+Enterで改行、Escapeで取り消しに対応。OCR候補を「問題なし」と断定しない。
- [x] 保存と復旧: 未保存の入力だけを保護し、保存中に行った追加修正を応答で上書きしない。セル入力中の離脱も検知。確定失敗時はサーバーを再取得し、同じrevision・内容・書類名で確定済みなら確定表示へ復旧する。内容が異なる場合は入力を退避して競合復旧へ進む。
- [x] 確定と出力: ブラウザーのconfirmからアプリ内の確認ダイアログへ変更。再編集は画面内で行い、再読み込みで勝手に再編集しない。XLSX取得エラーは結果画面に残して再試行可能にし、確定済み書類名をダウンロード名に使用。
- [x] 不要な操作: テンプレート内の検索も除去。複製に失敗した理由は自動消去する通知だけにせず、閉じるまで残す。
- [x] 実画面: 単一ページで識別確認、手動修正、保存、確定、再編集、再確定、再読み込み、XLSX取得を実行。通常幅1265pxと幅390pxを確認。

### 変更境界とファイル

frontend22ファイル、backendのルート1ファイル・そのintegration test1ファイル、本書を変更。正確なfrontend一覧と今回のみの差分は、追加証跡の `ux-current-files.json` / `ux-current-only.diff` に保存しています。

| 対象 | 今回の変更 |
| --- | --- |
| `App.tsx`、`HomePage.tsx` | 再編集URLの一度だけの消費、初期接続待ち、直前の確定結果 |
| `ReviewPage.tsx`、`PagedReviewPage.tsx`、`OcrReviewResults.tsx` | 識別確認、結果の行列表示、入力保護、保存・確定・再編集のUI状態 |
| `LayoutEditor.tsx`、新規`LayoutAssignmentPreview.tsx` | 意味づけと行列設定の表示プレビュー。保存・mapping生成の規則は変更なし |
| `OcrPage.tsx`、`OcrTemplateSelector.tsx`、`TemplatePage.tsx` | 検索の除去、複製エラーの保持、利用者向け表記 |
| `ExcelExportActions.tsx`、`workbench.css` | XLSX取得と失敗表示、確定書類名、表示幅・操作領域の調整 |
| `backend/app/api/v1/routes/ocr.py`、`frontend/src/api.ts` | 読み取り専用の`GET /ocr-jobs/{job_id}/review-context`を追加 |
| 対応テスト | 実際に確認された保存競合・確定応答・取得失敗の回帰と汎用識別の確認。MaskEditorはuseEffectによる保存完了通知を既存waitFor内で待つよう修正し、期待値は維持 |

backend追加の理由: 既存result APIだけでは受付時の識別項目定義を取得できず、frontendだけで推測すると新しいテンプレート設定や牛用の固定設定へ誤って依存するためです。新GETは受付snapshotの識別定義と既存の牛用確定条件を表示用に返します。現在のテンプレートからmappingを補充せず、OCR、補正、確定、出力のserviceは変更しません。

保護対象277ファイル中275ファイルは開始時と同一ハッシュ。差分は上記ルートとAPIクライアントのみです。provider、prompt、model設定、schema、crop、association、mapping生成、補正payload、revision、出力セル対応を変更せず、既存OCR contractを維持しました。元の`llm-ocr`には編集していません。

### 実OCRの結果

- 実OpenAI呼出しは合計**7回**（2ジョブ、2回＋5回）。Google APIは0回。実OCRモデルは既存設定の`gpt-6-luna`。コーディングモデルの指定によってOCRモデルを変更していません。
- 原テンプレート「牛繁殖台帳004」の名号欄が母の項目へ結び付いている既存設定を確認。原テンプレートは変更せず、最後に取得したテンプレートJSONが開始時と完全一致することを確認。最初の結果は未確定のままです。
- 検証用コピー「UX検証用・台帳の識別と列確認」を作成し、通常のUIで名号の意味づけ、名称・生年月日の識別、10行×7列の設定、画像との対応確認、有効化を行いました。自動複製の有効化は原テンプレートの識別未設定により409となったため、下書きコピーで準備しています。既存guardの解除はしていません。
- 検証用コピーの実結果は72個のcrop、validation `valid=true / issues=[]`。これは文字列の完全一致を意味しません。画像との照合で6項目を修正し、補正revision 3を確定しました。
- 6項目の`sourceRegionId` / `boundingBox`と72件のfield preview bindingは補正前後で一致。修正した6値、名号、識別番号の計8セルを実XLSXと照合しました。
- ブラウザーイベントで`UX検証_こよみ.xlsx`、7,097 bytesのダウンロード完了を確認。Playwrightのdownload待機はこのIABでタイムアウトしたため、同じブラウザーのCDPダウンロード完了イベントを保存しています。内容確認用ファイルは同じ出力APIから別途取得しました。
- 確定APIが409でもサーバーは既に確定済み、画面は未確定という状態を実際に確認。その復旧を追加し、409と通信失敗の両方を短いテストで固定しました。

### 最終検証

| 検証 | 結果 |
| --- | --- |
| `npm test` | 41ファイル、**507件成功**。全E2E実行ではなくfrontendの全unit/componentテスト |
| `npm run build` | 成功。JS 594.61 kB / gzip 181.54 kB。500 kB超のchunk警告は残る |
| `pytest tests/integration/test_review_context_api.py tests/integration/test_jobs_api.py --basetemp=D:\llm-ocr-archive\pytest-work\20261003-review-context-final-05` | **13件成功** |
| `ruff check app/api/v1/routes/ocr.py tests/integration/test_review_context_api.py` | 成功 |
| `mypy --follow-imports=silent app/api/v1/routes/ocr.py` | 変更したルート1ファイルは成功 |
| `git diff --check` | 成功 |
| 保護領域・原テンプレート | 既存core275ファイル不変、原テンプレートJSON不変 |

import先まで追うmypyでは、変更していない8ファイルに56件（Windowsで未定義のPOSIX属性、openpyxl stub等）が出ました。全backendの型検査成功とは扱いません。backend full tests、OCR contract専用スクリプト、実複数ページOCRは再実行していません。OCR coreのコードは変更していません。

使い捨てのUI検証コードは製品ディレクトリへ残していません。不要になった確認ダイアログのmockは除去。意味のある回帰テストと失敗を含む実行ログは保持しています。

### 残る制約

以下は2026-10-03の検証時点の制約です。項目1の画像帳票XLSX出力は
[2026-10-04の汎用化](generic-document-generalization-2026-10-04.md)で改修しました。
新規画像テンプレートの初期設定と識別判定も同記録を参照してください。
旧牛用データの自動移行および実複数ページOCRの追加検証は行っていません。

1. **旧画像テンプレートのXLSXは完全には汎用化されていません。** `ocr_result_export._render_xlsx`が原本への出力を使えない場合、既存`ExcelExportService`の牛用固定項目＋追加項目一覧へフォールバックします。今回の7列の画面が、そのまま同じ7列のXLSXになるわけではありません。実ファイルで確認した残課題です。
2. 旧牛用テンプレートの個体識別番号条件も既存backendの条件を維持しています。汎用識別画面の実装と、既存テンプレートや保存済みデータの移行は別です。請求番号の英数字入力はcomponentテストで確認しましたが、今回の実API入力は牛の帳票1形式です。
3. 複数ページのUIは既存のfixtureとテストを使用。今回、実複数ページOCR・全ページXLSX取得の通し確認はしていません。全書類形式の正しさやOCR精度の正式認定を示す検証ではありません。

残るXLSXの汎用化は、固定見出しの置換だけでは完了しません。受付時の`reviewStructure` / mappingから明示的な行・列・個別項目を出力し、空欄、同名項目、補正revision、元XLSX出力経路を保持する必要があります。AGENTS.md §5・§7とSafety Contractに従い、同一保存responseでの前後比較、出力対応の境界テスト、contract検証とbackend full testsを伴う作業として扱います。今回のUI調整に紛れて出力対応を変更していません。

### 追加証跡

`D:\llm-ocr-archive\ux-redesign\20261003-live-review`

- `frontend-final.log`、`build-verified.log`、`backend-focused-verified.log`、`mypy-focused.log`
- `live-verification-final.json`、`browser-download-events.json`、`downloaded-live-result.xlsx`
- `protected-check-final.json`、`original-template-check.json`、`ux-current-only.diff`、`ux-current-files.json`
- `identity-final-desktop.jpg`、`review-final-desktop.jpg`、`review-final-mobile.jpg`、`review-selected-field.jpg`
