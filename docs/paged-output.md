# 全ページの記入済みXLSX出力 — 2026-09-13

本番化要求に対する継続工程。全ページの確定値から記入済みXLSXを取得する専用経路を追加した。
書類一覧統合・実手書きOCR・geometry/font/runtime/crop qualification・ACTIVE化は未完了。
テンプレートカードの印刷用XLSX/PDFとは別の成果物であり、物理印刷は実行しない。

## 原因と変更境界

既存の結果出力は単一OcrResultと準備jobの原本を参照し、openpyxlの全体再保存、
sheet名変更・他sheet除去を含む。ここへPage 1だけを渡すことも、全ページを現在の版から
再構成することも受付時の物理対応を維持できない。新経路を分離し、旧処理を変更しない。

`paged_document_snapshot.py`の新規受付だけに`outputAdapter=ooxml-paged-values-v1`を固定する。
既存schemaVersion 2でadapter未指定の受付はレビュー・確定を維持するが、出力は拒否する。
新adapterの後付け・現在設定へのfallbackはない。将来adapterを変更する場合も明示的な互換設計を要する。

## API・状態・所有権

- `GET /api/v1/ocr-jobs/{jobId}/page-review/output.xlsx?revision=N`。既存認証router配下。
- 指定revisionの全ページ確定状態とPagedConfirmationの値・受付SHA一致を要求する。
  全ページのpage ID、mapping ID、targetPath、sourceRegionId、元cell/range/parts/anchorを照合する。
- 原本は受付jobの所有コピーのみ。path所属とSHAを確認し、読取後のbytesも再照合する。
  現在のTemplateVersion、準備job、画面の位置やラベルは出力先決定に使わない。
- SQLiteの一貫したread transactionでsnapshotを取得し、出力生成前に解放する。
  生成後も確定状態・revision・受付SHAを再確認し、途中reopenでは古い出力を拒否する。
- MIMEはXLSX、Content-DispositionはUTF-8名、Cache-Controlはno-store。
  X-Result-Revisionと出力SHAのETagを返す。生成結果はその要求へ返すだけで、DB値・履歴を変更しない。
- 失敗で確定を解除しない。Google同期・書類一覧登録を成功したことにしない。

## 原本保持writer

`paged_workbook.py`は既存の原本保持adapterと同じZIP/XML安全検査を先に行う。
数式・外部参照・active content・未対応拡張は拒否する。openpyxlは読取と既存の結合保護検査だけに使い、
保存serializerには使わない。変更するのは対象worksheetの所有セル値と検証済み結合要素のみ。
未編集セル・style・列幅/行高・原sheet名・非表示管理sheet・他ZIP part・印刷設定を保持する。
新しい出力値が元dimensionを超える場合だけ明示座標でdimensionを拡張する。

- 通常セルは受付cell IDから元XLSX座標を照合。元結合はtop-left anchorだけを許可する。
- 手動結合は既存の連続範囲・selected source・内部値・literal一致・重複保護を再利用する。
  元結合内部の隠れた値を破棄する要求は拒否し、別出力項目が結合内部へ入る場合も拒否する。
- 同じ物理anchorへの値は同一の場合だけ許可し、異なる値・空白との競合は拒否する。
- 値は全て文字列。blank確定は当該入力セルの元placeholderを消す。旧単一出力のblank仕様は変更しない。
- inlineStrにより先頭`=`を数式にしない。先頭ゼロ、XML記号、改行、文字列中の`_xHHHH_`を保持する。
  XML不正文字と32767文字超は拒否する。

文字列形式はMicrosoftの[InlineString仕様](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.spreadsheet.inlinestring?view=openxml-3.0.1)と
[ST_Xstringのエスケープ仕様](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oe376/bd0aa042-434a-4ca7-b25f-4e1fd25a954d)に照合した。
内部printLayoutのページ設定を記入済み結果へ自動適用する変更はない。

## フロントエンド

`PagedReviewPage`の確定済み結果に「全ページの記入済みXLSXを取得」を表示する。
取得APIのrevision headerを照合してからダウンロードし、古いjobの遅延応答を破棄する。
取得中もページ切替・crop閲覧を維持し、二重取得と同画面のreopenだけを止める。
失敗時は値・確定を保持して再取得による復旧を案内する。

## 検証

