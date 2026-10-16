# OCRの改修前後比較

確認日: 2026-10-02。対象: `C:\Users\hanam\Desktop\llm-ocr-deploy`。

## 状態

比較と検証を完了した。改修開始時のコードに対して、核となるOCR処理の回帰は検出していない。既存OCR contractを維持したと判断する根拠は、保護領域のファイル一致、分割した処理の構文・引数・import一致、同一入力の応答・crop・出力比較、および下記のテスト結果である。
今回、製品コードの修正はしていない。変更したリポジトリファイルは本記録のみ。deployのテスト環境へ不足していたfonttoolsとpsutilを追加した。

## 比較基準

- 改修開始時のmanifest: `D:\llm-ocr-archive\deploy-refactor\20260930-224115\deploy-before.json`。
- 現在のGit HEADではなく、このmanifestに記録されたSHA-256に一致するファイルを基準とした。開始前から存在した未コミット変更も含む。
- 保存済みbaseline、現在のdeploy、元のllm-ocrから、当時のハッシュに一致する678ファイルを比較用フォルダへ復元した。不足0件。元のllm-ocrへの書込みはしていない。
- 証拠の保存先: `D:\llm-ocr-archive\ocr-refactor-audit\20261002-195130`。復元元とハッシュは `source-comparison.json` に記録した。

## 確認結果

| 確認 | 結果 | 証拠 |
| --- | --- | --- |
| backend本体の差分範囲 | result serviceの分割のみ。provider、prompt、schema、workflow、mapping、crop、paged処理は基準と同一 | source-comparison.json |
| result serviceの静的比較 | 43メソッドの引数・デコレーター・処理本体が一致。37委譲の引数・参照importも一致。2データクラスとクラス属性も一致 | fresh-ast-comparison.json |
| LayoutEditorの抽出処理 | 91宣言がTypeScript構文の出力で一致（export追加とコメントを除く） | fresh-frontend-declarations.json |
| フロントエンドAPI引数 | OCR作成、ページ入力binding、retry、保存、approveの引数を維持。変更はプレビューGETの変数抽出と承認後GETの系列ID保持 | frontend-api-call-comparison.json |
| 改修前のOCR契約テスト | 225 passed、56.16秒 | contract-before.log |
| 改修後のOCR契約テスト | 225 passed、58.85秒 | contract-after.log |
| 同一帳票のレビュー・補正・確定・再編集・出力 | 既存の受入テストを両方で実行して成功。25 HTTP応答の内容が一致 | replay-before.json、replay-after.json、replay-comparison.json |
| mapping・受付snapshot境界 | providerをstubにした既存2テストが両方で成功 | mapped-before.log、mapped-after.log |
| crop・mappingの直接比較 | mappedCells、provider送信診断、切り出し画像SHA-256、fieldPreviews、レビュー結果の6応答が一致 | crop-before.json、crop-after.json、crop-comparison.json |
| frontend全テスト | 40ファイル・503 passed | frontend-tests.log |
| frontend build | 成功。500 kB超のchunk警告あり | frontend-build.log |
| backend全テスト | 2,580 passed / 21 failed / 8 errors / 1 skipped、2,530.87秒。失敗・準備エラー29件を下記で再検証 | backend-full-2.log |
| backend失敗対象の順次再検証 | 29 passed、220.70秒。テスト対象を削除・変更せず実行。合計2,609件で成功結果を確認（単一全件実行が成功したという意味ではない） | failed-nodeids.json、failed-recheck.log |

API応答比較では、別DBで生成されたUUIDを出現順に一対一対応させた。参照先の一致・不一致、再利用の関係は維持して比較している。実行時刻（createdAt / updatedAt / confirmedAt / lastConfirmedAt等）とXLSXプロパティの作成・更新時刻は比較から除いた。除外した実際のパスとUUID対応表はcomparison JSONに記録した。OCR文字列、判定、reviewReason、mappingId・sourceRegionIdの対応、boundingBox、revision、出力セル、書式を除外していない。XLSXはZIP内のXML等を比較した。

UIには、離脱時の画像保護、古い非同期応答の無効化、受理済みjobの記録、承認成功後のGET再試行、プレビューの遅延取得という意図的な変更がある。これらを「全挙動が同一」とは扱わない。OCRデータ生成・保存の比較とは区別する。

