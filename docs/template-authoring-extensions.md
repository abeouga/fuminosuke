# テンプレート手動編集の拡張

2026-09-10。作業開始時HEAD: `b613c11a72b20d4c7cafc7ec3dd7ddf9adf52b55`。
開始時のworktreeはclean。commit / pushは行っていない。

## 実装

### 同名項目

意味づけの作成時に現在のLayoutDefinition内で保存先の使用状況を確認する。
別の記入欄ですでに使用している単独項目には、新しいlabelPool項目と保存先を作成する。
例は `漢字 → 漢字2 → 漢字3`、保存・再読込後の次は `漢字4`。
既存の帳票文字 `text` / `printedLabelText`、既存relationのID・targetPathは変更しない。
別テンプレートの候補は連番の根拠にしない。既存の繰り返し配列の行割当ては維持する。
保存時の既存重複修復ロジックに依存して、古い項目をリネームする方式ではない。

### 多角形

「欄の種類」→「枠を追加」で四角形／多角形を選択する。初期値は四角形。
頂点を順番にクリックし、始点クリックまたは「多角形を確定」で閉じる。
最後の頂点の取消し、作図キャンセル、Escapeによる終了が可能。

LayoutCellに任意の `shapeType` とcanonical座標の `vertices` を追加した。
旧データの省略値はrectangleと空の頂点列。外接矩形と頂点範囲の一致、
3〜100頂点、重複・自己交差・面積0の拒否をフロント／バック双方で検証する。
クリックはpoint-in-polygonで判定する。描画・区分変更・意味づけ・削除・
保存・再読込に対応する。

**通常の単一ページOCRまで対応する。** 頂点はmapping API、保存config、受付snapshot、
RegionConfigへ引き継ぎ、既存RegionCropperの切り出し後に領域外を白くする。
ink_differenceだけは領域外を0にする。印刷ラベル文字候補OCRも同じ形状でマスクする。
矩形では従来の切り出しをそのまま使用し、config/snapshotに空のverticesを追加しない。
既存画像や隣接セル、providerのmodel・prompt・association処理は変更しない。

### 見出し階層

「読み取る内容」または「繰り返し部分」の「親項目を接続」で、
見出しから子の見出し・項目名・記入欄・選択欄へ接続できる。
役割は既存の `parent_label` のまま。LayoutCellの `parentLabelCellId` に接続を保存し、
意味づけ済みの項目名では既存relationの親情報も整合させる。
新しい接続でtargetPath・relation ID・cell ID・mark sets・結合を作り直さない。
既存の親relationも参照して自己参照と循環を拒否し、単一親を維持する。
消しゴム・意味づけを戻す操作でもセルとrelationの親情報を整合させる。

### 仮想見出し

「仮想見出し」ツールで設置位置をクリックし、名前を入力して「見出しを配置」する。
配置された見出しからドラッグして対象を囲う。項目名・記入欄・選択欄だけが直接の子になる。
他の見出しは囲い込みに含めず、通常の親項目接続で接続する。

仮想見出しは `virtual=true`、`source=manual`、`role=parent_label` の名前付きノード。
配置用の座標は持つが、見出し自身のfield mappingを作らず、ラベル文字候補OCRからも除外する。
囲った範囲は選択手段であり、確定後は明示的なcell IDの親子関係を保存する。
現在の論理セル一覧から選ぶため、結合済みセルは1項目として扱い、partsから作り直さない。

## 同名項目の追加修正（2026-09-10）

複数の記入欄へ一括接続する経路では、同名なら使用済みのcustomFields保存先を
再利用するため、2組目の接続が競合して拒否されていた。
`createAutoRepeatedLabelPoolEntries`へ現在の使用済み保存先を渡し、
新しい組だけ別のキーと表示名（例: `備考2 1行目`）を採番するよう修正した。
既存の接続・セル・原文は変更しない。従来のsaleCalves/calvingsの行割当ても変更しない。

変更ファイルは `frontend/src/components/LayoutEditor.tsx`、
`frontend/src/components/LayoutEditor.authoring.test.tsx`、本書。
単独接続は漢字に加えて住所・電話番号・備考・氏名・年月日・担当者・所在地・
施設・事業所名の9例、複数欄接続は備考・住所・電話番号の3例で、
保存・再読込後の採番継続と既存接続の保持を確認する。
項目名に依存する分岐は追加していない。実帳票で報告された操作そのものは未確認。

