# WSL環境identityの保存・現在照合・期限切れ拒否

## 原因と実装

WSL資格経路には、保存済み環境証拠を現在のファイル内容へ再接続し、期限切れを拒否する
保存経路がなかった。現在の参照画像・glyph・geometryの証拠に加え、環境identityの
有効期限付き記録を実装した。**これは環境全体の認定証発行ではない。**

- `renderer_wsl_worker.inspect_environment` は固定 `--inspect-environment <runtime SHA>`
  の無帳票モード。Office/PDF/font parser/OCRを起動しない。
  既存core guard、private namespace、readonly runtime descriptorの前後SHA検査を用いる。
- `wsl_environment_evidence.inspect_current` はworkerソースをprivateな実行用コピーにし、
  `-I -S -B`、固定引数、非表示process、45秒上限で照会する。
  1MiBの応答上限、選択したimageと実行workerのSHA、各identityのpolicy/typeを確認する。
  タイムアウト・不正応答・存在しないimageを失敗とし、live環境へfallbackしない。
- 照合対象はruntime image/kernel、全Windows system fonts、worker、Python/ELF起動依存、
  broker core guard。内容を再読取し、metadata-only cacheを使用しない。
- `new_lease` は保存版の参照実行条件と現在の実体の一致を確認する。
  bootstrap証拠を持たない旧reportは変更せず、新証拠を補完しない。
- 当初の保存recordは `wsl-environment-identity-lease-v1`、scopeは `identity-freshness-only`。
  templateVersionId、prepared artifact graph SHA、identity、distribution、発行/失効時刻を持つ。
  lifetimeは24時間固定。余分なqualified flag、寿命延長、別版/別graphへの流用は拒否する。
- `save_lease` は対象source jobが所有する別JobArtifactとして保存する。
  `load_lease` はartifact所有者/名前/attempt/media type/path/SHA、版/graph、期限を検証してから
  現在の実体を再照会し、照会後にも期限を再確認する。GETはrecord/DBを更新しない。
- `ReferenceQualificationService` のPOST保存とGET検証に接続し、実装fingerprintへ新moduleを含めた。
  長い新規照会はPOSTのDB mutation lock取得前に行う。取得先の版は保存直前に既存経路で再検証する。

## 後続のWindows/WSLホスト照合

新規recordは `wsl-environment-identity-lease-v2` とし、`hostIdentity` を必須にした。
`wsl_host_identity.py` はWindows build/UBR/DisplayVersion、architecture、WSL launcherの
絶対pathと実体SHA、`wsl.exe --version` の生UTF-16LE出力SHAを取得する。
サイズ・時間を制限し、launcherを取得前後に再hashする。Linux環境照会の前後で
ホストidentityの一致を要求し、GETでも所有者・期限検査後に同じ前後照合を行う。
旧v1やhost情報のないrecordは補完せず拒否する。collectorのコードも資格fingerprintに含む。

このhost記録はlease作成時点の更新検出用であり、元のXLSX変換時のWindows環境を
遡って証明しない。OS全依存のclosure・正式環境資格・有効化許可の代わりにはしない。
変更前の追加テスト2件は拒否不足で失敗し、変更後の関連単体159件は成功（0.46秒）。
対象6ファイルのRuff、2サービスの限定Mypy成功。
保存資格API・実WSL無帳票照会は24 passed（138.14秒）、新v2の保存/再取得一致を確認した。
OCR契約scriptは210 passed / 1 warning（68.72秒）。直前の同じ210件のbaselineと一致する。
実OpenAI/Google APIは使用せず、OCR認識精度の実測は含まない。
この変更後に開始した追加backend fullは、後述の安全終了要求に従って中断した。
下記の完走済みfullとは区別し、成功実績には含めない。

## 変更境界と残条件

環境identityの一致と、network/resource/host manager/Windows側依存を含む正式な環境資格は別である。
このrecordにproduction permissionを持たせず、`environmentQualified=false`、ACTIVE/受付拒否は維持する。
送信前の人間によるcrop確認工程は承認待ちであり、追加していない。
意味づけ・矩形・mapping・入力画像・provider/model/prompt・実DB・原本は変更していない。

## 検証

- 変更前の全体回帰: **2262 passed / 1 skipped / 3 warnings（35分33秒）**。
  これは本変更より前のbaselineとして扱う。
