# Webベータ フェーズ6：独立審査・回帰検証

2026-10-16。**判定は修正要求。フェーズ7へ進めない。** 審査工程と今回の検証は完了したが、フェーズ6の終了条件「公開阻害事項0」は未達成である。計画上の担当はAstra / XHigh。別エージェントは起動せず、前工程の実装結果を現在のソースと新しい障害再現で審査した。

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。製品コード・製品テストの修正、通常DBの移行、Git書込、ゴール作成、AWS配備、有料AI送信は行っていない。精度V5/V6の改善も再開していない。

## 修正要求

| ID / 優先度 | 確認した問題と影響 | 根拠・修正境界 |
| --- | --- | --- |
| R1 / P1 | **DB削除後にファイル削除が中断すると、再適用しても原本・生成物が残る。** 同じjobの36ファイルが同一SHAで残り、その後のbackup/restoreにも36ファイルすべてが含まれた。DB上は削除済みなので通常の削除操作を再試行できず、データ保持と容量回収の不具合になる。取得APIで他利用者へ漏れたことを示すものではない。 | `services/job_service.py:624`でDBをcommitした後にファイルを削除する。一方、`cloud/operations.py:104`はDB行がなければ再適用を省略する。削除receiptを正本として、行の有無にかかわらず所有範囲の残存ファイルを冪等に回収する必要がある。未使用templateの同じ中断境界も修正時に検証する。 |
| R2 / P1 | **バックアップ取得後のアカウント停止が、復元で取り消される。** 元環境で`disable`した利用者は新sessionを拒否されたが、古いbackupを復元すると同じsubへ新sessionを発行・認証できた。古いcookieの再利用は拒否されても、再ログインによる権限復活を防げていない。 | `cloud/registry.py:113`の有効状態はローカルDBだけに保存される。`cloud/backup.py:178`はsession/loginを消すが、backup中の`active=1`を復元し、外部台帳は削除しか再適用しない。停止・再有効化の順序を復元対象外で保持するか、復元利用者を停止して明示再承認する方式が必要。実Cognitoへは接続せず、署名検証後のcallbackと同じ`Registry.issue`/`authenticate`で再現した。 |
| R3 / P2 | **フロントエンド全体回帰が3件失敗する。** 要求設定に`headers: Headers {}`が含まれるため、旧設定オブジェクトとの完全一致assertionが失敗する。取得内容・保存名・不正revision拒否を壊した証拠はなく、認証transport導入後のテスト契約不整合と判断した。全体成功とは報告できない。 | `frontend/src/api.pagedOutput.test.ts:17`の1件、`frontend/src/components/ExcelExportActions.test.tsx:28`の2件。認証情報・必要ヘッダー・URL・revision・失敗後の再試行を意味として検証するよう既存assertionを整理する。製品の認証経路を外したり、テストを削除・skipして通さない。 |

R1は、実job削除の最初のfilesystem操作だけに中断を注入した。DBとreceiptは実処理でcommitされ、後続の再適用・backup・restoreも現行製品実装を呼び出している。R2では、全利用者削除と未招待subが復元後も拒否される対照条件を確認した。単なるログ文字列やソースの形による判定ではない。

## 再実行した回帰

証拠は `D:\llm-ocr-archive\beta\20261016-phase6`。前工程の封印済みscript/fixtureは読み取り、出力先を新しい専用領域に変更したコピーを実行した。旧assertionは変更していない。

| 対象 | 今回の結果 | 証拠と限界 |
| --- | --- | --- |
| 認証・利用者分離・再起動 | **127条件成功** | `phase3-regression.log` / `phase3/acceptance-result.json`。制御OIDC応答＋実RSA検証、実ASGI/SQLite。相互ID指定、CSRF、編集/SSE/音声接続拒否、commit時の失効、二利用者の重処理直列化、再起動後の確定XLSX一致。実Cognitoではない。 |
| AI案・共通保存・手修正 | **17条件成功** | `phase4-regression.log` / `phase4/integration/e2881721-5617-4e99-90bc-2cf374a66f27/result.json`。保存した制御応答を再生し、同値手修正保護、競合、取り消し・再送、54物理セル/22入力先、明示保存と別接続での再取得を確認。モデル精度の再評価ではない。専用仮台帳4予約。 |
| 共通予算・削除・backup/restore | **既存39条件成功** | `phase5-regression-2.log` / `phase5/a/49f7535b/result.json`。20/100/1000上限、並列予約、JST日次境界、音声前払い、予約後rollback、外部応答不明、台帳欠落、削除再適用、復元破損拒否、session破棄。R1/R2の境界は旧39条件に含まれていなかった。 |
| Linux renderer | **8条件成功** | `runtime-checks.json`。新しい専用supervisor/socket/spoolで、任意command拒否、lease切れ、切断、無効帳票、network/metadata/host-file遮断、read-only/capability、OOM注入、親SIGKILL後の回収と再描画を確認。操作対象は専用spoolの所有markerで限定した。実EC2 4GiB負荷試験ではない。 |
| フロントエンド全体 | **173成功・3失敗 / 176** | `frontend-full.log`。27ファイル中25成功・2失敗。R3として記録し、失敗ログを保持。 |
| フロントエンドbuild | **成功** | `frontend-build.log`。 |
| Ruff | **対象範囲成功** | `ruff.log`。cloud、予算、関連config/lifecycle/logging/DB/旧音声proxy、rendering。 |
| Mypy | **限定19ファイル成功、依存先を含める検査は85指摘** | `mypy-targeted.log`は`--follow-imports=silent --disable-error-code import-untyped`を使用。通常設定の`mypy.log`は85指摘/19ファイルで失敗。WindowsからLinux専用属性を検査した指摘、第三者型定義不足、既存サービスの型不整合を含む。限定成功をbackend全体成功へ拡張しない。 |
| 配備定義 | **静的検査成功** | `infra-lint.log`、cfn-lint 1.40.4 / ap-northeast-1。実IAM/S3 policy評価・UserData導入・AWS配備の成立は未検証。 |