## テスト環境

- before/after共通: deployの `backend\.venv\Scripts\python.exe`、Python 3.12.10。
- 全体テストの初回は `fontTools` 不足で収集時に停止（exit 2）。製品の失敗ではなく、宣言済みdev依存 `fonttools==4.65.0` の未導入だった。同バージョンをdeployのvenvに導入後、未使用basetempで再実行した。テストの削除・skip・assertion変更はしていない。
- 全体実行中、プロセス清掃を確認する3テストで `psutil` 未導入も判明した。元環境と同じ `psutil==7.2.2` をdeployのvenvへ追加した。これは現在のdev依存宣言に含まれていないため、この検証環境を再現する場合は明示的な導入が必要。
- `PYTHONDONTWRITEBYTECODE=1`、`RUN_OPENAI_INTEGRATION_TESTS=false`。pytestのbasetempはすべて上記Dドライブ配下の未使用絶対パスを使用した。
- 比較用baselineの契約runnerだけ、Dドライブの未使用basetempを要求する現runnerに置換した。アプリケーションコードは当時のハッシュを維持した。
- 旧runnerと現runnerの14対象が同じ順序で一致することをASTから確認した（contract-target-comparison.json）。比較した現コード697ファイルのハッシュも保存した（tested-current-hashes.json）。

実行した主なコマンド（basetempは各ログに対応する別名）:

```powershell
python -B scripts/verify_ocr_contract.py --basetemp=D:\llm-ocr-archive\ocr-refactor-audit\20261002-195130\contract-after
# backendディレクトリ
python -B -m pytest tests -vv --tb=short --durations=8 --basetemp=D:\llm-ocr-archive\ocr-refactor-audit\20261002-195130\backend-full-2
# frontendディレクトリ
npm test -- --reporter=dot
npm run build
```

## 全体実行の失敗と切り分け

- 最初のページレビュー準備エラーは `Office render timed out`。同じテストは単独実行で成功し、最後の29件の順次再検証でも成功した（review-error-repro.log、failed-recheck.log）。
- 調査のために開始した単独再現試験と全体実行が重なった。rendererは `Global\\UtsushibiReferenceRenderer-v1` によりプロセスをまたいで排他制御される。代表例を調べると、環境固定テストはgeometry/referenceの呼出し前に `RENDERER_BUSY` で拒否されていた（pinned-repro.log）。追加試験を並行させたことは検証手順上の問題であり、製品の排他制御を緩めて解消していない。以降は全体実行の終了を確認してから失敗29件を1プロセスで順に実行した。
- そのほかにWSL起動の15秒タイムアウト、`Glyph bootstrap Python changed during validation` が記録された。後者の変更原因そのものまでは特定していない。該当保護処理は基準コードと同一であり、ガードを無効化せず順次再検証で通過した。初回失敗をなかったことにはしない。
- 3件の `ModuleNotFoundError: psutil` は、テスト環境への依存追加後に成功した。
- 失敗対象29件すべてが、製品コード・テストコード・timeout・assertionを変更せず成功した。外部環境を含む初回実行の不安定性が完全になくなったことまでは証明していない。

## 検証境界と終了確認

- 実OpenAI API・実Google APIの呼出しは0回。providerは既存fixture / stub / mockを使用した。実モデルの非決定的な応答や帳票全種類の読取精度を保証するものではない。
- フロントエンドの検証は既存DOMテストとbuild。今回、ブラウザーの実画面E2Eは実行していない。
- 実OpenAI呼出しを要求する既存opt-inテスト1件は、指定した設定どおりskipped。これを読取精度の実測として数えていない。
- 使用済みの一時collector / AST確認 / baseline復元スクリプト5本を削除済み。結果JSON・ログ・比較用baseline・既存受入テストと入力fixtureはDドライブに保持した。
- 終了時に697ファイルのハッシュ変化0件、既存tracked差分の変更0件、追加ファイルは本記録だけ、`git diff --check` 成功を確認した。全体実行で失敗・準備エラーになった29 node IDすべてに再検証の成功が対応することも確認した（final-audit.json / status-after.txt）。