- 無帳票の実WSL再取得2回一致と欠落image拒否: **2 passed（27.26秒）**。
- 単体/保存参照/worker境界: **60 passed（0.33秒）**。
- 保存資格API（実WSLでの作成・GET一致を含む）: **18 passed（101.88秒）**。
  その後、期限切れGETとDB非更新の検査を追加した最終の単体/API合同実行は
  **78 passed（99.49秒）**。期限切れは `REFERENCE_EVIDENCE_CHANGED` で拒否し、DBは不変。
- 変更後のOCR契約専用script: **210 passed / 1 warning（47.27秒）**。
  変更前の210件と同じ対象。全backend回帰・実API認識品質の代替ではない。
- 現workerソースSHA-256:
  `9d0c9dc1376a297ebc01e2e70ec4f294c7606c29fc44be20af600ce375da90c4`。
- 対象Ruff成功。新module/資格serviceのMypyは `--follow-imports=silent` で成功。
  Linux専用workerは `--platform linux --python-version 3.10 --follow-imports=silent` で成功。
- 最初のWindows通常mypyは、Linux専用workerをWindowsとして解析したattrエラーと、
  依存先のopenpyxl等stub不足・既存import/layout serviceの型問題を含み48件だった。
  platform別・対象限定の成功をプロジェクト全体mypy成功と扱わない。
- 新しいテストの長い行/比較順・expiry検査の配置誤りをRuffで検出し、修正した。
  既存assertionの削除やskip追加はしていない。
- 本変更と後続の実行時隔離証拠検査を含むbackend full:
  **2348 passed / 1 skipped / 3 warnings（2315.61秒）**、終了コード0。
  `RUN_OPENAI_INTEGRATION_TESTS=false`、basetempは `.pt-environment-execution-full-0914`。
  app/testsのPython 460ファイルは開始・終了時のSHAが全件一致した。
  実行中のソース変更・別native rendererの並行起動なし。
  OpenAI 0/100回、Google 0回。

既存OCR contractを維持した。元XLSX・実スキャン・canonical矩形・mapping ID・targetPath・
sourceRegionId・crop/association・provider/model/prompt/schema・補正/出力対応は本変更の対象外。
今回の実スキャンを使ったprovider送信・レビュー・確定・出力までの比較は未実施であり、
OCR安全性を完全には検証できていない。対象実版のACTIVE化やカードからの実ダウンロードも未確認。

再現コマンド（backend cwd、Pythonは `.venv/Scripts/python.exe`）:

```text
python -m pytest -q --tb=short -o addopts='' -p no:cacheprovider tests/integration/test_wsl_environment_inspection_runtime.py
python -m pytest -q --tb=short -o addopts='' -p no:cacheprovider tests/unit/test_wsl_environment_evidence.py tests/unit/test_prepared_reference_replay.py tests/unit/test_wsl_worker_identity.py tests/integration/test_prepared_reference_qualification.py
python -m mypy app/services/wsl_environment_evidence.py app/services/reference_qualification_service.py --follow-imports=silent --no-incremental
python -m mypy app/services/renderer_wsl_worker.py --platform linux --python-version 3.10 --follow-imports=silent --no-incremental
```

## 安全終了確認 — COMPLETE

利用者の最新指示により、本作業をWSL environment identity leaseの保存・再照合・期限管理に限定して終了する。
正式な環境資格・production permission・ACTIVE化を完了したという意味ではない。
終了確認中の製品コード変更は0件。新機能・新schema・refactor・追加closure探索は行っていない。

### 確認した対象ファイル

- `backend/app/services/wsl_environment_evidence.py`
- `backend/app/services/wsl_host_identity.py`
- `backend/app/services/renderer_wsl_worker.py` の `inspect_environment` と固定CLI分岐
- `backend/app/services/reference_qualification_service.py` のlease保存/読取・fingerprint追加部分
- `backend/tests/unit/test_wsl_environment_evidence.py`
- `backend/tests/unit/test_wsl_host_identity.py`
- `backend/tests/integration/test_wsl_environment_inspection_runtime.py`
- `backend/tests/integration/test_prepared_reference_qualification.py` の関連保存/失効/非更新検証
- `docs/ocr-safety-contract.md`、本記録、`docs/verification/production-goal-audit-20260914.md`

対象にdebugコード・不要ログ・skip追加・assertion削除は見つからなかった。
workerのJSON stdoutは固定照会の応答でありdebug出力ではない。
既存の無関係な差分、試験生成物、原本、実DBは整理・削除していない。

