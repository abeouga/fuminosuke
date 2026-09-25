# 複数ページの候補OCR受付 — 2026-09-21

後続追記: 本文は正式資格経路の実装時点の記録です。環境更新後の候補利用条件は、
Safety Contract §12.10と[候補OCR準備確認](candidate-readiness-20260921.md)で追加しました。
以下のDRAFT/未有効化という観測は当時の状態であり、後続の実測と分けて参照してください。

## 目的・変更境界

「複数ページから対応する欄の文字候補を得て、人間が確認・修正・確定する」を完成条件とする。
利用者の最短開放案への実装承認に基づき、常時閉鎖だった受付を正式資格validatorへ接続した。
これは新paged受付条件の意図的変更であり、全筆画の自動安全認定が完成したという意味ではない。

`JobPageService.catalog/accept`は共通の判定でACTIVE、現layout SHA、ページ順序、全ページの
text mapping所有を確認する。既存`ReferenceQualificationService.require_for_activation`を
同じsessionで呼び、原本・plan・artifact/graph・実装・環境・期限・component資格を再検証する。
acceptは従来のmutation lock/transaction内で判定し、受付中の期限切れも拒否する。
旧`layoutVerified`は参照せず変更もしない。idempotency、受付後のimmutable snapshotを維持する。

新bundleに`admission`を追加し、`paged-reviewed-candidates-v1`、人間レビュー必須、
`inkClearanceVerified=false`、資格report SHA/binding/発行時刻/期限を固定する。
renderer資格、入力SHA、一対一page binding、registration失敗拒否、送信前の全crop照合、
association、文書全体API予算、補正revision、全ページ明示確認、未確定出力拒否は維持する。
provider/model/prompt/schema、位置合わせ閾値、物理ID、mapping/座標、原本、既存DB schemaは変更しない。

入力画面・正式資格表示・専用レビューに、OCRは候補であり筆画完全性を自動保証しないことを表示する。
不足筆画をPASS扱いせず、人間が画像・欄・文字を確認する。旧安全段階の診断記述を資格証や
送信許可そのものとして扱わない。契約の正本はSafety Contract §12.9。

## 検証記録

今回の変更ファイル（開始時からの差分を含むファイルも、今回の追記範囲だけを対象とする）:

- backend: `app/services/job_page_service.py`、`tests/integration/test_calc_qualification_plan.py`、
  `tests/integration/test_job_pages.py`。
- frontend: `src/components/OcrPageBindings.tsx`、`ReferenceQualification.tsx`、
  `src/pages/PagedReviewPage.tsx`と各対応テスト。
- 文書: `AGENTS.md`、`docs/ocr-safety-contract.md`、`docs/multi-page-ocr.md`、本報告。

- 変更前OCR contract: 210 passed、50.78秒。verifierの`targets`をASTから読み、同じpytest対象を
  `D:/llm-ocr-archive/pytest-work/paged-admission-before-*`で実行した。C:固定tempのscript直接実行はしていない。
- 既存受付/正式資格対象の初回: 39 passed / 追加7 failed。承認だけを検証するfixtureには
  読み取り欄のないページがあり、候補受付が拒否した。新専用fixtureで資格発行前に必要mappingを追加した。
  構築順による原本範囲不一致と、renderer stubの同一画像を入力にした重複拒否も修正した。
  製品側の拒否・assertionは弱めていない。
- 専用fixture再試験: 7 passed。正式validatorを迂回せず、承認→catalog→受付、旧boolean不要、
  未確定queue、snapshot、冪等性、期限/graph/原本/版状態/layout変更拒否とjob未作成を確認。
  native資格証拠はstubであり、実環境認定や実OCRの証拠ではない。
- frontend対象: 最終55 passed / 4 files。正式資格説明文も更新した後の全体再試験は
  426 passed / 35 files（14.89秒）、build成功（既存500 kB chunk警告）。
- 対象Python RuffおよびJobPageService限定mypy成功。
- 変更後OCR contract: 同じ210対象がpassed、54.98秒。
  D:の別basetempを使い、実rendererを起動しない契約対象を全体試験と並行して実行した。
