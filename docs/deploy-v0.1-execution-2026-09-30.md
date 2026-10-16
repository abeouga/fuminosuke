# v0.1 改修実行記録

実行開始日: 2026-09-30
最終確認日: 2026-10-01
対象: `C:\Users\hanam\Desktop\llm-ocr-deploy`
実行指示: [フェーズ実行指示書](deploy-v0.1-refactor-phases-2026-09-30.md)
採否: [チェックリスト](review-decisions-2026-09-29.md)

## 開始時点

- ユーザーの実行指示により、実装前の制限を解除し、デプロイ用コピーで実行を開始した。
- 元の `llm-ocr` は編集しない。開始時の両フォルダの追跡・非ignoreファイルのSHA、status、既存diff、対象コードのコピーを `D:\llm-ocr-archive\deploy-refactor\20260930-224115` に保存した。
- `backend/.env` は許可済みのコピーを保持する。テストでは開発用の実API設定・DBを利用しない。
- `C:\Users\hanam\Desktop\テンプレート用` にPNG 4件を確認した。今回の固定fixture検証のために内容や原本を変更しない。
- frontend依存は既存lockfileから `npm ci --no-audit --no-fund` で導入した。Python 3.12は元フォルダのvenvの実行ファイル・既存依存を読み取り利用し、import先はデプロイ側backendに固定する。

## Phase 0: baseline

- 対象frontend検証: `npm test -- App.test.tsx OcrPage.test.tsx TemplatePage.test.tsx LayoutEditor.test.tsx`。
- 変更前: 219件中194成功、25失敗。パターン一致により `OcrImagePicker.test.tsx` も含まれる。
- 失敗は既存画面変更後の旧「画像の準備へ」「vNを選択」「読み取り」「書類を読み取る」等のselectorと、削除済みホーム表示の期待が中心。現行の利用者操作へ照合して検証方法を更新し、意味上のassertionは保持する。失敗を隠す削除・skipはしない。
- navigation guardは常時false、プレビューはEscapeのみでTab制御なし。新規作成した版の承認後、古い親propsで系列を探索するコードが残っている。
- previewは一覧の全sourceJobIdへ無制限に取得し、一覧identity更新でも再取得する。変更前のrequest数を固定条件で記録してから遅延取得を実装する。
- OCR契約scriptは相対basetempを使用していたため、対象・assertionを維持したまま、未使用の絶対Dドライブパスを必須引数にする。OCRコードの移動前baselineから利用する。

## Phase 1: 設計ゲート

[クラウド運用の設計ゲート](deploy-v0.1-cloud-design.md) に未決事項と推奨条件を整理した。設計ゲートとして達成。認証・共有DB・保持期間・deployment設定は未決であり、採用決定・本番配備は実施していない。

## フェーズ達成状況

| Phase | 状態 | 確認内容・未決事項 |
| --- | --- | --- |
| 0 | 達成 | 開始差分・症状・変更前OCR225件・preview取得回数を記録 |
| 1 | 達成（設計メモ） | 採用決定・クラウド実装は別途必要 |
| 2 | 達成 | 選択・送信・離脱後応答・承認再取得・focusを検証 |
| 3 | 達成 | 一覧0回、選択1回、cache・失効・直列化を検証 |
| 4 | 達成 | 既存115件/101件と、移動91宣言の同一性を確認 |
| 5 | 達成 | UI/helper分離、最終frontend503件とbuild成功 |
| 6 | 達成 | OCR契約225件成功。backend全体検証と既存テストの呼出し不整合の修正を下記に記録 |
| 7 | 達成 | 最終差分・元フォルダのSHA・一時検証ファイルの整理を確認 |

## Phase 2: 操作上のデータ保護

