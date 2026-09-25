# 通常OCR準備と有効化の一本化

実施日: 2026-09-21〜22。既存dirty差分を保持。stage/commit/pushなし。

## 原因と変更境界

候補準備v2も初回には過去の正式測定artifactとnative replayが必要であり、
新規テンプレートに「準備→資格確認→有効化」の操作を要求していた。
さらに画像選択画面がcatalogとは別に正式資格を取得し、受付条件と異なる案内を表示していた。

ユーザーの承認に従い、通常OCRを正式な印刷精度認定から分離した。
`CandidateReadinessService.prepare_for_ocr`と既存approveを順番に呼ぶ小規模な変更とし、
新しいjob frameworkや審査エンジンは追加していない。
正式資格とv1/v2の既存処理・記録は保持し、通常UIからのみ取り除いた。

## 利用フロー

1. 既存のGUIで区切り線と読み取る欄を設定・保存する。
2. 「このテンプレートでOCRを使う」を押す。
3. 見本未作成時だけ印刷見本を作成し、保存内容との対応を確認して有効化する。
4. ページごとの画像を指定してOCRを行い、全ページを確認・補正して確定する。

mm入力・詳細診断・旧方式への変更は折りたたみに移した。
見出しのページ移動は範囲文字列の入力ではなく、ページ候補の選択とした。
準備失敗後も現在画面と保存設定を維持し、生成済み見本は再試行時に再生成しない。
未保存の編集・busy・revision競合の扱いを維持し、承認の自動再送は行わない。

## 利用者が行うこと

通常操作は、次の4点だけです。

1. 画面上の青い区切り線とページプレビューを見て、各ページに印刷する範囲を調整する。
2. 読み取る枠に、何を読み取る欄かを設定して保存する。
3. 「このテンプレートでOCRを使う」を押す。印刷見本が未作成なら、ここで作成する。
4. ページごとに記入済み画像を選び、OCR結果と切り出し画像を確認し、必要なら修正してから確定する。

通常操作として、印刷精度の認定、環境の確認、座標・余白の数値入力を要求しない。余白などは任意の詳細設定に置く。

### 事前に知ること

- 初回やページ設定を変更した直後は、印刷見本の作成に時間がかかる場合がある。
- 原本とページ設定の対応を確認できない場合、または現在未対応のExcel構造を含む場合は、OCR開始前に止まる。画面の理由を確認し、ページ範囲または読み取る欄を修正する。
- OCR結果は確定前の候補である。文字の完全一致や印刷位置の完全一致をシステムが保証するものではない。全ページの結果と切り出し画像を確認し、必要な修正後に確定する。

## テンプレート設定画面の簡略化

- ステップ2の「登録済みの項目から保存先を選ぶ」は初期状態で閉じ、必要なときだけ開く。開閉しても検索条件や選択内容は維持する。
- ステップ3は残し、ツールバーの「繰り返し部分をまとめる」と「見出しへ接続」も維持する。プレビュー下の名称・行列・状態を扱う詳細パネルだけを表示しない。
- ステップ5では、Excelからの自動作成と印刷プレビューへの座標補正を知らせる技術的な説明、および変更がないときの保存済み通知を表示しない。未保存、保存中、保存失敗、実際に修正が必要な警告は維持する。
- この簡略化では、既存の繰り返し構造、保存先、セル対応、OCR結果、補正・確定処理を変更しない。

## 保持する契約

- DRAFT/revision、所有原本・config・layout・print plan・reference画像・描画レポートの照合。
- 全enabled mappingと各ページの一意対応、既存の未対応項目拒否。
- 新規描画時の隔離処理。無制限実行や別rendererへのfallbackは追加しない。
- OCR受付時のreference PNG内容SHA、immutable snapshot、実入力page identity、registration、
  crop correspondence、association、文書全体API予算。
- 全ページの人間レビュー、補正revision、未確定出力拒否。

