# テンプレート操作改善・区分変更調査（2026-09-12）

## 範囲

項目2（完全一致による手動項目追加）、3（明示的ハンドモードでのCanvas配置修正）、
5（見出し階層と繰り返し群接続）を実装。項目4は調査・再現テストのみ。
詳細な操作とページ編集制限は[手動編集の拡張](template-authoring-extensions.md)に記載。

主な原因は、手動追加にもOCR向けの正規化・部分一致を使っていたことと、
親接続処理が一般のセル階層と従来の繰り返し表処理に分かれ、ステップ2が群を認識しなかったこと。
サーバー側は繰り返し群の既存親接続を循環検証へ含める4行だけを追加。
フロントエンドだけの循環拒否では保存APIへの直接要求を保護できないため必要。

## 今回の変更ファイル

- frontend/src/components/LayoutEditor.tsx
- frontend/src/components/LayoutEditor.move.css
- frontend/src/components/layoutAuthoring.ts
- frontend/src/components/LayoutEditor.authoring.test.tsx
- frontend/src/components/layoutAuthoring.test.ts
- frontend/src/pages/TemplatePage.test.tsx（既存保存失敗テストの読み込み完了待ちを追加）
- backend/app/layout_analysis/models.py
- backend/tests/unit/test_layout_authoring_extensions.py
- backend/tests/unit/test_manual_cell_translation.py
- backend/tests/integration/test_page_authoring.py
- frontend/verification/authoring.html、authoring.tsx（実APIを使わない手動検証画面）
- docs/ocr-safety-contract.md、docs/template-authoring-extensions.md、本報告

開始時の未コミット差分は保持。AGENTS.md、OCR provider/model/prompt/schema、
Google連携、renderer、既存job・補正値・確定snapshot・原本は変更していない。
移動したDRAFT枠だけの座標・頂点・所属行bbox更新は明示要求による意図した変更。
mapping ID、targetPath、source regionの所有関係を保持し、既存OCR contractを維持した。

## 検証

Pythonはbackend/.venv/Scripts/python.exe。pytest・ruff・mypyはbackend、npmはfrontendで実行。

- 変更前：frontend `npm.cmd test` — 301 passed。
- 変更前・後：rootから `python scripts/verify_ocr_contract.py` — 両方210 passed。
- 変更後：`npm.cmd test` — 315 passed。`npm.cmd run build` — 成功（既存chunkサイズ警告あり）。
- `python -m pytest tests/unit/test_manual_cell_translation.py tests/unit/test_layout_authoring_extensions.py -q -p no:cacheprovider --basetemp=.pt-authoring-extra-0912` — 18 passed。
- `python -m ruff check app/layout_analysis/models.py tests/unit/test_layout_authoring_extensions.py tests/unit/test_manual_cell_translation.py` — 成功。
- `python -m mypy app/layout_analysis/models.py` — 成功。
- backend全体：`python -m pytest -q -o addopts='' -p no:cacheprovider --basetemp=.pt-authoring-2345-full-0912` — 844 passed / 1 skipped / 3既存警告、556.29秒。
- 全体実行の収集後に追加した2件（移動crop、群の保存API）は個別にも通過。移動cropは上記18件に含む。
- `python -m pytest tests/integration/test_page_authoring.py -q -p no:cacheprovider -k nested_heading --basetemp=.pt-authoring-api-0912` — 1 passed。見出し階層と繰り返し群の親をテストDBへ保存し、GET応答・元セル座標・既存relationの保持を確認。対象ファイルのruffも通過。
- `git diff --check` — 成功。

追加テストは任意名・文字種差・キー衝突・完全一致の再追加、保存/再読込、通常選択と移動の分離、
多角形/仮想見出し移動、キャンセル、境界制限、両ステップの群接続と循環拒否を含む。
区分変更後にlabelCellIdsが空でもinputのtargetPathが残ることをfrontend/backend双方で再現した。
移動した多角形の保存モデルからmapping/cropまで確認し、bboxは(6,6,48,48)から
(26,16,48,48)へ指定量だけ移り、内側画素・外側mask・元画像SHA・mapping所有者を検証した。

途中のfrontend回帰で従来の「同じcollectionの複数帯へ親を設定する」ケースが失敗したため、
既存テストを弱めず実装側を修正。既存期待値の差替え・skip追加はしていない。
保存失敗の既存テストは並列実行時にeditor読込前の工程ボタンを押す競合が出たため、
Canvasの出現と編集値の保持を確認してから押すよう前提条件の待機・assertionを追加した。

## 未検証・制約

ブラウザーで検証用画面を開けたが、操作環境がWindows上の現在URLを安全に特定できず、
Computer Useが停止した。以後の画面操作を中止。実ブラウザーでのドラッグ・見た目・保存成功は未確認。
実テンプレートへの保存・実行中backend再起動は行っていない。テストDB保存は実テンプレート保存の代替ではない。
本報告のcrop確認は固定画像によるもので、実手書き認識精度・実APIのassociation mismatch等は未計測。
実OpenAI / Google API呼出しは0回。Golden fixtureは変更していない。
テスト用の`.pt-authoring-*`等は未コミットで残している。staging / commit / pushは行っていない。