- 選択した画像、送信中、受付済みjob、ページ入力の状態をAppの既存navigation guardへ接続した。移動取消では入力を保持し、明示的に破棄した場合だけrootの一時入力を初期化する。
- 同じテンプレートの選択画面へ戻った場合は画像を保持する。異なるテンプレートへ切り替える際は破棄を確認する。画像未選択では不要な確認を出さない。
- 送信後に移動し、旧受付応答が後着する再現ケースを追加した。単一ページは世代を照合して新しい入力を上書きせず、複数ページはunmount後でも受付済みjobを復帰用に記録する。サーバーのjobを暗黙にcancelしない。
- job完了後の結果取得応答が移動後に失敗し、旧レビューを開くケースも変更前に再現した。離脱後の成功/失敗callbackとretry応答による一時state更新を世代で制限した。
- 承認後はreviewが保持するtemplateIdでdetail APIを取得し、要求版がACTIVEかつactiveVersionIdと一致し、他のACTIVEがないことを検証する。一覧取得失敗でも確認済みACTIVEを保持する。
- 承認POST成功後にdetail取得できない場合は、未確認状態とGETだけの再取得導線を表示する。承認POSTを自動再送しない。
- 単一/複数ページの拡大見本に共通focus管理を適用した。初期focus、Tab/Shift+Tab、外部focusの回収、Escape、閉じた後のtrigger復帰を検証した。

API payload、OCR値、確定判定、job cancel/retry要求の意味は維持した。navigationは一時入力の保護を扱う。

## Phase 3: 見本画像の取得

固定条件は8テンプレート、mock artifact APIの20ms遅延、選択→同じ一覧の再取得→選択版のsourceJobId変更。

| 時点 | 変更前の累計request | 変更後の累計request |
| --- | ---: | ---: |
| 一覧の初期表示 | 8 | 0 |
| 1件選択 | 8 | 1 |
| 同じ一覧を再取得 | 16 | 1 |
| 選択版のsourceJobId変更 | 24 | 2 |

- cache keyはversionIdとsourceJobId。選択された見本だけ取得し、同時取得数は1とする。
- 取得中に選択が連続変更された場合、開始前の旧選択は送信せず、実行中の古い応答は現在の選択を上書きしない。
- 欠落/取得失敗は既存の見本placeholderとし、OCR admissionの成功に置き換えない。
- 変更前の見本表示計測は113ms、変更後の代表実行は74ms。ただしmock/UIテスト内の値であり、実サーバーや実利用の時間短縮を保証しない。確定した改善は不要requestの削減である。

## Phase 4 / 5: frontend責務分離

| owner | 分離した責務 | 維持した境界 |
| --- | --- | --- |
| LayoutEditor | geometry、meaning、records、merges、drawingのhelper、記号/選択肢editor | 元のstate、自動保存、下書き復元、revision、layout保存・ID生成経路 |
| OcrPage | template選択UI、見本cache、mapping取得、job監視/retry、進捗表示 | file/version/step/jobのcontainer所有、受付payload、Appへのprops契約 |

`layoutAuthoring.ts` / `headingConnections.ts` の既存helperを利用し、複製しない。LayoutEditorから移動した91宣言は、export/importと空白を除くtoken比較で一致した。純粋関数分離の後に既存115件、記号editor分離の後にLayoutEditor101件を確認してから次へ進めた。

行数の変化（import/reexport・facade行も含む）:

| file | 開始時 | 分離後 |
| --- | ---: | ---: |
| LayoutEditor.tsx | 7,655 | 4,671 |
| OcrPage.tsx | 955 | 623 |
| ocr_result_service.py | 2,030 | 457 |

LayoutEditorはstateと描画面のownerとして大きさが残る。今回移動したhelperは挙動を変えず、全stateの再設計・形式一般化は行っていない。

## Phase 6: 移動前の境界確認

HTTP callerは `backend/app/api/v1/routes/ocr.py`、Sheet編集callerは `backend/app/services/google_sheets_edit_worker.py`。route・依存注入・workerは変更しない。次の所有境界を維持して、read/preview → lifecycle → spreadsheet → exportの順に移動する。

