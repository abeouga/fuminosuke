# 汎用帳票への変更記録 — 2026-10-04

> **旧段階の記録です。** この後、旧形式の互換性を廃止し、既存データをschema 2.0へ変換しました。
> 以下の互換処理・旧モジュール・旧テスト件数は現在の仕様ではありません。
> 現在の仕様と証拠は [互換廃止・汎用変換記録](generic-cleanup-2026-10-04.md) を参照してください。


## 対象と結論

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy` です。元の `llm-ocr` は編集していません。
新規画像テンプレート、書類の識別条件、画像帳票のXLSX出力を汎用化しました。
変更範囲の検証は完了しました。開始していたバックエンド全件実行は、追記の最小検証方針に
合わせて停止しました。全件成功とは扱いません。

2026-10-04の追記で、汎用化・最適化に必要な破壊的変更が許可され、
ユニットテストを極力避ける方針が指定されました。`AGENTS.md` §1.1とSafety Contractの
契約変更手順へ反映しました。追記前に追加したXLSXの境界2ケースは保持し、
以後の検証は保存response・API・出力照合を優先しています。

帳票の識別は、利用者がテンプレートに設定した番号・名称・日付等の確認として扱います。
すべての帳票へ個体識別番号や10桁制約を要求しません。確認・補正・明示的な確定を経て
XLSXを取得する流れを維持します。Google認証・保存先設定・持ち出しバックアップ・
開発情報・書類ライブラリ・検索を再導入していません。

## 修正前に確認した問題

1. 新しい画像テンプレートが `cow-breeding-ledger` として作成され、牛用configのregions・
   marksを引き継いでいました。汎用テンプレートを再処理しても牛用設定へ戻る経路がありました。
2. 原XLSXを持たない画像帳票は、牛用の固定見出しと追加項目一覧へ出力されていました。
   画面で設定した7列10行の明細構造を、そのままXLSXへ保持できませんでした。
3. 識別番号の必須判定が複数serviceへ重複し、受付後の現在のTemplate種類を参照していました。
   種類を変更すると受付済みjobの確定条件が変わる可能性がありました。

修正前の短いテストで欠落を再現し、失敗ログを保存しています。新規テストは、
汎用XLSXの値・行列の保持、不正bindingの拒否、英数字識別での補正・確定・取得の3ケースです。
既存のアップロードテストへ再処理の確認を追加しました。旧牛用の確定テストでは
テンプレート種類を明示し、旧契約のassertionを保持しています。

## 変更した境界

| ファイル | 変更内容 |
| --- | --- |
| `backend/resources/templates/generic-document-v1/template.yaml` | 固定regions・marks・mapping・識別情報を持たない汎用初期config |
| `backend/app/domain/template_config.py` | 空テンプレートconfigの初期値を汎用resourceへ変更 |
| `backend/app/services/template_service.py` | 新規画像テンプレートを汎用種類で作成。汎用版の再処理は前版のconfigを継承 |
| `backend/app/services/job_service.py` | 新規単一ページjobに受付時のdocumentTypeを保存 |
| `backend/app/services/document_identity_policy.py` | 受付時種類を優先する識別番号必須判定を共有 |
| `backend/app/services/ocr_result_service.py` / `document_reconciliation_service.py` | 上記判定を利用。補正・確定・照合の既存経路を維持 |
| `backend/app/services/excel_export_service.py` | 受付時reviewStructure / mappingから独立項目・見出し・明細表を出力 |
| `backend/app/services/ocr_result_export.py` | 確定書類名を汎用出力へ渡す |
| `backend/app/main.py` / `backend/pyproject.toml` | API名と説明を汎用帳票へ変更 |
| `frontend/src/pages/TemplatePage.tsx` / `components/LayoutEditor.tsx` | 操作説明の業種依存の例を整理。保存payloadは変更なし |
| `backend/tests/unit/test_excel_export_service.py` | 同名列・途中空行・先頭ゼロ・補正値・改行・式文字列・不正対応を検証 |
| `backend/tests/integration/test_jobs_api.py` / `test_ocr_review_api.py` | 汎用初期設定・再処理・受付時種類・補正・確定・未確定出力拒否を検証 |
| `AGENTS.md` / `README.md` / `docs/ocr-safety-contract.md` / 本文書 / `docs/frontend-ux-redesign-2026-10-02.md` | 許可・検証方針、現行仕様と検証結果への参照を更新 |

frontendだけでは修正できない理由は、テンプレート種類・受付snapshot・確定条件・XLSXの
生成をbackendが所有しているためです。出力時に画面のラベルや配列位置から対応を推測せず、
保存されたcolumn ID、field ID、mappingId、targetPathを検証します。

## 新しい出力仕様と互換性

- 非空の受付mappingがある画像帳票は、受付reviewStructureから列・行・独立項目を出力します。
  同名の列、明示された空セル、途中・末尾の空行を維持します。
- 値は確定結果のnormalizedを優先し、nullの場合だけrawを使います。汎用項目は文字列として
  保存し、数値・日付・単位を推測しません。先頭ゼロや式に見える文字列も保持します。
- fieldとmappingの不一致、欠落、重複使用は409 `XLSX_REVIEW_STRUCTURE_INVALID` で拒否します。
  空欄に見える場所へ対応を推測して追加しません。
- reviewStructureがない旧受付は、その受付mappingから項目一覧を作ります。
  mappingのない旧固定形式は従来の出力を利用します。
- 未mappingの非空手動値は別の項目欄へ残します。空の牛用見出しを汎用結果へ追加しません。
- 原XLSXへの出力が可能な帳票は既存経路を優先します。pagedの原本SHA・明示cell binding・
  確定revision・書式保持は変更していません。
- 新規jobは受付時documentTypeで識別条件を判定します。種類を持たない旧snapshotの
  従来条件、旧牛用targetPathの条件は維持します。旧データの自動移行は行っていません。

これは画像帳票の出力契約と新規テンプレートの初期設定・受付時識別判定の意図的な変更です。
[Safety Contract §7.4](ocr-safety-contract.md#74-汎用画像帳票の識別とxlsx出力2026-10-04)へ反映しました。
mapping identity、physical identity、provider association、crop / boundingBox、OCRの
prompt・model・structured-output schemaは変更せず、既存OCR contractの対応関係を維持しました。
DB schemaの変更、既存jobのsnapshot書換え、過去結果の再構築は行っていません。

## 実結果との照合

前のUX作業で実OpenAIにより取得し、人が補正・確定したjob
`65d54edf-71a5-4d00-bfa1-434782e7f6ee` を保存response比較に使用しました。
今回の追加OpenAI / Google API呼出しはともに **0回** です。

- revision 3、confirmed=true。再起動前後のresult・reviewStructure・recordStructure・
  fieldPreviews・revision・confirmedが一致し、保存値と72個のcrop bindingに変更はありません。
- 実出力APIから取得したXLSXの「売却記録」7列10行、計70セルを、受付field ID → targetPath →
  確定値の順で照合しました。空欄を含めて一致しました。補正値・末尾2空行も保持しています。
- 名号・生年月日の独立項目と、旧jobへ手動入力した未mappingの識別番号も保持しています。
- 出力ファイルをSpreadsheetsの読み取り専用importで確認し、描画画像を保存しました。
  製品のXLSX生成は既存openpyxlを利用しています。
- ブラウザーのボタン操作は出力APIへHTTP 200で到達し、XLSXデータの取得完了を記録しました。
  このアプリ内ブラウザーでは、その後の保存が `canceled` になりました。同じURLの直接取得も
  `ERR_BLOCKED_BY_CLIENT` で遮断されています。今回のブラウザー保存完了は未確認です。
  API取得成功と、端末のファイル保存成功を区別しています。

association mismatch・mapping誤対応・schema errorの新規実OCR測定は行っていません。
保存responseの70セルにおける出力誤対応は **0件** でした。この件数はOCR文字認識精度の指標ではありません。

## 検証

すべてのpytest一時成果物は、未使用のDドライブ絶対パスへ保存しました。

| 検証 | コマンド・結果 |
| --- | --- |
| 変更前OCR contract | `python scripts/verify_ocr_contract.py --basetemp=D:\llm-ocr-archive\pytest-work\20261004-generic-contract-before-01` — **225件成功** |
| 変更後OCR contract | 同script、`--basetemp=D:\llm-ocr-archive\pytest-work\20261004-generic-contract-after-01` — **226件成功** |
| backend対象検証 | XLSX unit、jobs API、review API — **13件成功** |
| backend全体 | `python -m pytest -v --basetemp=D:\llm-ocr-archive\pytest-work\20261004-generic-backend-full-final-02` — 追記の最小検証方針に合わせて途中停止。全体成功は未確認 |
| frontend対象検証 | `npm test -- LayoutEditor.test.tsx TemplatePage.test.tsx` — **178件成功、3ファイル** |
| frontend build | `npm run build` — 成功。JS 594.57 kB / gzip 181.51 kB。既存の500 kB超警告あり |
| Ruff | 変更したbackend実装・対象テスト13ファイル — 成功 |
| mypy | `mypy --follow-imports=silent`、変更した8モジュール — 成功。全backendの型検査ではない |
| 差分 | `git diff --check` — 成功 |
| 実APIと保存response | 確定結果不変、7列10行70セル一致、追加外部API 0回 |

追加した意味のある回帰テストは保持しています。使い捨ての調査script・画像・XLSX・
ログは製品ディレクトリに置いていません。既存テストの削除・skip追加・assertion弱体化はありません。
途中の失敗と中断した旧検証のログも、成功した最終実行とは区別して保存しています。

全件実行では影響範囲外のOffice/WSL検証も実行され、今回の変更に関するAPI検証は通過して
いました。追記後は全件完走を完了条件にせず、変更した識別・出力・初期設定の境界が
既存専用script、対象integration、保存実response比較で確認できていることを条件としました。
全件実行の終了コードは完走成功の証拠として使用していません。今回のtest processと
その専用worker clientだけを停止し、製品のUI/APIは稼働を維持しています。

## 残る制約

- 旧牛用model・resource・固定targetPath・非提供画面のコードは互換性のため残っています。
  内部の業種固有schemaを全面廃止した状態ではありません。
- 任意のExcel形式・複数ページ構成を無条件に処理できるという保証はありません。
  renderer・入力位置合わせ・crop・全ページ確認の既存guardを維持しています。
- 今回の実結果比較は保存済みの帳票1件です。非牛用の英数字識別は短いAPI integrationで
  検証しましたが、外部OCR providerの新しい実帳票精度比較はしていません。
- 実複数ページOCR、全件ブラウザーE2E、クラウド本番配備の追加確認は行っていません。
- バックエンド全件実行は途中停止したため、全backendの回帰なしという保証はありません。
- アプリ内ブラウザーでの保存キャンセルは上記のとおり残っています。

## 証跡

`D:\llm-ocr-archive\genericization\20261004`

- `status-before.txt` / `diff-before.patch` / `excel-export-before.py` / `contract-before.md`
- `red.log` / `reprocess-red.log` / `focused-01.log` / `focused-02.log` / `focused-03.log`
- `contract-before.log` / `contract-after.log` / `backend-full-final.log`
- `backend-full-stopped.json`（中断理由と停止した専用process）
- `frontend-focused.log` / `build.log` / `ruff.log`
- `live-result-before.json` / `live-result-after.json` / `live-verification.json`
- `live-generic-result.xlsx` / `xlsx-inspection.json` / `xlsx-preview.png`
- `review-browser.png` / `browser-download-observed.json`
- `browser-direct-download.json` / `original-template-check.json` / `runtime-health.json`
- `tracked-current-only.diff` / `tracked-current-files.json` / `mypy.log`

既存差分・既存未追跡ファイルを保持し、staging・commit・pushは行っていません。
