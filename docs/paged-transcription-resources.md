# ページ別OCRの受付時prompt・応答schema

2026-09-13。本番化に向けた受付snapshotの補完。ACTIVE/本番受付gateは解除しない。

## 原因と変更境界

`JobPageService.execution_config`は設定値とprompt/schemaの版番号を保存するが、
providerは各呼出しで現在の`transcription-prompt.md`を読み込んでいた。
同じ設定・画像・manifestのままloaderの返す本文だけを変更すると、送信instructionsが変わる。
この再現は外部APIを呼ばない固定responseで確認した。

新しいOpenAI paged受付だけに`transcriptionResources`を追加する。
adapterは`paged-transcription-resources-v1`。受付時の本文全文とUTF-8 SHA、
`CompactTranscriptionBatch.model_json_schema()`の内容とcanonical JSON SHAを保存し、
文書全体の`jobPagesSha256`と既存の永続予算snapshot SHAにも含める。
既存jobを後付け更新しない。DB schema変更は不要。

`JobPageService.validate`で全体SHA・各resourceのSHA・型・adapter・現在の応答model schemaを
検証してから画像処理へ進む。workflowもprovider生成前に再確認する。
欠落・破損・未知adapterは拒否し、現在のファイルから補充しない。
schemaが変わった場合は`PAGE_TRANSCRIPTION_SCHEMA_CHANGED`で拒否する。
保存したJSON schemaから任意のPython型を生成・実行する方式ではない。

本文は検証後の`OcrProviderRequest.transcription_prompt`へ渡し、通常batch・識別項目batch・
高精細再試行用の部分requestへ保持する。現在のpromptファイルが変更・欠落していても、
新paged jobは受付時の本文を使う。legacy requestは既定値Noneのまま従来loaderを使う。
provider/model、schemaクラス、画像、manifest、compact ID/token、mapping/crop、
返却値の解釈、レビュー・補正・出力を変更していない。

## 検証

- 変更前OCR契約は直前工程の210 passed（42.13秒）。
- 追加request試験は変更前2 failed（保存本文の引渡し口が未実装）、変更後2 passed（0.09秒）。
- offline SDK送信境界で、legacyの本文と同じ保存本文を使用した場合に、全送信引数と
  mapped transcription responseが一致する。現在のloaderがFileNotFoundErrorでも保存本文で成功する。
- 62項目の通常/識別4batchと再試行部分requestで本文が保持される。
- resource単体で本文・改行・Unicode・SHA不変、欠落・破損・schema変更・未知属性を検証する。
- 統合試験は人工テンプレートだけの受付gate fixtureを使う。実版の認定情報を変更しない。
  DB受付→両ページrequestで保存本文とmapping IDが一致し、予算台帳とsnapshot不変を検査する。
  resource欠落・schema変更では画像処理/provider生成/予算消費の前に拒否する。
- 最初の対象試験はunit/integrationの同名moduleによるcollection errorを検出し、
  integration側を`test_paged_transcription_acceptance.py`へ改名した。
- 次の対象試験は56 passed / 5 errors。並行していた全体試験とOfficeのglobal mutexが競合し、
  fixture作成が`RENDERER_BUSY`で失敗した。排他制約・assertionを変更せず、
  自身の全体試験processを照合して停止し、対象→全体の順序へ変更した。
- 対象8 PythonファイルのRuff、product 5 moduleの限定mypyは通過。
- 直列対象: 61 passed（116.31秒）。新規20件、既存paged予算27件、既存provider予算14件。
  `pytest tests/unit/test_paged_transcription_resources.py tests/unit/test_paged_transcription_request.py
  tests/integration/test_paged_transcription_acceptance.py tests/integration/test_paged_call_budget.py
  tests/unit/test_openai_call_budget.py -q -o addopts='' -p no:cacheprovider
  --basetemp=.pt-paged-resources-target3-0913`（backend venv）。
- 変更後OCR契約: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、
  210 passed / 1 warning（53.30秒）。対象範囲では既存OCR contractを維持した。
- backend全体は同venvで`pytest tests -q -o addopts='' -p no:cacheprovider
  --basetemp=.pt-paged-resources-full-0913`を直列開始した。結果は追記する。
- 現在の実promptはUTF-8 875 bytes、SHA
  `3ecabcb5e70138fca0e25674760a7b41d66f4ef456e6fbea78e74d22f6e226e8`。
  応答model schema SHAは`3706a080be4bff52e6dcfa2ec009027838151efd422475ba5cb1db53c6b79208`。
  read-onlyでcapture→validate一致を確認した。原本・実版・過去jobへ保存していない。
- 先行予算/renderer工程と本工程の完了・失敗・中断済みテスト専用14ディレクトリを
  `C:/Users/hanam/.codex/tmp/llmocr-paged-budget-and-resources-tests-20260913`へ移動して保持した。
  削除は行っていない。進行中の全体試験ディレクトリは移動していない。

## 未完了の境界

このschema保存はPydantic応答modelのJSON schemaであり、SDKが加工する最終wire schemaや
SDK/Python/nativeライブラリの完全なruntime identityではない。
バイナリ・モデル等の依存固定、WSL font/runtime/geometry/crop資格は別途必要である。
ONNX segmenterは現実装でも推論を有効化しておらず、この変更で有効化しない。
設定のmodel_versionを実モデル内容の検証に代用しない。

実OpenAI/Google呼出し0回。実手書き認識誤り・実association誤対応は未測定。
稼働UI・実テンプレート有効化の確認は未完了。frontendの変更・再ビルドは本工程に含まない。

変更ファイル: `backend/app/services/paged_transcription_resources.py`、
`job_page_service.py`、`ocr_workflow_service.py`、`backend/app/ocr/base.py`、
`openai_provider.py`、新規unit 2ファイル・integration 1ファイル。