変更ファイルは`services/paged_workbook.py`、`services/paged_output_service.py`、
`services/paged_document_snapshot.py`、`services/paged_review_service.py`、
`api/v1/routes/job_pages.py`、frontendの`api.ts`と`pages/PagedReviewPage.tsx`。
新規試験は`test_paged_workbook.py`、`test_paged_output.py`、`test_paged_output_renderer.py`、
`api.pagedOutput.test.ts`。既存PagedReviewPage試験と確定試験の出力可否期待値を新契約へ拡張した。
AGENTS・Safety Contract・関連工程文書も更新した。今回の追加DB migrationはない。

- 変更前OCR contract: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、210 passed（50.40秒）。
- writer単体16件: 未変更part/要素bytes、元sheet/書式、prefix、挿入順、結合範囲と全罫線属性、
  SHA/adapter/identity/内部literal不正、数式様文字列、blank、重複先を検証。
- API実DB fixture 3件: 全ページ補正→確定→出力、mapping/crop不変、版変更非依存、原本改変拒否、
  失敗時確定保持、生成中reopen拒否、再確定revision、旧adapterなしの非移行を検証（79.22秒）。
- 隔離WSL実試験1件: 生成XLSXを実LibreOfficeでFODS/PDF/PNGへ変換。
  先頭ゼロ・数式様文字列・エスケープ様文字列・改行・日本語が元値と一致、式属性なし。
  生成PDFの目視で文字欠け・はみ出しを認めなかった。networkDeniedとreadonly font mountも確認。
- Spreadsheetsスキルで別readerによるread-only inspect/renderも実施した。ファイルを再保存していない。
  補助readerは`_xHHHH_`のOOXML escapeを展開せず、プレビューでは先頭ゼロも落ちた。
  同じファイルのOffice実読取・PDFでは保持されたため、補助プレビューをOffice互換の証拠に代用しない。
- 実DRAFT版`6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`をSQLite `mode=ro`/query_onlyで参照し、
  4ページの151 binding/151 anchorへ座標ごとの検証用文字列を出力した。全151元セルの値一致、
  原本SHA `aa1d088cf5ebae9cfca0f42d0ac3cbe1d8b23f7067196407f6ae3ce9c3cc3176`不変、
  全ZIP part中で対象sheet1.xmlだけの変更を確認。成果物31977bytes。実OCR・実確定ではない。
  ページ別mapping数は60/51/37/3。mark/compositeは0だが、識別field 1件とdocumentName設定があり、
  この出力工程時点の`JobPageService.accept`はqualificationの後にも`PRINT_PAGE_CONFIG_UNSUPPORTED`で拒否した。
  後続の[文書metadata対応](paged-document-metadata.md)で設定を削除せずページ所属を扱うようにした。
  本番qualificationと有効化は未完了。
- frontend全体: `npm test -- --run`、34ファイル366 passed（13.76秒）。
  `npm run build`成功、既存のbundle size警告あり。
- Ruff対象8 Pythonファイルは成功。`mypy --follow-imports=silent`で変更5モジュールは成功。
  importsまで通常追跡する実行では、対象外4モジュールの未導入stubと型不整合31件が残る。
  全体mypy成功とは扱わず、対象外のOCR取込/座標処理をこの出力修正に含めて変更していない。
- backend全体: `.venv/Scripts/python.exe -m pytest tests -q -o addopts='' -p no:cacheprovider
  --basetemp=.pt-paged-output-full-0913`（cwd=backend）、964 passed / 1 skipped / 3 warnings（1191.74秒）。
  新しい実WSL試験もこの実行で通過。skipとdeprecated警告は従来のもの。

- 変更後OCR contract: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、
  210 passed / 1 warning（53.98秒）。旧単一ページの既存OCR contractを維持し、新paged出力を
  意図的な別契約として拡張した。実応答association mismatch/recognition errorは未測定。

実アプリの再起動はpolicyで拒否され、前工程で手動起動を依頼済み。新API/UIのlive反映は未確認。
この工程では実DB・既存原本・ACTIVE gateを変更せず、OpenAI/Google呼出しは0。
fixtureによるprovider/gate置換は実OCR・本番認定の証拠ではなく、OCR安全性を完全には検証できていない。

終了済みの当工程テスト用5ディレクトリは削除せず、
`C:/Users/hanam/.codex/tmp/llmocr-paged-output-tests-20260913`へ移動して保持した。
補助readerと実原本への仮値診断は`C:/Users/hanam/.codex/tmp/paged-output-inspection-20260913`。
既存の未コミット差分を保持し、staging・commit・pushは行っていない。最終`git diff --check`成功。
