# 同一読み込みによるUNO寸法観測（2026-09-14）

## 原因と変更境界

FODSの小さな寸法だけでは、LibreOfficeのuint16変換で桁あふれした値を区別できない。
先行の[8点実測](uno-dimension-observation-20260914.md)に基づき、固定runtimeの
同じread-only documentから単一セルrange Sizeを取得し、そのdocumentをFODSへ出力する。
前後の寸法一致、原本/FODS SHA、要求prefix、従来のruntime/worker/font identityを要求する。

出力フィルタ名はLibreOffice 7.3.7.2の
[ODS_FlatXML.xcu](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/filter/source/config/fragments/filters/ODS_FlatXML.xcu)
で確認した `OpenDocument Spreadsheet Flat XML`。
wide寸法の照合は同版のGetMMRectと
[整数単位変換](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/include/o3tl/unit_conversion.hxx)
に従い、累積twipをそれぞれmm100へ丸めた差にinclusive端点の1を加える。
毎行のscalar寸法に一律1を足す方式は使わない。隠れた寸法は表示幅0/range Size 1と照合する。

変更ファイル:

- `renderer_wsl.py` / `renderer_wsl_worker.py`: 明示要求・同一読み込み・完全な応答inventory/identity。
- `calc_workbook_geometry.py`: wide非overflow/可視性/累積寸法の照合。旧draft読込は維持。
- `print_preparation_service.py`: 新Calc確定では必須。失敗前にDB/config/artifactを更新しない。
- `reference_qualification_service.py`: Calc資格検証にも必須。旧証拠を補完しない。
- `scripts/inspect_uno_dimensions.py`: 原本を変更しない再実行可能な診断。
- 対応unit/integration、Safety Contract。provider/model/prompt、旧cropとmappingは変更しない。

## 実環境の確認

固定runtime `ee8b87dbbffd6b0817e1346aaabeef744c14b9632ea206d7af903cc239ecb03c`。
実アプリのruntime設定は変更していない。新runtimeの正式な環境資格は未発行。

製品bridge/workerから実帳票の102行・37列を同一読み込みで観測した。
終了コード0、入力print.xlsxは不変。出力は
`C:/Users/hanam/AppData/Local/Temp/llmocr-uno-same-import-7a6e2vno/`。
`report.json` SHAは `d7a797c584f5c18b399c8ba0028e7c1757ffb3ab0365ead35acc9ab66c27ed78`。
全139寸法で、FODSから復元したtwipの累積予測wide寸法と観測が完全一致した。
これは個別帳票の実測であり、全入力/全renderer環境の資格証ではない。

## 回帰と制限

- 変更前OCR契約: 210 passed / 1 warning（55.76秒）。
- 変更後OCR契約: 210 passed / 1 warning（55.39秒）。
- 単体/従来worker等: 100 passed（0.66秒、mockを含む）。
- API/保存/資格/新単体: 76 passed（24.08秒、rendererはstub）。
- Ruff、対象Mypy、git diff --check: PASS。
- 最初の単体実行は既存pytest一時フォルダのACLで53 setup errors。固有basetempで再実行。
- 最初のprivate再確定は同時テストのmutexによりRENDERER_BUSYで拒否。迂回せず直列再実行。

backend fullとprivate再確定の最終結果は追記する。旧2044件fullを今回の変更後証拠に流用しない。
有効化、実API OCR、ブラウザ操作、カードからの実ダウンロードは未検証。
環境資格、可視性/本番geometry許容値、実入力のink安全検査などが残り、ACTIVE/受付gateは未解除。
OpenAI 0/100、Google 0。原本・スキャン・本番DBは変更していない。

### private再確定の結果

`python -m scripts.dry_run_print_preparation` を既存privateコピーに対して直列再実行し、終了コード0。
新コピー `C:/Users/hanam/AppData/Local/Temp/llmocr-prepare-copy-k2rurigg`。
新版revision `a4baa611b846c67314d9eaf8cfb246621062b4ac56ff5bbf96c14f9392b223bb`。
worker SHA `bc7477b5548140613d6bf60c96a6509a3b795db28f656adbb5e067e8fe070c2f`。
4ページの範囲・全fields/bindings/座標・reference PNG SHA・canonical layout・DRAFT・
保存応答とGET・コピー元全artifactの不変を確認した。比較元DBもread-onlyで前後一致。
新runtimeと同一読み込み観測を保存した版はこのprivateコピーのみで、本番有効化ではない。

### 追加レビューでの終了境界修正