### 完了条件との照合

| 条件 | 確認結果・根拠 |
| --- | --- |
| 1. v2保存 | `new_lease` → `save_lease`。実WSL保存APIでv2 artifactとGET一致を確認 |
| 2. GETのWindows host再取得 | `load_lease` が `host_identity.current()` をLinux照会前後に実行 |
| 3. WSL現在実体の再取得 | `inspect_current` → 固定workerの `inspect_environment`。runtime/font/bootstrap/workerを現物から検査 |
| 4. identity不一致拒否 | 保存/GETの完全一致比較。host前後変更・Linux identity変更の拒否テスト成功 |
| 5. 期限切れ拒否 | `issuedAt <= now < expiresAt` を照会前後に検査。境界時刻と期限切れGETの拒否を確認 |
| 6. artifact/version/graph拒否 | 所有job・名前・attempt・media type・path・SHA、版ID、prepared graph SHAを照合 |
| 7. v1/host欠落拒否 | policyと厳密なrecord key集合を検査。補完せず照会前に拒否するテスト成功 |
| 8. GET非更新 | 読取側にDB/artifactの書込なし。返却用JSONだけを計算し直す。保存plan・期限切れ時のquality非更新をAPIで確認 |
| 9. fallback不存在 | timeout・不正応答・欠落imageは例外/未検証。別runtimeや成功フラグへのfallbackなし |
| 10. production permissionなし | lease scopeは `identity-freshness-only`、資格/許可を付与しない |
| 11. environmentQualified=false | WSLのPOST/GETでfalseを設定。書換えた成功フラグも受理せず、ACTIVE/受付拒否を維持 |
| 12. contract/regression維持 | 既存full 2348/1skip/3warnings、v2後の関連159件・実WSL/API24件・OCR契約210件成功。下記の終了確認61件も成功 |
| 13. 明白な迂回経路なし | 製品コード・frontend・scriptsを横断検索し、現leaseの読取判断は `load_lease` 経由だけと確認 |

### 迂回検索・最終検査

`backend/app`、`frontend/src`、`scripts`、`backend/scripts` に対して
`wsl_environment_evidence`、`host_identity`、`environmentEvidence`、
`preparedEvidenceSha256`、`identity-freshness-only`、lease policy、`environment.json`、
`expiresAt`、`load_lease` / `validate_lease` / `new_lease` / `save_lease` を検索した。
現lease JSONのparseは `load_lease` 内で所有artifactの内容SHA検査後に行われ、その後に正式検証と現物照会を通る。
呼出元は `ReferenceQualificationService.status`。現在照会を省略してleaseを有効と判定する経路、
artifact内容だけやexpiresAtだけによる許可経路は見つからなかった。
同名 `environment.json` の旧Windows診断scriptや、Google/backupの別用途の期限はWSL leaseの迂回ではない。

- 終了確認: `python -m pytest tests/unit/test_wsl_environment_evidence.py tests/unit/test_wsl_host_identity.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-wsl-identity-closure-0914`。
  **61 passed（0.30秒）**、終了コード0。新しい全backend fullは起動していない。
- 既存完走full: **2348 passed / 1 skipped / 3 warnings**。当時のPython 460ファイルSHA一致。
  これはhost v2より前のbaselineであり、v2後fullの成功と記載しない。
- 既に起動済みだった `.pt-host-unit-admission-full-0914` の追加fullは所有sessionへ割込みを送り中断。
  終了コード1であり成功扱いしない。停止後の対象pytest・WSL変換unitの残留なし。
  この実行開始・停止後のapp/tests Python **463ファイルは全SHA一致、追加/削除0件**。
- 対象Ruff成功、対象限定Mypy成功は前掲結果を維持。Windows通常Mypyの既存/環境依存エラーを全体成功と扱わない。
- `git diff --check` 成功。OpenAI **0回**、Google API **0回**。

### 意図的に未実施・次のIssue候補

正式environment qualification、Windows/WSL/renderer dependency closure完全化、
実スキャンprovider送信・review・confirm・output・元XLSXとのE2E比較、認識精度実測、
ACTIVE版カードからの実ダウンロード、人間によるcrop確認工程は範囲外として未実施のまま残す。
今回の完了条件を直接妨げる新規問題は見つからなかった。追加改善を開始せず終了する。