**変更したのは有効化/受付の前提条件**であり、契約を一切変えていないとは報告しない。
v3はgeometry/glyph/environmentの正式認定を要求せず、`geometryQualified=false`を返す。
精度・全筆画の保持は保証しない。既存OCRの対応関係・補正・確定のcontractは維持した。
正本はSafety Contract §12.12。過去の正式資格記録や旧snapshotの意味は変更しない。

## 今回の変更ファイル

- backend: `app/services/candidate_readiness_service.py`、`app/api/v1/routes/print_layout.py`、
  `tests/integration/test_candidate_readiness_api.py`
- frontend: `src/pages/TemplatePage.tsx`、`src/components/PrintLayoutEditor.tsx`、
  `src/components/OcrPageBindings.tsx`、`src/api.ts`、`src/printLayout.ts`、
  対応するテスト4ファイルと`src/pages/OcrPage.test.tsx`
- 文書: `AGENTS.md`、`docs/ocr-safety-contract.md`、この文書

既存dirtyのその他の変更は今回の実装として数えない。

## 検証結果

backendディレクトリ:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_candidate_readiness_api.py tests/unit/test_candidate_readiness.py -q -o addopts='' -p no:cacheprovider --basetemp=D:\llm-ocr-archive\pytest-work\20260922-readiness-regression-02
```

32 passed（29.99秒）。新規5ケースでは正式資格を一度も発行せず、2ページの準備→有効化→受付と
原本/config/layout保持を検証。native replay/環境照会が呼ばれたら失敗するstubを使用した。
原本・reference画像・ページbinding・revisionの変更拒否も検証した。
旧v1/v2の回帰も含む。初回の新規5件単独実行はpytest cache権限警告1件、結果は成功。

frontendディレクトリ:

```powershell
npm test -- src/pages/TemplatePage.test.tsx src/pages/OcrPage.test.tsx src/components/OcrPageBindings.test.tsx src/components/PrintLayoutEditor.test.tsx src/components/ReferenceQualification.test.tsx src/printLayout.qualification.test.ts
npm run build
```

133 passed（6.81秒）。自動準備の順序・準備後revision、生成/対応検証の失敗と再試行、二重押下、
競合の自動再送禁止、旧単一ページ、下書き保持、ページ候補選択と保存失敗を確認した。
build成功。既存の500 kB chunk警告あり。変更PythonのRuff、`git diff --check`成功。
旧資格文言を前提にしていたテストは、新しい承認済みフローの期待値へ変更した。skip追加なし。

## 実画面と実環境

- 既存Chromeタブ`1093604708`、`http://127.0.0.1:5173/`を再利用して画面取得。
  新規タブ・明示reloadなし。Viteの反映後、既存V3/4ページ設定を表示した。
- 「資格を確認」ボタン0件、「このテンプレートでOCRを使う」はenabled。
  印刷設定の詳細折りたたみと見出し設定の簡略表示を実画面で確認した。
  保存・有効化・印刷生成・画像送信は操作していない。
- 実行中/待機中jobがないことをread-only DBで確認し、旧dev launcherとbackendを止め、
  backendだけを非表示プロセスで再起動。既存frontendは維持した。
  health=ok、OCR provider=openai、新`/ocr-readiness`がOpenAPIに登録され、frontend HTTP 200を確認。
  再起動ログ: `backend/data/logs/dev-backend-reviewed-20260922.{out,err}.log`。
  従来launcherは停止済みのため、今後の停止/起動は既存の起動・停止手順で行う。

## 未実施と限界

ユーザーの高速化要求に従い、backend/frontend full tests、重い契約スクリプト、実WSL再描画、
実テンプレートの有効化、実OCR・認識品質評価は実施していない。外部API呼出し0件。
初回の印刷見本生成そのものには既存WSL処理が必要であり、生成時間をゼロにはしていない。
実帳票のOCR安全性を完全には検証できていない。対象テストの成功と本番品質は区別する。
