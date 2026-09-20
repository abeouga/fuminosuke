# 複数ページOCRの送信前crop照合

## 結論・範囲

本番化作業の送信前検証を実装した。有効化・本番受付はまだ開放していない。
対象は `CROP_CORRESPONDENCE` であり、位置合わせ精度・実筆画のclearance・
renderer環境の資格証明ではない。画像・padding・mapping・providerの認識方式は変更しない。

原因は `OcrWorkflowService.process` が抽出結果のpage snapshotとcrop画像を受け取った後、
受付snapshot・ページ画像との独立再照合なしにproviderを生成していたこと。
改変cropを入れる結合テストで、変更前はprovider生成へ到達して失敗した。
これは人工入力の故障注入による境界の再現であり、実スキャンに誤配送があったとの主張ではない。

## 実装

- `backend/app/services/paged_crop_correspondence.py`: 全ページの受付検証に接続し、
  当該page/input/version/config/attemptと抽出結果を照合する。Pythonの `True == 1` でも
  snapshotを同一視しない。現行TemplateVersionから対応を再構成しない。
- 同じjob/attempt/pageに所有される `alignedSource` / `handwritingOnly` と、受付時の
  `cleanTemplatePath` を内容SHA・保存先範囲込みで再読込する。保存PASSフラグは利用しない。
- 受付configから期待するregion集合・padding込みbboxを計算する。欠落・追加・別設定を拒否する。
  `RegionCropper`を再実行して照合結果を作らず、全体画像の該当sliceから5種類のcrop画素を比較する。
  enhanced gray / differenceは既存 `create_variants` で作る。手書き分離処理は再実行しない。
- 多角形の既存maskも比較対象とし、mask外の変更も拒否する。このテストは、
  未対応のXLSXページ多角形の受付制約を解除するものではない。
- workflowのprovider生成前に検証し、成功時はページ別 `cropCorrespondence` artifactに
  snapshot SHA・入力/参照SHA・region ID・bbox・5種類のraw pixel SHAを記録する。
  `productionQualified=false` / `inkClearanceVerified=false` を維持する。
- 不一致は `PAGED_CROP_CORRESPONDENCE_INVALID`。ページ実行結果に理由・page/region IDを残す。
  他ページの成功結果は保持し、再試行で失敗ページだけを処理する。

既存単一ページにはこの検証を適用しない。原本、実DB、公開済み版、過去jobの更新なし。
prompt/model/schema/provider association、mapping ID/targetPath/sourceRegionId、
crop座標/padding/画素、補正・確定・出力規則は変更していない。
既存OCR contractを維持し、新paged経路の送信前に拒否境界を追加した。

## 検証

| 検証 | 結果 |
| --- | --- |
| 変更前OCR contract script | 210 passed、41.19秒、既存warning 1件 |
| 変更前改変crop再現 | 1 failed。provider生成へ到達することを確認 |
| 最初の対象・既存page pipeline | 11 passed、238.40秒 |
| 最終新規unit/integration | 33 passed、82.11秒 |
| 変更後OCR contract script | 210 passed、57.01秒、既存warning 1件 |
| 検証前後のrequest比較 | 矩形/多角形の2条件で、送信画像bytes・mapping・PreparedOcrRequest全体が一致 |
| backend full | 1566 passed、既存1 skipped、3 warnings、2005.89秒（33分25秒）、exit 0 |
| 実スキャンの画素再比較 | 8画像・302crop、元領域ID/bbox/aligned crop画素すべて一致 |

新規unitでは全5variantへの1画素故障、別regionへの画像差替え、shape/dtype変更、
region欠落/追加、bbox/config/snapshot変更、polygon mask、同名項目を検証した。
integrationでは保存SHA改変、同一bytesでも所有範囲外のartifact拒否、page snapshotの欠落・
差替え、古いattempt、部分失敗と成功保持・失敗ページだけのretryを検証した。
無効入力ではprovider生成0回。既存テストのskip・削除・assertion弱体化なし。
最初のunit/integration同時収集で同名test moduleが衝突したため、新規unitを
`test_paged_crop_pixels.py`へ改名し、全対象を再実行した。

実スキャン比較は前回の保存baseline
`C:/Users/hanam/AppData/Local/Temp/llmocr-scan-baseline-pjo23fxk/report.json`
（SHA `c1d2b3f3e9484b362df9197b4702587552f481b75887f61d1d787f33eea6427a`）を使用。
SQLite query-only・原本/参照/診断PNGのSHA照合後、同じpage configでcropを再構成し、
新しいpure検証関数を実行した。302cropを比較し、各入力で1cropの1画素故障も拒否した。
全読込元の前後SHAとDB状態は不変、実版はDRAFTのまま。
これはpixel-only harnessであり、本番受付job作成・資格発行・実provider送信ではない。
認識文字列の正誤、実responseのassociation mismatch/schema errorは未測定。

再現コマンド（backend配下のPythonは `.venv/Scripts/python.exe`）:

```text
# root
backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py
# backend
python -m pytest tests/integration/test_paged_crop_correspondence.py tests/unit/test_paged_crop_pixels.py -q -o addopts='' -p no:cacheprovider --basetemp=<unused>
python -m pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=<unused>
```

変更Python 5ファイルのRuffは通過。サービス3ファイルのmypy
（`--follow-imports=silent`）も通過した。リポジトリ全体の静的検査成功とは区別する。
全体試験は `RUN_OPENAI_INTEGRATION_TESTS=false`、
`--basetemp=.pt-paged-crop-full-0913` で実行した。新規33件もこの全体収集に含む。
警告3件は既存Spreadsheet importテストのdeprecated copy呼出し。
最終 `git diff --check` は通過した。

## 残る条件

この照合は「保存した全体画像のどの領域をproviderへ渡すか」を検証する。
保存全体画像自体のscan registration残差、未印刷のfield境界のreference geometry、
罫線上/隣接領域の実筆画を含む独立clearance、正式な環境資格を証明するものではない。
これらの未完了を、同一cropのSHAや単体テスト成功で認定済みに置換しない。
本番有効化・実OCRからレビュー/確定/取得までのE2Eは未検証。
OCR安全性を完全には検証できていない。

実OpenAI API 0/100回、Google API 0回。フロントエンドの変更・新規UIテストなし。
既存の未コミット差分を保持し、staging/commit/pushは行わない。
