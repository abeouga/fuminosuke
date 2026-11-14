# ページ別OCRの受付時prompt・応答schema

複数ページの読み取りで、外部OCRへ送るprompt本文と応答schemaを受付時に固定し、実行時に再検証する仕様。
ACTIVE/本番受付gateの条件は変えない。

## 固定する理由

`JobPageService.execution_config`は設定値とprompt/schemaの版番号を保存する。
providerが各呼出しで現在の`transcription-prompt.md`を読み込むと、同じ設定・画像・manifestのままでも、
loaderの返す本文が変われば送信instructionsが変わる。受付時の条件で実行するには、本文そのものを固定する必要がある。

## 仕様

OpenAI paged受付は`transcriptionResources`を持つ。
adapterは`paged-transcription-resources-v1`。受付時の本文全文とUTF-8 SHA、
`CompactTranscriptionBatch.model_json_schema()`の内容とcanonical JSON SHAを保存し、
文書全体の`jobPagesSha256`と永続予算snapshot SHA（[文書全体API予算](paged-ocr-budget.md)）にも含める。
既存jobを後付け更新しない。DB schema変更は不要。

`JobPageService.validate`で全体SHA・各resourceのSHA・型・adapter・現在の応答model schemaを
検証してから画像処理へ進む。workflowもprovider生成前に再確認する。
欠落・破損・未知adapterは拒否し、現在のファイルから補充しない。
schemaが変わった場合は`PAGE_TRANSCRIPTION_SCHEMA_CHANGED`で拒否する。
保存したJSON schemaから任意のPython型を生成・実行する方式ではない。

本文は検証後の`OcrProviderRequest.transcription_prompt`へ渡し、通常batch・識別項目batch・
高精細再試行用の部分requestへ保持する。現在のpromptファイルが変更・欠落していても、
paged jobは受付時の本文を使う。legacy requestは既定値Noneのまま従来loaderを使う。
provider/model、schemaクラス、画像、manifest、compact ID/token、mapping/crop、
返却値の解釈、レビュー・補正・出力は変えない。

## 固定していないもの

このschema保存はPydantic応答modelのJSON schemaであり、SDKが加工する最終wire schemaや
SDK/Python/nativeライブラリの完全なruntime identityではない。
バイナリ・モデル等の依存固定、WSL font/runtime/geometry/crop資格は別の仕組みで扱う。
ONNX segmenterは既定では使わない（ライセンス確認済みモデルが設定されていなければ拒否する）。
設定のmodel_versionを実モデル内容の検証に代用しない。

## 実装の場所

`backend/app/services/paged_transcription_resources.py`、`backend/app/services/job_page_service.py`、
`backend/app/services/ocr_workflow_service.py`、`backend/app/ocr/base.py`、`backend/app/ocr/openai_provider.py`。