本修正の検証結果は下記の初回実装結果とは区別する。
OCR契約は変更前後とも194 passed。frontend全体は282 passed。
backend全体は791 passed、既存1 skipped（604.01秒）。
コマンドは下記と同じで、backend全体のbasetempのみ
`.pt-increment-regression-20260910`を使用した。`git diff --check`も通過。
build成功（既存のchunkサイズ警告あり）。外部API呼出し0回。
backend・OCR認識・crop・provider・Google・印刷ページ処理の追加変更はなく、
既存OCR contractを維持した。

## 後方互換性と制限

- DB migrationや過去版・過去jobの一括変換はない。DRAFT制約、版分離、logicalFieldIdの継承を維持する。
- 新metadataはLayoutDefinitionの任意フィールド。パッケージJSON Schemaも更新し、従来の厳密なfield制約を保持する。
- 既存矩形の画像画素とpadding込みbbox、単一ページjobの従来snapshot形式を回帰テストで比較する。
- 多角形・仮想見出しの新規追加は従来のプレビューで行う。ページ単位編集では従来の枠追加不可の制限を維持する。
  pageDisplayCellsの頂点変換は表示投影のみであり、保存するgeometryやページidentityを変更しない。
- 多角形・仮想見出し・新しい見出し接続を含むセルの結合は拒否する。
  先に結合した論理セルを仮想見出しの子にする操作は可能。
- 別形式のAI汎用TemplateDefinitionは新しい形状・明示的階層を保持できないため、
  `GENERIC_DEFINITION_AUTHORING_UNSUPPORTED`で生成・承認による変換を拒否する。
  通常の手動レイアウト保存・mapping OCRとは別の制限である。
- 多段見出しはテンプレート編集・保存で扱う。ReviewPageの多段ツリー表示を追加したものではない。
  過去OCR結果・補正・confirmed状態は変更しない。
- XLSX印刷ページ設定、reference renderer、qualification、本番gate、Google連携は変更しない。
- 外部OpenAI / Google APIは使用しない。新しい多角形の実帳票での認識精度は未実測。
- 稼働中のポート8000のバックエンドは `--reload` なしで起動している。
  今回は再起動していないため、稼働中アプリへの変更反映にはバックエンド再起動が必要。

## 検証

自動テストでは、同名項目の連番と保存後の再開、形状の保存・再読込・内外判定・
区分変更・削除・意味づけ、見出しの自己／循環参照、仮想見出しと結合／意味づけの保持、
OCR除外、mapping APIの往復、旧版・logicalFieldIdの保持、版fork時の親ID変換を確認する。

多角形cropは同じ固定画像から内側画素の一致と外側のマスクを検査する。
同時に矩形の全variantを元のsliceと完全一致比較し、入力画像SHAが変わらないことも検査する。
これは保存fixture／mockによる検証であり、実手書き認識率の測定ではない。

実装したRegionCropperの出力を別の生成画像でも目視確認した。
同一bbox `(16, 16, 268, 268)` で矩形は両文字列を保持し、多角形は外側だけをマスクする。
下図の青線は説明用の重ね描きであり、入力画像には含めていない。

![矩形と多角形の切り出し比較](verification/template-authoring-crop.png)

目視確認サンプルのRGB画素列SHA-256:

| 対象 | SHA-256 |
| --- | --- |
| 元画像 | `68bb9327b8a0b6e8b8abd74c04cedc0f647d181094d0e4c6fe673de1f23fde4e` |
| 矩形crop | `a0e79b2ac3a9ed32a9ec0fd119b90dbf6621bfc45dff2b5cd7e41abcb9a3b3fc` |
| 多角形crop | `56609c1fe79989d681539b95aa6f6be23b71b3507e685149b085371050b7867b` |

実ブラウザーではlocalhostの一時的な検証用帳票を使い、三角形の作図、
仮想見出しから3欄への接続、通常見出し同士・通常→仮想の接続、
保存後のreload表示、同名の「漢字2」作成を確認した。
本番DBの帳票を操作した検証ではなく、保存は検証用localStorageを使用した。

