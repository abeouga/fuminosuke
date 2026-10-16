# Webベータ 修正6-B：独立再審査

2026-10-16。**修正6-AのR1〜R3は合格。追加の修正要求はない。** 実装担当工程から分けて、現在のコード、変更前後の封印、実際の障害処理を再審査した。計画上の担当はAstra / XHigh。別エージェントは起動していない。

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。製品コード・製品テストを変更せず、新しい専用領域で検証した。今回の合格は3件の修正と関連境界に対する判定であり、AWS上の公開受入完了ではない。次はフェーズ7の実環境受入へ進める。公開操作はこの工程では行っていない。

## 審査結果

| 対象 | 判定と確認した根拠 |
| --- | --- |
| R1：DB削除後の残存ファイル | 解消。検証済み削除receiptを起点に、行が消失していても同一tenantのjob用uploads/artifacts/exports、template用templatesを回収する。行がある場合は既存serviceの参照・状態検査を使用する。再起動時の回収自体が再度中断した場合も起動は失敗し、次の起動で残りを回収できた。 |
| R1：所有範囲・リンク | 合格。対象直下・内部のリンク拒否に加え、既存`LocalStorageService.delete_tree`の親経路検査も維持している。4種類の親ディレクトリ経由の同一owner内リンクと、別ownerへのリンクをLinuxで拒否し、対象外ファイルを保持した。別種の資源が同じUUIDを持つ場合も保持し、削除receiptのない残存ファイルを一括回収しない。 |
| R2：復元による権限復活 | 解消。復元した全利用者を停止し、session/loginを破棄してから外部台帳と削除記録を適用する。復元途中で削除処理が失敗しても全利用者停止と未完成markerを維持し、公開アプリの構築を拒否した。管理CLIによる明示enable後だけ新sessionで確定出力を取得でき、古いcookieは拒否した。再度backup/restoreすると再承認を改めて要求する。 |
| R3：画面テストの不整合 | 解消。変更前の要求assertionだけを戻した内容が6-A開始時のファイルSHAと一致することを確認した。URL、revision、credentials、空ヘッダー、取得内容、エラー、再試行、保存名の検査は保持され、テストの削除・skip・他のassertion変更はない。今回も全176テストとbuildが成功した。 |
| 保守性 | cloud復旧adapterの2ファイルへ変更を限定し、既存Registry・管理CLI・保存serviceを再利用している。追加の権限台帳、schema、専用保存経路を増やしていない。復元後の全員停止という運用上の変更は手順にも明記されている。 |

削除記録の資源IDはJournalでUUIDとして検証される。経路を含む不正IDは外部記録の追記前に拒否されることも確認した。DBが存在しないtenant全体の破損回収や、ホスト管理者による同時ファイル改変までを新たに保証する変更ではない。

## 今回実行した検証

証拠は `D:\llm-ocr-archive\beta\20261016-phase6b`。開始snapshotの`before.json`と`entry-comparison.json`で、6-A終了時から両checkout、通常データ、設定、台帳に差分がないことを確認した。

| 検証 | 結果・記録 |
| --- | --- |
| 修正6-Aの受入再実行 | **37条件成功**。元scriptを同一SHAのまま新領域へコピーし実行。`acceptance-rerun.log`、`after/9c842e/result.json`。DB commit後・ディレクトリ間・部分削除後の復旧、他owner、参照保護、未使用template、使用済み版archive、停止・再有効化・全削除の復元を含む。 |
| 独立した障害境界 | **15条件成功**。`independent.py`、`independent.log`、`i/fc0a3d/result.json`。回収再中断、同一UUIDの別種資源、receiptのない資源、不正ID、復元中断と起動拒否、新領域への再復元、元root保持、実管理CLI、古いcookie拒否、二度目の復元を確認。 |
| Linuxのリンク境界 | **既存6＋追加5条件成功**。`linux-links-2.log`、`linux-extra.log`。実backend imageへ現在appをread-only mountし、network none、uid/gid10001、read-only root、capability除去で実行した。検証コンテナーは`--rm`で終了。 |
| frontend全体 | **27ファイル・176テスト成功**。`frontend-full.log`。 |
| frontend build | **成功**。`frontend-build.log`。 |
| Ruff / Mypy | **対象2ファイル成功**。`ruff.log`、`mypy-targeted.log`。Mypyは`--follow-imports=silent --disable-error-code import-untyped`を明示した限定検査。依存を含む既知の85指摘の解消やbackend全体成功を意味しない。 |
| 変更範囲・過去証拠 | 6-Aの10変更ファイルと現在の内容を照合。フェーズ1〜6-Aの7封印、計26,192ファイルを開始時・終了時に再検証。`reviewed-change-scope.json`、`test-scope.json`、`prior-seals-entry.json`、`prior-seals-final.json`。 |

6-Aの認証127条件・運用41条件、フェーズ6の保存17条件・renderer8条件は封印と実装不変を確認した既存証拠として参照し、今回再実行した件数には含めない。新しいLinux配布image全体の受入、実ブラウザーE2E、実Cognito、実S3の検証とは区別する。

独立検証は実ASGI、SQLite、既存serviceを使用し、OIDC/S3/AIの外部通信は制御・遮断した。管理CLIは現行の`python -m app.cloud.admin enable`を専用復元rootへ実行した。取得した確定revision4のXLSX SHA256は `79f92adb2bc91ccecbda632e2ac4f5e7ca1102b09aff38be8be4e8cfc03a2b5c` で基準と一致し、B2=`00789`・B20=`60000`の出力を維持した。

Linux検証の最初の起動はWSL既定利用者にDocker socketの権限がなく失敗した。`linux-links.log`に保持し、ホスト側の管理利用者で再実行した。コンテナー内は引き続きuid/gid10001であり、製品の実行権限を変更していない。

主な実行コマンドは専用領域の`python -B acceptance.py`・`python -B independent.py`、frontendの`npm test -- --reporter=default`・`npm run build`、対象Ruff/Mypy、`git diff --check`である。検証scriptと使い捨てデータは製品外に保存した。

## 保持と次工程

変更は本報告、AGENTS、Safety Contract、運用手順の現在地のみ。製品コード・製品テスト、通常DBの全表/schema、781 artifact、設定、旧`llm-ocr`、前工程の封印を保持した。OCR provider/model/prompt/schema、physical identity、mapping、crop、補正・確定・出力生成を変更しておらず、**既存OCR contractを維持した**。最終未使用4帳票は使用していない。

実AI API **0回**、実AWS API **0回**。開発台帳は **440/924・残484**。beta追加1,000回は実環境で未初期化・未消費。Git書込、ゴール作成、別エージェント起動、V5/V6精度研究の再開は行っていない。終了時の`after.json`、`preservation.json`、`completion-1/hashes.json`を保持証拠とする。

次は **フェーズ7：Astra / HighでAWS受入・公開準備**。使用プロファイルは **abe-deploy-user**、account **535562976288**、東京。公開ドメイン・費用・権限・完成配布imageを確定し、実HTTPS/Cognito/S3、実マイク、3ブラウザー・実4GiB負荷、全容量backup/restoreを受け入れる必要がある。実マイクは利用者参加で行う。

500MiB/人・全体5GiBは受付時検査であり、生成artifactの厳密な容量上限は未実装のままである。この制約を含む容量対策と実測を公開前に扱う。**R1〜R3の修正要求は解消したが、公開受入条件がすべて満たされたという判定ではない。** フェーズ7の指示・モデル切替前にAWSリソース作成や公開へ先行しない。
