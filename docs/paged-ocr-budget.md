# 複数ページOCRの文書全体API予算

複数ページの読み取りで、外部OCRへの呼出し回数の上限を文書全体で共有・予約する仕組みの仕様。
認識方式や本番資格の条件を変えるものではない。

## 文書単位の予算が要る理由

`OpenAIOcrProvider.extract`の呼出し回数の計数は呼出しごとのものである。
`PagedOcrExecution`はページごとにそれを呼ぶため、ページ別の上限だけではページ数倍まで送信が進む。
ページ別に成功応答の回数を足すだけでは、失敗・並行batch・再起動時の過剰送信を防げない。

## 予算の固定

OpenAI paged受付は`jobPages.ocrCallBudget`へ
`adapter=paged-ocr-job-budget-v1 / scope=job / maxCalls`を固定し、同じ受付transactionで
`PagedOcrBudget`を作る。上限は受付時の`executionConfig.ocr.max_calls_per_job`と一致させる。
mock/disabled、旧単一ページの予算挙動は変更しない。
古いpaged受付へ予算を自動追加せず、必要な記録がないOpenAI実行は送信前に拒否する。

## 予約・失敗・再試行

- 文書単位の上限は全page・全attemptで共有する。再試行や再起動でリセットしない。
- `PagedCallBudget.reserve`は書込みtransaction（`begin_immediate`）内でjobのPROCESSING状態、
  cancel、attempt、page所有権、受付SHA、予算と呼出し台帳の整合を検証する。
  `used_calls`増分と`PagedOcrCall`追加をcommitしてからprovider送信へ進む。
- 予約番号は文書全体で一意。台帳にはpage ID・attempt・予約時刻を保持し、
  provider diagnosticsのcall番号へ渡す。成功responseの`apiCallCount`は当該ページの計数を維持する。
- 失敗・応答消失・予約後cancelを理由に予約を払い戻さない。
  予約済みは「実際に送信した」と同義ではなく、送信の可能性を含めて保守的に消費した枠である。
  commit応答が不明なときは送信せず、commit済みなら使用回数を残す。
- 読み取りも明示的なread transactionを用い、並行予約でcounterと台帳の異なる時点を比較しない。
  閲覧時の残数が古くても、実予約はwrite transaction内で再検証する。
- 上限到達は明示エラーで停止する。現行設定の増額を受付済みjobへ黙って適用しない。
  新しい上限で実行する場合は別の明示的な受付が必要である。

backend serviceが保存を所有し、providerへは`OcrCallBudget` protocolを渡す。
request/response計算やproviderにSQLAlchemy・repositoryの直接依存を追加しない。

## 全ページの最低枠

既存batch方式（通常項目は最大30件、識別項目は各1件）と同じ式で、未完了全ページの
設定済み全項目を送る場合の必要枠を事前計算する。成功ページは保存SHAとページsnapshotの一致を検証して再利用し、
再送の最低枠に含めない。不足時は画像抽出・最初のprovider呼出しより前に拒否する。

これは入力画像に依存するローカル解決を行う前の予定枠であり、実送信数の下限ではない。
従来の空白行等のローカル解決で送信対象が減る場合も、予定全項目分の予算を要求する。
ローカル解決を停止したり、空白項目を追加送信したりして計算値へ合わせない。

各ページの実行中は、後続未完了ページの最低枠を保護する。
先頭ページのoptional high-detail再読取やfallbackが後続の最低枠を使い切らない。
ネットワーク失敗を含め全ページの成功を保証するものではなく、有限予算内で安全に停止する方式である。
認識値・判定、画像/crop、mapping/sourceRegionId/targetPath、association token、prompt/model/schema、
既存の単一ページretry方式は変更しない。paged実行の予算契約である。

## 再試行余力を含む受付

OpenAI paged受付は、現在の
`fieldMappings`と`documentContext.identityFields`からページ別最低回数を同じbatch規則で計算し、
`jobPages.ocrCallBudgetPlan`へ次を固定する。

- `adapter=paged-ocr-retry-reserve-v1`
- ページIDごとの`minimumCalls`
- 全ページの`minimumCalls`
- 同数の`documentRetryReserveCalls`
- 両者を足した`requiredCalls`と設定上限`maxCalls`

catalogとacceptは`maxCalls >= requiredCalls`を要求する。既定上限（`OPENAI_MAX_CALLS_PER_JOB`）は16回であり、
最低8回までのテンプレートは全ページ1回分と同数の再試行余力を持って受付できる。
最低回数が8回を超えるテンプレートは、運用者がhard ceilingを明示的に増やすまで
利用不可とする。項目を削除・batchを偽装・ページを省略して通さない。

受付時点で確保するのは予算上の余力である。timeout、provider error、Schema不成立などの
再送も非払戻しで同じ台帳を消費するため、あらゆる失敗後に文書全体1回分が残る保証ではない。
ただし、上限が最低枠と同値で再試行不能な受付は作らない。既存jobの予算は変更せず、
予算不足・実行設定変更・予算snapshot不成立では「処理を再試行」を表示せず、新しい受付へ戻す。

`OCR_SCHEMA_INVALID`は破損・途中切れの応答を保存せず、設定済み`OPENAI_MAX_RETRIES`と残予算の
範囲で同一batchを再送する。mapping ID、targetPath、sourceRegionId、画像、prompt/schema、
association tokenは変更しない。再送後も不成立なら従来どおりページ失敗として停止する。

## 実装の場所

- `backend/app/services/paged_ocr_budget.py`、`backend/app/db/models.py`
- `backend/app/services/job_page_service.py`、`backend/app/services/paged_ocr_execution.py`、`backend/app/services/ocr_workflow_service.py`
- `backend/app/ocr/base.py`、`backend/app/ocr/openai_provider.py`