| public method | session / commitの所有者 | 既存の副作用・失敗境界 |
| --- | --- | --- |
| get | service内の読取session | 状態確認、response/preview、既存同期状態の照会。未準備・paged拒否 |
| correct | service内のmutation session、repository.save_correctionのrevision CAS / commit | 補正再検証・履歴保存の後、既存条件下だけSheets同期。競合409 |
| resolve_document | mutation session、documents.resolveの後にcommit | 保存先と補正結果の対応。未確認状態以外を拒否 |
| reopen | mutation session、job状態更新後commit | 確定snapshotの存在を検査し、補正値とrevisionを維持。既存条件下で外部同期 |
| confirm | mutation session、補正はcommit=False、snapshot/index/document更新の後にcommit | ファイル生成は既存のcommit前位置。commit後にDrive/Sheets同期。同期失敗でローカル確定を戻さない |
| apply_spreadsheet_edit | mutation session、revision更新・event・必要な再確定snapshotの後commit | event ID/所有者/前値の競合保護。同期はsessionを離れてから。失敗event保存位置も維持 |
| sync_spreadsheet | 読取session → 既存外部service → response再取得session | operation IDと所有者の検査、名前同期retry、ローカル確定状態の保持 |
| rename_spreadsheet | 読取・XLSX生成 → Drive rename → 再検査session / commit | 出力先変更を拒否。名称commit後に既存XLSXを書込む |
| document_id / spreadsheet_status | 既存の読取session | 単一ページ・job種別・Google所有者の検査。既存disabled意味を維持 |
| export_path | paged検査sessionと確定読取session | 確定revision・固定bindingから生成済み出力へ。未確定・未対応形式拒否 |
| excel_table | 確定読取session | 既存Excel payload、未確定・paged拒否 |

facadeのmethod signature、constructor、静的/class method呼出しを保つ。分離先はfacadeが持つ依存を受け取り、既存method bodyをそのまま実行する。facade wrapperはsession・commit・catchを追加しない。段階ごとに既存の短い検証を実行し、最終的に同じOCR契約225件とbackend full testsを実行する。

### 実装と比較

- facade以外にpreview、lifecycle、spreadsheet、exportの4moduleとtypesを追加した。確定レコードの索引保存はlifecycleへ配置し、previewは読取・表示構造を扱う。
- 37 methodを移動した。全43 methodのsignature・decorator・引数転送、constructor、class属性、2つのdataclassを開始時ASTと照合した。移動bodyは同一。旧classへの実行時参照を持つ2関数だけ、循環importを避ける局所importを追加した。
- 既存route、DB schema、repository、provider、model、prompt、schema、association、crop、XLSX出力bindingは変更していない。既存Google同期は互換動作を構造上分離したもので、v0.1の提供機能へ追加していない。
- 索引保存の配置を整える過程でimport不足により3件が失敗した。元のDecimal/InvalidOperation/unicodedata依存を移動先へ追加し、テストの期待値を変更せずに修正した。
- 最終構成のOCR契約検証は225件成功（50.16秒）。変更前225件（54.21秒）と同じ対象・fixture/期待値を使用している。時間の差を性能改善の証明とはしない。
- 最初のbackend full runは最終配置の調整前に中断した。部分的な成功を全件成功へ数えず、最終構成で全2,610件を再実行した。
- 最終backend全体検証は2,608成功、1失敗、1skip（37分52秒）。唯一の失敗は今回未変更のJobRunnerに対し、既存テストが必須引数 `stack_trace` を渡していなかったことによるTypeError。開始時SHAと原フォルダとの一致を確認し、今回のservice分離による回帰と区別した。
- テストの呼出しに固定の `stack_trace` を補い、CANCELLED・error_code・error_message_safeのassertionを維持した。JobRunner製品コードは変更せず、該当ファイルの4件が成功した。最後の変更はテストの呼出しだけなので、全体検証は繰り返していない。「最終full runが全件成功」とは扱わない。比較根拠は `job-runner-baseline-comparison.json`。