回帰で取得した確定XLSXのSHA256は `79f92adb2bc91ccecbda632e2ac4f5e7ca1102b09aff38be8be4e8cfc03a2b5c`。B2=`00789`、B20=`60000`、確定revision4を保持した。制御S3の小さいfixtureでbackup約3.70秒、restore約2.11秒。実S3や全容量のRPO/RTOとは扱わない。

最初のphase5回帰コピーはWindowsのpath長上限で中断したため、新証拠領域内の実行ディレクトリ名だけを短縮して再実行した。初回ログは`phase5-regression.log`として保存し、製品障害・期待値変更とは区別する。独立反例の最初の実行も、検証設定のHTTPS origin不足で中断し、設定を補って再実行した。元ログを保持している。

## 検証コマンド

backendの仮想環境から、新証拠ディレクトリの`phase3/acceptance.py`、`phase4/integration.py`、`phase5/acceptance.py`、`adversarial.py`を`python -B`で実行した。外部socket通信を遮断し、provider/OIDC/S3は制御応答を使用した。実ASGI、SQLite、既存保存・出力処理は代替していない。

frontendは`npm test -- --reporter=default`と`npm run build`。静的検査は次の範囲で行った。

```text
ruff check app/cloud app/ai/api_budget.py app/ai/budget_contract.py app/core/config.py app/core/lifecycle.py app/core/logging.py app/db/session.py app/assistant/stream_proxy.py app/rendering
mypy app/cloud app/ai/api_budget.py app/ai/budget_contract.py app/core/config.py app/core/lifecycle.py app/core/logging.py app/db/session.py app/assistant/stream_proxy.py
mypy --follow-imports=silent --disable-error-code import-untyped app/cloud app/ai/api_budget.py app/ai/budget_contract.py app/core/config.py app/core/lifecycle.py app/core/logging.py app/db/session.py app/assistant/stream_proxy.py
cfn-lint -t deploy/infra.yaml -r ap-northeast-1
git diff --check
```

Mypy通常設定の失敗は、今回の製品変更による回帰ではない。開始時照合でフェーズ5終了時から製品ソース差分0を確認し、審査中も製品ソースを変更していない。ただし、85件すべての実行時影響を個別に解消したことは意味しない。

## 保持・未受入・次工程

開始時の`entry-comparison.json`でフェーズ5終了snapshotとの一致を確認した。終了時は`after.json`、`preservation.json`、`completion-1/hashes.json`を正本とする。変更は本報告、AGENTS、Safety Contract、運用手順の現在地追記だけ。通常DB全業務表/schema、台帳、781 artifact、設定、旧`llm-ocr`、過去封印を保持し、**既存OCR contractを維持した**。

実AI API **0回**、実AWS API **0回**。通常開発台帳 **440/924・残484**、beta追加1,000回は実環境で未初期化・未消費。仮台帳の1,000回試験と制御応答の4予約は許可枠を消費しない。最終未使用4帳票は使用していない。新しいrenderer serviceは停止し、所有子コンテナー/volume/spool残存0、既存phase2 serviceは稼働維持を確認した。

500MiB/人・全体5GiBは依然として受付時検査で、生成artifactの厳密な容量上限ではない。実AWS/S3/Cognito、実HTTPS証明書、実マイク、3ブラウザー・実4GiB負荷、全容量backup/restore、配備ドメインと費用は公開前の残条件である。今回の隔離試験だけで公開受入済みとはしない。

次は **6-A：GPT-6.1 Sol / HighでR1〜R3を修正**し、**6-B：Astra / XHighで再審査**する。R1では削除前/DB commit後/各ディレクトリ削除中の中断、再起動・復元、参照保護と他利用者の不変を確認する。R2では停止・再有効化・全削除の順序を含む古いbackup復元で権限を復活させない。R3では既存の意味上の検証を維持して全176テストとbuildを再確認する。実装は次の工程指示を待ち、フェーズ7へ先行しない。
