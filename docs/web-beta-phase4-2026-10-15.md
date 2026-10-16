# Webベータ フェーズ4：製品画面と手動復帰

2026-10-15。担当予定モデルはGPT-6.1 Sol / High。対象は `C:\Users\hanam\Desktop\llm-ocr-deploy` のみ。[全体計画](web-beta-phase1-2026-10-15.md)の製品UI工程を実施した。精度・高速化の追加研究、通常DB移行、公開、ゴール作成、別エージェント、Git書込は行っていない。

## 変更と原因

| 対象 | 原因・変更境界 |
| --- | --- |
| `backend/app/services/ocr_result_service.py` | paged jobに単一OcrResultがなく404が先に発生し、画面が専用レビューへ進めなかった。受付snapshotのjobPagesを先に判定する。単一補正・確定への拒否も維持し、保存データを変更しない |
| `frontend/src/pages/ReviewPage.tsx` | 単一結果を取得してから編集sessionを初期化。pagedと確定済み結果には単一編集sessionを作らない。取得失敗時の再試行を追加 |
| `frontend/src/pages/PagedReviewPage.tsx` | 使用テンプレート名を表示。画像下のページ移動が固定footerに覆われたため、画像headerへ移動。同じpage identityとhandlerを使用 |
| `frontend/src/auth/{AuthBoundary,transport}.ts*`、`App.tsx` | 未保存・保留中logoutの確認、正常logout時のworkspace終了、遅れて返る旧session応答の無効化。通常の画面離脱guardを再利用 |
| `frontend/src/editing/{EditingControls,TemplateAnalysisPanel,templateAnalysisStore}.ts*` | 音声・文字指示の利用可否を表示。案なし時の手動復帰を明示。受付前の明確な利用不可503を処理不明と区別する。未知5xx/timeoutではdraftを保持し回復確認を要求 |
| `frontend/src/pages/{HomePage,JobPage,TemplatePage}.tsx`、`styles.css` | ベータ表示、接続回復の文言、手動編集継続を整理。帳票用の業務画面を維持 |
| `deploy/compose{,.cloud}.yaml` | 共通手動編集APIをcloudで有効化。通常composeのoriginを実際のBETA_HTTP_PORTへ合わせる。cloudのHTTPS origin、認証・CSRF・有料AI停止は維持 |

AIテンプレート案は「案を反映」→「確認して保存」の既存経路を使う。連続音声と選択欄だけの局所録音を残し、表選択は任意のままとした。音声利用不可でも手動編集・保存を継続できる。独自保存経路や新しいOCR経路は追加していない。

## 検証

証拠の正本は `D:\llm-ocr-archive\beta\20261015-phase4`。最終保持比較は `preservation.json`、ソース・証拠の固定は `completion-1/hashes.json`。

- frontend対象8ファイル **54テスト成功**、`npm run build`成功。routing、利用不可表示、未保存logout、旧session応答、案なし手動復帰、受付前503と未知504の区別を検証した。実行一覧は `frontend-tests-final.log`、buildは `frontend-build-final.log`。
- 変更前imageの対象ファイルSHAを開始時snapshotと照合。同一の専用2ページfixtureで旧404→新409 `MULTI_PAGE_REVIEW_REQUIRED`、専用レビュー200、存在しないjob404、単一確定拒否・書込0を確認した。`routing-comparison.json`に変更前後の内容を保存した。
- 受付page/input/reference SHA、確定revision4、全業務テーブル、XLSXのB2=`00789`・B20=`60000`を保持。XLSX SHA256は `79f92adb2bc91ccecbda632e2ac4f5e7ca1102b09aff38be8be4e8cfc03a2b5c` で変更前後一致した。
- 制御HTTP provider＋実ASGI/SQLiteの **17条件成功**。running中の案、scope/digest拒否、手動同値編集・geometry競合保護、冪等性、旧案失効、明示保存と別接続の再読込を確認。54物理セル・22入力/mapping/cropを保持した。`controlled/integration/ce9552f9-df65-4f6d-a8e3-94baea4990cd/result.json`。
- Chromeの制御AIで案反映→明示保存revision2→手修正revision3→reloadを確認。54セルのID/矩形・22mappingを保持し、手修正した記号 `○◎` が別接続でも残った。`controlled-ui-proof.json`、`controlled-ui-reload-proof.json`、`chrome-ai-reloaded.txt`。
- 実コンテナー5304のChromeで2ページ表示・ページ移動・60000の対応crop読込を確認。AI解析は202受付後、planned/reserved/completedCallsすべて0・案なし失敗となり、「手動編集に戻る」で保存保留解除、音声利用不可表示、authoring revision1の保持を確認。これは受付前503の実画面再現ではない。503/未知504の区別は対象frontendテストで検証した。`chrome-paged-review.txt`、`chrome-provider-unavailable.txt`、`chrome-manual-return.txt`。
- 対象backend静的検査は変更前後比較。ruffの既存1件、mypyの既存6件が残り、追加指摘0。全件正常とは扱わない。`static-comparison.json`とbefore/afterログ。
- 実行imageと現ソースを照合し、backend389ファイル・frontend16配布ファイルが一致。`image-verification.json`。

主な再現入口は、backend仮想環境から `python -B controlled/integration.py`（製品backendを作業ディレクトリ）、WSLの `sh baseline-routing.sh` / `sh probe-after.sh`、frontendの次の対象試験。いずれも本工程archive内の固定fixtureと専用DBを使用する。

```text
npm test -- src/pages/ReviewPage.routing.test.tsx src/editing/EditingControls.test.tsx src/auth/AuthBoundary.test.tsx src/auth/transport.test.ts src/editing/TemplateAnalysisPanel.test.tsx src/editing/templateAnalysisStore.test.ts src/pages/OcrPage.pagedReview.test.tsx src/pages/JobPage.test.tsx
npm run build
```

## 保持・未検証・次工程

実AI API **0回**、実AWS **0回**。開発台帳 **440/924・残484**、Webベータ追加1,000回は未設定。制御ASGIの専用予約4件と制御画面の専用予約は通常台帳へ混入させない。制御providerは実モデルの精度・応答時間の証拠ではない。開発用R02を使用し、最終未使用4帳票を評価・調整へ転用していない。

通常データ・schema・artifact・設定hash・旧checkout・既存差分・過去封印を開始時基準と照合する。provider、model、prompt、schema、物理セル、対応、crop、補正、revision、確定、出力生成の意味は変更しておらず、既存OCR contractを維持した。

実Cognito、実AWS/ドメイン、実マイク、全体回帰、実4GiBの継続負荷・公開は未検証。複数ページ結果の編集は既存の手動レビューを使い、今回、ページ単位の音声対象契約は新設していない。実マイクB04は利用者参加でフェーズ7に実施する。制御画面のWindows専用runtimeは検証後に停止し、5304の専用コンテナーだけを確認用に残す。

次は **フェーズ5：GPT-6.1 Sol / High**。共通送信予約・外部台帳、削除、backup/restore、配備定義と隔離検証を実装する。共有quota接続までcloudの有料AIは停止を維持する。広い回帰と独立工程審査はフェーズ6、AWS受入・公開はフェーズ7とする。