検証コマンドと結果:

| 検証 | 作業ディレクトリ / コマンド | 結果 |
| --- | --- | --- |
| 変更前・変更後のOCR契約 | root: `backend\.venv\Scripts\python.exe scripts\verify_ocr_contract.py` | 両方194 passed |
| フロントエンド全体 | frontend: `npm.cmd test` | 変更前260、変更後271 passed |
| フロントエンドbuild | frontend: `npm.cmd run build` | 成功。500kBを超えるchunk警告あり |
| 拡張の境界・保存・既存版・schema・受付snapshot | backend: `.venv\Scripts\python.exe -m pytest tests/unit/test_layout_authoring_extensions.py tests/unit/test_template_package_contracts.py tests/integration/test_template_versioning.py tests/ocr_contract/test_phase3_job_submission_contract.py --basetemp=.pt-authoring-final-focus` | 49 passed |
| バックエンド全体 | backend: `.venv\Scripts\python.exe -m pytest --basetemp=.pt-authoring-regression-clean` | 791 passed、既存の1 skipped、577.56秒 |
| 対象Ruff | backend: `.venv\Scripts\python.exe -m ruff check`（下記の変更Pythonファイル） | 全件通過 |
| パッケージschema | backend: `.venv\Scripts\python.exe -m app.template_packages.schema_export --check` | 通過 |
| 対象型検査 | backend: `.venv\Scripts\python.exe -m mypy --follow-imports=silent`（変更したapp配下8ファイル） | 既存エラー1件。下記参照 |
| 差分 | root: `git diff --check` | 通過 |

途中の契約検証で検出した矩形snapshotへの空vertices追加は、既存期待値を変えず実装側で修正した。
途中のbackend全体実行では790 passed、1 skipped、renderer共有tempのstale ownershipテストが
1 failedとなった。`clean_stale`が古い所有ディレクトリを1件回収し、空配列を期待したassertionと
異なったものである。rendererコードとテストは変更していない。
`tests/integration/test_renderer_process.py` の再実行は6 passed。
古い一時領域を回収した後のbackend全体再実行は791 passed、1 skippedとなり、失敗は残っていない。
初回の全体実行は生成schemaの更新時期との不一致も検出したため、変更前の固定baselineには数えていない。

型検査の残件は `LayoutService._composite_target_path` の既存
`custom_field_key(parent_target_path).strip()` に対する `str | None` の指摘。
開始時HEADと同じ処理であることを照合し、依頼範囲外として修正していない。
OpenAI / Google API呼出しは0回。実APIでのassociation mismatch・mapping誤対応・
schema error・recognition-only errorの件数は未実測。

既存矩形について既存OCR contractを維持した。多角形の入力マスクは今回明示的に追加した
拡張であり、provider・認識設定・既存の矩形認識精度を変更する処理は含めていない。
既存テストの期待値変更・削除・skip追加はない。

## 変更ファイル

- UI・型: `frontend/src/components/LayoutEditor.tsx`、`layoutAuthoring.ts`（同ディレクトリ）、
  `frontend/src/types.ts`、`frontend/src/pageAuthoring.ts`
- データ・切り出し: `backend/app/domain/polygon.py`、`backend/app/domain/template_config.py`、
  `backend/app/layout_analysis/models.py`、`backend/app/extraction/region_cropper.py`
- 保存・境界: `backend/app/api/v1/routes/templates.py`、`backend/app/services/layout_service.py`、
  `backend/app/services/label_cell_ocr_service.py`、`backend/app/services/template_definition_service.py`
- パッケージ: `backend/app/template_packages/schemas/template-v1.schema.json`
- テスト: `frontend/src/components/layoutAuthoring.test.ts`、
  `frontend/src/components/LayoutEditor.authoring.test.tsx`、
  `backend/tests/unit/test_layout_authoring_extensions.py`、
  `backend/tests/integration/test_template_versioning.py`
- 文書: `AGENTS.md`、`docs/ocr-safety-contract.md`、本書
- 検証図: `docs/verification/template-authoring-crop.png`