最初のbackend fullは、追加レビューでUNO終了timeoutを成功扱いにし得る箇所を見つけたため
停止した。専用basetempをcommand lineに持つpytest PIDを照合して対象processのみ停止した。
当該fullは未完走であり、成功件数として計上しない。

UNOの正常終了code 0も必須にした。終了timeoutは所有Office processを停止した上で拒否し、
出力が存在しても成功reportを返さない。異常終了も同様に拒否する。
timeout/異常終了の注入テストを追加し、関連unit/integration 123 passed（21.05秒）。
workerのLinux/Python 3.10条件のMypyも成功。UNOのimport型定義だけはhost venvに存在しない
ため、当該3importのmissing stubを明示除外し、実native検証と分けて扱う。

frontend全体は373 passed（35ファイル、15.66秒）、build成功。
既存bundle size warningあり。実ブラウザ確認の代替ではない。
上記worker SHAとprivate実測は終了境界修正前の値であり、最終native再実行の結果を追記する。

### 終了境界修正後の再実測

OCR契約は210 passed / 1 warning（54.71秒）。private再確定は終了コード0。
最終worker SHA `ddd0682df190c9911d2d8f446779f374b6b0bd64a67c30d47540828de3d940da`。
新コピー `C:/Users/hanam/AppData/Local/Temp/llmocr-prepare-copy-vnxpvohr`、
revision `a57aaa41151f27e94c2185e843aa43f7ed47f57c79172f857f3d6b7b02707b91`。
4ページの範囲、fields/bindings/座標、PNG SHA、canonical layout、DRAFT、保存/GET一致、
コピー元全artifactと元DBの不変を再確認した。
`RUN_OPENAI_INTEGRATION_TESTS=false`、
`pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-uno-shutdown-full-0914`
でbackend fullを再開した。終了結果の取得までは全体回帰PASSと扱わない。

### 最終全体回帰の完走結果

上記の同一実行は終了コード0、**2094 passed / 1 skipped / 3 warnings / 2112.46秒**
（35分12秒）で完走した。1件は既存OpenAI opt-in条件、3警告は既存openpyxl Font.copy非推奨。
停止した先行実行の結果は合算していない。この完走にはUNO終了境界・保存/GET/資格拒否・
従来rendererのテストを含む。worker SHAは最終native実測と同じddd0682d…940daで維持した。
前述frontend 373件/build、変更後OCR契約210件と区別して記録する。

全体回帰の成功は、未実装の環境資格issuer・本番誤差基準・入力ink検査・正規承認/受付の
成功経路を実装済みにするものではない。有効化および実OpenAI OCR/E2Eは未完了。

### 最終private資格検証の保存結果と再取得

最終privateコピー `llmocr-prepare-copy-vnxpvohr` の資格reportを読み取り専用で確認した。
保存先は同コピーの `artifacts/7b90d415-98d5-44e4-971a-e477ded0fdab/qualification/49f211535c15400e8ebc7fa86e7befe8/report.json`、
内容SHAは `8e397cb8d1b26cd8da0603179b3d7fb3d84ffc9f442de45e89d588d4aa4a4f59`。
元のnative実行の終了出力は取得できなかったため、終了コードや実行末尾のassert成功は推定しない。
別の読み取り専用 `ReferenceQualificationService.status` は終了コード0で完了し、
所有された保存証拠の再検査後も、保存reportと返却値が完全一致した。

- `deterministic=true`。字形照合は640 font/code組・延べ3515 glyphでPASS。
- 欄別観測は4ページ・151欄でMEASURED。ページ別の最大辺誤差は
  0.4854167 / 0.5015912 / 0.4807579 / 0.4104167 px。
- `thresholdQualified=false`、`visibilityVerified=false` は全ページで維持。
- 全体は `status=FAIL`、`qualified=false`。
  `REFERENCE_ANCHORS_INSUFFICIENT`、`WSL_ENVIRONMENT_QUALIFICATION_UNVERIFIED`、
  `GEOMETRY_TOLERANCE_UNQUALIFIED` が残る。
- 実入力検査は `NOT_EVALUATED`。記入済みスキャンの筆画安全性やOCR精度を認定した結果ではない。

`transform_rect` は各辺を整数丸めするが、約0.5pxの観測値だけから本番許容値を決めない。
PDF出力精度・描画と可視性・実入力の局所位置合わせ誤差は別の検証事項である。
この再確認では製品コード・原本・スキャン・本番DB・runtime設定を変更せず、
資格/有効化gateも変更していない。OpenAI 0/100、Google 0。
