# Webベータ 修正6-A：削除再適用・復元権限・画面回帰

2026-10-16。[フェーズ6の独立審査](web-beta-phase6-review-2026-10-16.md)のR1〜R3を修正した。計画上の担当はGPT-6.1 Sol / High。実装・隔離検証を完了し、**6-Bの独立再審査待ち**とする。フェーズ7のAWS受入・公開は開始しない。

対象は `C:\Users\hanam\Desktop\llm-ocr-deploy` のみ。通常DB・設定・旧checkoutを変更せず、Git書込、ゴール作成、別エージェント起動、精度V5/V6の研究再開は行っていない。

## 変更した境界

| 指摘 | 実装 | 意図・互換性 |
| --- | --- | --- |
| R1：DB削除後のファイル残存 | `backend/app/cloud/operations.py`で、削除receiptの資源がDBに存在しない場合も、同一tenant・resource IDの残存ファイルを回収する。jobはuploads/artifacts/exports、templateはtemplatesだけを対象とする。 | 外部削除記録を復旧の正本として使う。既存行がある場合の参照・状態検査と、使用済みtemplateのarchiveは従来serviceを使う。シンボリックリンクは対象直下・内部・リンク切れとも拒否し、他の資源へ追従しない。通常ローカル削除serviceは変更していない。 |
| R2：復元で停止アカウントが復活 | `backend/app/cloud/backup.py`のrestoreで、全session/loginを破棄すると同時に、**復元した全利用者をactive=0**にする。返却結果へ`usersRequireReadmission=true`を追加する。 | 古いbackupだけでは現在の利用許可を証明できない。復元後は管理者が現在の利用許可を確認したsubだけを既存`admin enable`で明示再承認する。停止・再有効化用の第二の外部台帳や新schemaは増やさない。以前の「元々有効だった利用者は直ちに再ログイン可能」という復旧動作を意図的に廃止する。 |
| R3：3件の画面テスト不整合 | `frontend/src/api.pagedOutput.test.ts`と`frontend/src/components/ExcelExportActions.test.tsx`で、Headersの表現を通常のキー/値へ変換してから要求設定を比較する。送信回数も明示検証する。 | URL・credentials・空ヘッダーを含む設定全体の一致を検査する。取得内容、revision不一致拒否、APIエラー拒否、失敗後の再試行、書類名・ダウンロード名の既存assertionを維持する。製品の認証transportは変更していない。既存のCSRFヘッダー付与・外部送信拒否テストも通過した。 |

OCR provider/model/prompt/schema、物理セル・mapping・crop、補正・確定・XLSX生成経路は変更していない。修正対象はcloudの復旧adapterと既存テストである。

## 修正前後の受入

証拠は `D:\llm-ocr-archive\beta\20261016-phase6a`。開始snapshotは`before.json`。フェーズ6終了時との一致を`entry-phase6-match.json`で確認した。修正前の専用backendコピーは413ファイルを開始SHAと照合し、`baseline-source-hashes.json`で固定した。通常DBや秘密設定をこのコピーへ持ち込んでいない。

同じ受入script・同じ固定帳票fixtureで、**修正前37条件中16失敗 → 修正後37条件すべて成功**。`acceptance-before-final.log`と`acceptance-after-final.log`、各`before/*/result.json`・`after/*/result.json`に記録した。

- job削除のDB commit後、最初のディレクトリ削除前・ディレクトリ間・artifact内の一部ファイル削除後で中断。実cloudアプリの再起動入口で残存36/31/30ファイルをすべて回収し、削除jobの404、繰り返し再適用、後続backup/restoreに原本・生成物が残らないことを確認。
- 版を持つ未使用templateの親・版削除がcommitされた直後にファイル削除を中断し、再適用で専用原本を回収。削除receipt前の失敗ではDB・ファイルを保持する。
- templateの元job参照は`JOB_IN_USE`で拒否し、新たな削除receiptを発行しない。使用済みtemplateはarchiveで版とファイルを維持し、確定XLSXが同じSHAで取得できる。
- backup前から停止、backup後に停止、停止→再有効化、全利用者削除を別ownerで試す。復元直後は全ownerへの新session発行と旧cookieを拒否。既存subへの`admit`再実行も勝手に有効化しない。管理者が確認した生存ownerだけの`enable`後は実APIで確定出力を取得でき、他ownerは停止を維持し、削除済みownerの再有効化は拒否する。