- backend全体: 2526 passed / 1 skipped / 3 warnings（2230.81秒）。既存skipとdeprecated警告を維持。
  コマンドはbackendから`.venv/Scripts/python.exe -m pytest tests -q -o addopts= -p no:cacheprovider
  --basetemp=D:/llm-ocr-archive/pytest-work/paged-admission-full-20260921-a`。
  受付→既存pipeline、crop改変時のprovider生成拒否、補正/確定/出力、budget、renderer回帰を含む。
- `git diff --check`成功。staging/commit/pushなし。

## 実環境・未確認範囲

変更前に既存Chromeタブ1093604667の画面を取得した。V2の正式資格PASS、保存済み4ページと
「承認して有効化」を確認。この初回確認では新規タブ・別ブラウザー・画面再読込は行っていない。
以下の再取得で資格不一致を検出したため、この初回PASS表示を現在の資格として扱わない。
この時点では実V2は未承認であり、実OpenAI呼出し0。テスト通過を実OCR成功とは扱わない。
実DBをSQLite `mode=ro` / `query_only`で確認した時点では、系列
`9dc80bd9-0070-4754-a271-7ff274729e14` のV1
`b92f4035-06be-4d09-8cc4-c0a55aba51ba` がACTIVE、V2
`e3286670-4190-436a-a1b6-9f0d7e278b7c` がDRAFTだった。
V2のrangeはA1:AK28/A29:AK60/A61:AK89/A90:AK102、mapping数は59/51/37/3。
現行batch規則による最小呼出しは3/2/2/1で計8回、設定された文書全体上限も8回。
この確認のために予算・設定・版を変更していない。
実有効化・4スキャンOCR・人間レビュー/出力の到達状況は、後続の実測がある場合だけ追記する。
実応答の認識誤り率・association mismatch率は未測定であり、OCR安全性を完全には検証できていない。

### 実装反映後の再確認と残存blocker

全体試験の完了後、DBに実行中・待機中jobがないことを確認して、対象workspaceのbackendだけを
再起動した。`GET /api/v1/health`は正常。既存Viteは維持し、保存済みを確認した既存Chromeタブを
再読込してV2の工程5へ戻った。新規タブ・別ブラウザーは使っていない。

現在の実画面では`UNVERIFIED / REFERENCE_EVIDENCE_CHANGED`、有効化ボタン無効を確認し、
画面を取得した。テンプレート設定は保存済み、未割当0件・設定不完全0件であり、
今回の停止は欄の意味づけ不足ではない。

DBを読み取り専用で開いて正式validatorの例外を確認した結果、直接の拒否理由は
`Environment installation changed after qualification`だった。保存済みenvironment leaseと
既存`inspect_current`による現在値を比較すると、不一致は
`bootstrapPythonIdentity.nativeDependencies.sha256`だけだった。

- 保存済み: `b6d2ce5fd5990e330f280dc2f997147849513060338680068b66b141917ea32d`
- 現在値: `735e4e2b70f53bf16e9998b764587dce76eef6bb44b7d77209858fc360b4adbe`

runtime、font、worker、bootstrap Python tree、core guardの残りの比較値は一致した。
資格実装fingerprintも一致し、期限切れではない。このnative hashはELF依存ファイルと
loader設定・依存graphの集約値である。具体的にどの入力が変わったか、実ファイル変更か
観測条件の差かは未確定。backend再起動や今回の変更が原因だとは断定しない。

保存済み証拠の書き換え、比較の省略、資格の強制PASS化は行っていない。
次はこの環境差を特定し、現在環境が妥当であることを確認してから、正規の印刷用ファイル再準備と
正式資格再取得を行う。再準備だけで必ず解消するという保証はない。
その後にV2有効化（V1の旧版化）と4スキャンの実OCRを行う順序とする。
有効化・OpenAI送信について提示した確認には、この記録時点で返答がなく、実施していない。
V1 ACTIVE / V2 DRAFTを維持し、今回の実OpenAI呼出しは0回。実装・自動試験の完了と、
実テンプレートでの機能開放・実OCR成功は区別する。

既存dirty差分は保持。staging、commit、push、既存差分の復元・整理なし。