## Phase 7: 検証結果

artifact root: `D:\llm-ocr-archive\deploy-refactor\20260930-224115`

| 検証 | 結果 | log |
| --- | --- | --- |
| frontend baseline対象 | 194成功 / 25既存失敗 | baseline-frontend.log |
| Phase 2操作・承認・focus | 127成功 | phase2-targeted-final.log |
| Phase 3 request/cache/世代 | 38成功 | phase3.log |
| LayoutEditor helper分離 | 115成功 | phase4-pure.log |
| LayoutEditor editor分離 | 101成功 | phase4-components.log |
| OcrPage分離 | 53成功 | phase5.log |
| 遅延受付・離脱保護 | 24成功 | navigation-pending-final.log |
| 遅延結果応答・既存読み取り | 56成功 | navigation-review-green.log |
| backend preview | 12成功 | phase6-preview.log |
| backend lifecycle | 14成功 | phase6-lifecycle.log |
| backend spreadsheet | 61成功 | phase6-spreadsheet.log |
| backend export | 64成功 | phase6-export.log |
| OCR契約・変更前 | 225成功 | ocr-before.log |
| OCR契約・最終構成 | 225成功 | ocr-final-2.log |
| frontend full | 503成功 / 40 files | frontend-full-complete.log |
| frontend build | 成功 | frontend-build-complete.log |
| ruff・変更moduleとscript | 成功 | ruff-final.log |
| mypy・変更6module、依存はsilentで対象を限定 | 成功 | mypy-final.log |
| backend full | 2,608成功 / 既存テスト呼出し不整合1失敗 / 既存opt-in 1skip | backend-full-final.log |
| JobRunnerテストの呼出し修正後 | 4成功。全体検証の再実行はなし | job-runner-final.log |
| git diff --check | 成功 | diff-check-final.log |

ビルドのJS bundleは690.90 KB（gzip 208.80 KB）で、Viteの500 KB警告は残る。bundle分割は今回の計測・採用対象へ含めていない。

### コマンドと実行環境