他利用者の同じjob ID・原本/生成物SHAを保持し、確定revision4のXLSX SHA256は `79f92adb2bc91ccecbda632e2ac4f5e7ca1102b09aff38be8be4e8cfc03a2b5c`。B2=`00789`、B20=`60000`の基準を維持した。実ASGI/SQLite、既存保存・出力serviceを使い、外部OIDC/S3/AIだけを制御応答にして通信を遮断した。実Cognito受入とは扱わない。

## 関連回帰・静的検査

| 検証 | 結果・証拠 |
| --- | --- |
| 認証・利用者分離・再起動 | **127条件成功**、`auth-regression.log` / `phase3/acceptance-result.json`。フェーズ6の127条件を専用コピーで再実行。 |
| 共通予算・削除・復元 | **41条件成功**、`operations-regression.log` / `phase5/a/30b1d5b1/result.json`。元39条件を維持し、全利用者停止と暗黙の再ログイン拒否を追加。変更した復旧契約に従い、管理者の明示enable後に元のXLSX/予約残数検査を行う。適応内容は`regression-adaptations.json`へ記録。 |
| Linuxの削除範囲・link拒否 | **6条件成功**、`linux-links-2.log`。実backend imageに現在のappをread-only mountし、非root・cap除去・network none・read-only rootで実行。対象外owner/resource、同一ownerへのtop link、他ownerへのtop/nested link、リンク切れを確認。検証containerは`--rm`で終了。 |
| frontend全体 | **27ファイル・176テスト成功**、`frontend-full.log`。前工程の3失敗を解消し、削除・skipなし。 |
| frontend build | **成功**、`frontend-build.log`。 |
| 対象Ruff | **成功**、`ruff-final.log`。`app/cloud/operations.py app/cloud/backup.py`。 |
| 対象Mypy | **2ファイル成功**、`mypy-targeted.log`。`--follow-imports=silent --disable-error-code import-untyped`を明示。フェーズ6の依存を含む85指摘を今回すべて解消したとは扱わず、backend全体の型検査成功とも報告しない。 |
| 差分・保持 | `git diff --check`、参照path、通常DB全表/schema・設定・artifact・旧checkout・前封印を照合。最終記録は`preservation.json`、`completion-1/hashes.json`。 |

受入の初版36条件も修正前16失敗→修正後全成功だったが、未使用templateの版削除を明示する1条件を加えて最終37条件を再比較した。旧結果は削除していない。Linux用検証の初回はscriptのimport path不足で中断したため、検証script側に`/app`を追加して再実行した。`linux-links.log`を保持し、製品側の失敗と区別する。

主な実行入口はbackend仮想環境の`python -B acceptance.py`、専用コピーの`phase3/acceptance.py`と`phase5/acceptance.py`、frontendの`npm test -- --reporter=default`・`npm run build`。検証script・使い捨てfixtureは製品外に保持する。

## 保持・運用上の変更・次工程

復元後の全利用者停止と再承認を[運用手順](web-beta-operations.md)へ反映した。データ・tenant ID・subの対応を保持したまま、利用権限だけを安全側へ停止する。利用者ごとに新しいtenantを作り直さず、削除済みsubを再有効化しない。cloud registry v2、外部台帳形式、共通上限は維持する。

実AI API **0回**、実AWS API **0回**、開発台帳 **440/924・残484**。beta追加1,000回は実環境で未初期化・未消費。今回の仮台帳試験を許可枠から差し引かない。通常DB・全業務表/schema・781 artifact・設定hash・旧`llm-ocr`・過去封印を保持し、**既存OCR contractを維持した**。最終未使用4帳票は使っていない。

実AWS/S3/Cognito、実マイク、実4GiB・3ブラウザー負荷、生成物の厳密な容量上限、全容量backup/restore、公開ドメイン・費用は引き続き公開前の残条件。今回の修正ではrenderer本体・配備imageを変更・再発行していない。Linux検証は現在appをmountした隔離検証であり、新しい完成配布imageの受入ではない。

次は **6-B：Astra / XHighで独立再審査**。R1の所有範囲・中断回復、R2の全利用者停止・明示再承認、R3の意味上の検証保持と、今回の封印を再確認する。再審査・フェーズ7をこの工程で自動開始しない。
