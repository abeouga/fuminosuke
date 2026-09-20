# 承認後の検証段階分離

本番有効化は未完了。2026-09-13の承認に基づき、未取得の記入画像を
テンプレート資格の前提にしない診断契約を実装した。実入力の送信前検査、
正式な資格発行、承認・受付の成功経路はこの変更だけでは完成しない。

## 変更境界

- `paged_safety_stages.py`: 有効化と入力後の検査を分離する純粋な診断記述。
- `reference_qualification_service.py`: WSL経路だけに適用し、所有証拠の再検査後に
  段階情報を再生成する。保存した成功booleanや改変した段階情報で承認しない。
- `ReferenceQualification.tsx` / `printLayout.ts`: 未解決の有効化条件と未実施の入力検査を
  別に表示する。診断codesが空でも資格判定待ちであって有効化済みとは表示しない。
- `AGENTS.md` / `ocr-safety-contract.md`: 承認された検査時期を明文化した。
- `scripts/dry_run_print_preparation.py`: 原本DBをread-onlyで開き、SQLite backupと必要な
  成果物のコピーを用意する。アプリの書込用Databaseはコピーにだけ接続し、処理後も
  元の版・source job・コピー対象ファイルが変わっていないことを検査する。

provider/model/prompt/schema、legacy crop、保存済みjob、実DBの版状態は変更していない。
実OpenAI呼出しはこのタスク累計 **0 / 100回**、Google呼出しも0回。

## 実4ページのコピー上での再確定

対象版: `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`（DRAFT）。
コピー: `C:/Users/hanam/AppData/Local/Temp/llmocr-prepare-copy-x5_l5t3k`。
検証用コピーは削除していない。元DBを置き換えるファイルではない。

固定runtime:
`9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01`。
元revision:
`2b026bcecccc5542cfb729614e43b086bd95fae1e11c5a89e2ee243237f33512`。
コピー再確定revision:
`7648ae168127a4029875094e419f0469de490fb6e6f9fb8f1256ae5eb5d69d4b`。

正規の `PrintPreparationService.prepare` をコピーに実行し、以下の完全一致を確認した。

- 全4ページのrange、fields/bindings、reference PNG SHA
- canonical layout JSON、DRAFT状態、コピー対象の既存ファイルbytes
- 処理前後の元版・元source jobのJSON、元成果物のSHA

新しいコピー側reportに固定runtime、全font集合、workerのidentityを記録した。
元の古いreportに情報を後付けしていない。`.env`や稼働設定の変更なし。

## コピー上での正規資格検査とGET

`ReferenceQualificationService.qualify` と、その後の `status` が完全一致した。
再描画は全ページ一致。使用文字命令3,515、font code640のglyph照合はPASS、
全使用faceが宣言faceに一致した。文字位置・可視性の認定とは区別する。

残る有効化のcodes:

- `REFERENCE_ANCHORS_INSUFFICIENT`
- `GEOMETRY_TOLERANCE_UNQUALIFIED`
- `WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED`

| ページ | 旧raster検査対象領域 | 測定できた領域 | 最大誤差px | 入力後へ分離したbinding付き領域 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 122 | 102 | 2.0 | 60 |
| 2 | 81 | 22 | 2.0 | 51 |
| 3 | 76 | 35 | 2.0 | 37 |
| 4 | 6 | 3 | 1.5 | 3 |

全ページが `REFERENCE_ANCHORS_INSUFFICIENT` でFAIL。
「測定できた部分が2px以内」を未測定領域の成功へ拡張していない。
旧raster検査は四辺が揃った枠と5個以上の分散したanchorを要求する。
記入欄3個のページや罫線が揃わない領域を扱う資格方式は引き続き未完成であり、
記入画像を提出すればこの未実装が解消するとは説明しない。

qualification report SHA:
`b0ec86585bfa3434c149d9d4cefccc67e2bb4cf0ab065e1f11850811c58d0bf9`。
`cropSafety` は全ページ `DEFERRED_UNTIL_INPUT`、実行段階は `NOT_EVALUATED`。
実入力の検査が実行済みという証拠ではない。

## 確認済み検証

backendのPythonは `.venv/Scripts/python.exe` を使用。

| 検証 | 結果 |
| --- | --- |
| 変更前 `scripts/verify_ocr_contract.py` | 210 passed、41.71秒 |
| 段階unit + 既存qualification/glyph/replay API | 44 passed、102.12秒 |
| 新段階API + 段階unit（上記と重複あり） | 6 passed、2.48秒 |
| `test_wsl_glyph_qualification.py`（実WSL） | 1 passed、76.64秒 |
| 変更後 `scripts/verify_ocr_contract.py` | 210 passed、41.27秒 |
| frontend対象2ファイル | 17 passed |
| 最終 `npm test`（未知の段階policyでも拒否理由を隠さない回帰を追加） | 34ファイル、372 passed |
| `npm run build` | 成功、既存の500kB超bundle警告あり |
| 今回のPython対象Ruff | 成功 |
| 変更service2本・dry-run scriptのmypy（follow-imports=silent） | 3ファイル、エラー0 |

通常のmypyは依存先4ファイルに31件のエラーを報告した。未型付け依存のstubと
既存のimport先typingを含むため、全体mypy成功とは扱わない。
実Chrome/APIサーバーでのUI操作、本番OCR精度、全体有効化E2Eは未検証。
既存OCR contractは前後210件と、実コピーのidentity比較の範囲で維持した。
本番OCR安全性を完全には検証できていない。

backend全体回帰は `.pt-safety-stages-full-0913` で完了した。
**1,508 passed / 1 skipped / 3 warnings / 1,987.51秒、終了コード0**。
`RUN_OPENAI_INTEGRATION_TESTS=false` を明示し、既存の外部API opt-in skipを維持した。
warningは既存のopenpyxl Font.copy非推奨通知3件。今回の全体実行は段階分離APIに加え、
先行追加したvector/raster検証も含む。試験の削除・skip追加・assertion弱体化なし。

終了前のread-only確認でも実版はDRAFTで元revisionに一致し、保存PDF/XLSXのSHAも一致した。
`git diff --check` 成功。既存の未コミット差分を保持し、staging/commit/pushは行っていない。