PowerShellで、Python実行ごとに以下を指定した。

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONPATH = 'C:\Users\hanam\Desktop\llm-ocr-deploy\backend'
$py = 'C:\Users\hanam\Desktop\llm-ocr\backend\.venv\Scripts\python.exe'
# 作業ディレクトリ: llm-ocr-deploy\backend
& $py -B ..\scripts\verify_ocr_contract.py --basetemp='D:\llm-ocr-archive\deploy-refactor\20260930-224115\ocr-final-2'
& $py -B -m pytest tests -vv --tb=short --durations=8 --basetemp='D:\llm-ocr-archive\deploy-refactor\20260930-224115\backend-full-final'
& $py -B -m pytest tests/unit/test_job_runner.py --basetemp='D:\llm-ocr-archive\deploy-refactor\20260930-224115\job-runner-final'
& $py -B -m ruff check app/services/ocr_result_service.py app/services/ocr_result_preview.py app/services/ocr_result_lifecycle.py app/services/ocr_result_spreadsheet.py app/services/ocr_result_export.py app/services/ocr_result_types.py ../scripts/verify_ocr_contract.py --no-cache
& $py -B -m mypy app/services/ocr_result_service.py app/services/ocr_result_preview.py app/services/ocr_result_lifecycle.py app/services/ocr_result_spreadsheet.py app/services/ocr_result_export.py app/services/ocr_result_types.py --follow-imports=silent --cache-dir='D:\llm-ocr-archive\deploy-refactor\20260930-224115\mypy-cache'
# 作業ディレクトリ: llm-ocr-deploy\frontend
npm.cmd test
npm.cmd run build
```

これは実行記録である。記載basetempは既に使用済みであり、再実行では未使用の別パスを指定する。full実行では `RUN_OPENAI_INTEGRATION_TESTS=false` とし、既存の実API opt-inを有効化していない。

### 今回追加・変更したファイル

開始時の既存差分と今回の差分は、deploy-before.jsonと最終manifestで区別する。今回の実装は以下を対象とする（開始時からの未コミット変更を含むfileでも、その既存部分は保持）。

- App/操作: `frontend/src/App.tsx`、`App.test.tsx`、`pages/OcrPage.tsx`、`OcrPage.test.tsx`、`OcrPageSafety.test.tsx`、`pages/TemplatePage.tsx`、`TemplatePage.test.tsx`。
- ページ入力: `frontend/src/components/OcrPageBindings.tsx`、`OcrPageBindings.test.tsx`。
- LayoutEditor: `frontend/src/components/LayoutEditor.tsx`、`LayoutEditor.test.tsx`、`layoutEditorGeometry.ts`、`layoutEditorMeaning.ts`、`layoutEditorRecords.ts`、`layoutEditorMerges.ts`、`layoutEditorDrawing.ts`、`LayoutMarkEditors.tsx`。
- OcrPage UI: `frontend/src/components/OcrTemplateSelector.tsx`、`OcrJobProgressPanel.tsx`。
- hooks: `frontend/src/hooks/useTemplatePreviews.ts`、`usePreviewDialog.ts`、`useOcrMappingPreview.ts`、`useOcrJobProgress.ts`、`useOcrJobProgress.test.tsx`。
- OCR結果service: `backend/app/services/ocr_result_service.py`、`ocr_result_preview.py`、`ocr_result_lifecycle.py`、`ocr_result_spreadsheet.py`、`ocr_result_export.py`、`ocr_result_types.py`。
- 既存テストの呼出し整合: `backend/tests/unit/test_job_runner.py`。必須引数だけを補い、検証条件は維持。
- 検証script: `scripts/verify_ocr_contract.py`。
- 文書: `README.md`、`ARTIFACT_STORAGE_POLICY.md`、`docs/ocr-safety-contract.md`、`deploy-v0.1-refactor-phases-2026-09-30.md`、`deploy-v0.1-cloud-design.md`、本記録。

frontend package.json/lockfile、backend依存、DB schemaは変更していない。backendテストは追加せず、既存1件の呼出しを上記のとおり修正した。

### 保持・整理と検証限界

- 有効な既存テストと再現可能な回帰テストは保持する。古い画面selector・期待文言を実コードへ照合して更新した。テスト削除・skip追加・assertion弱体化・backend fixture変更で失敗を隠していない。
- 一時分割script、構文比較probe、移動bodyの全量dump、最終manifest比較scriptの計12件を、比較結果の記録後に除去した（probe-cleanup.json）。開始時snapshot、差分、比較結果JSON、検証logはDドライブへ保持する。
- 原フォルダの開始SHA対象967件は一致した。開始一覧に含まれなかった日本語名の既存出力3件は、Git HEADおよびdeployコピーと一致し、更新日時も作業前である。比較結果はworkspace-comparison.json / source-output-check.json。
- backend/.envは原本とSHA一致。内容は表示していない。開発用DB・ユーザーのテンプレート/画像原本は変更していない。staging、commit、push、reset等は実行していない。
- OpenAI/Googleの実API呼出しは0件。実帳票4PNGのOCR、実ブラウザーE2E、クラウド配備は実行していない。
- association mismatch率、mapping誤対応率、認識文字の誤り率、実処理時間・料金は未実測であり、0件/0%とは報告しない。
- method/宣言比較と固定fixtureによる回帰確認の範囲で、既存OCR contractを維持した。実帳票の精度、全Golden replay、レンダラーの本番資格、クラウド運用開始の証明にはならない。
- 次の本番配備判断は、設計ゲートの認証・利用者間分離・保存/削除時点・運用主体の決定に依存する。
