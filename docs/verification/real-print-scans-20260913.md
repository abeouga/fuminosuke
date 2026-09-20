# 実印刷スキャンの初期検証と変換記録の修正

テンプレートの本番有効化は未完了。この記録は実入力の診断と、実画像に適用した
変換行列の記録不整合の修正を扱う。資格証・ACTIVE化・実OCR成功の記録ではない。

## 入力と保護対象

利用者が指定した `Desktop/実印刷未記入` と `Desktop/実印刷記入済み` の各1〜4.png。
8枚すべて3308×4676。表示された第1〜4面を目視で確認し、明示ページbindingを与えた。
ファイル名やアップロード順から本番のphysical identityを推定する処理は追加していない。

対象版 `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` はDRAFT。
保存rangeは A1:AK28 / A29:AK60 / A61:AK89 / A90:AK102。
OCR対象領域は60 / 51 / 37 / 3、全151領域。

診断CLIはSQLiteをmode=ro/query_onlyで読み、原本スキャン・config・参照PNGのSHAと
版status/quality/layout/config pathの前後一致を検査する。診断画像は新規のローカルtempへ
保存し、原本・実DB・元XLSX・印刷PDFは更新しない。画像と記入内容はrepoへコピーしない。
設定はソース既定値を明示した診断条件であり、稼働backendの秘密設定は読み込まない。

## 実入力で確認した範囲

- 正しい対応: 未記入4枚＋記入済み4枚すべて既存ORB_RANSAC_ECC経路を通過。
- 間違った対応: 記入済み各ページを他の3ページへ割り当てる12通りはすべて拒否。
- 通過時の従来のmeanReprojectionErrorPxは約1.24〜1.65px。
  この値はORB段階の平均であり、最終画像の全位置の最大誤差ではない。
- 従来のraster罫線診断では、測定できた部分にも3〜4pxの予測座標との差があった。
  第4ページでは測定できない枠が多い。未測定を0pxや成功へ読み替えない。
- 未記入スキャンにも手書き候補maskが発生した。元の印刷画素とスキャンの差が
  含まれており、heuristic maskの不在/存在だけでは筆画clearanceを認定できない。

`inspect_scan_clearance.py` は明示したblank/filled SHA、同じページID・参照SHA・
領域ID/bboxを照合し、登録済み画像同士の暗い差分を観測する。印刷画素近傍を
「安全な空白」にはせずunknownとして別に残す。孤立した1pxも削除しない。
候補にはスキャンノイズを含み、crop外の筆画や未知の局所変形を証明できない。
したがって全観測で `inkClearanceVerified=false` を維持する。

暫定観測では151領域中23領域のcrop端2px以内に候補がある。
第4ページの候補画像を目視すると罫線や印刷文字の残差も含まれるため、
23領域すべてを「手書き文字が切れている」と断定しない。

## 再現した不具合と修正

`TemplateAligner._align_legacy` はORB/resize後にECC補正していたが、返す
`RegistrationResult.homography` はECC補正前の行列だった。resize fallbackでも
実際のresize倍率を返さずidentityを返していた。回復経路はすでに最終変換を合成していた。

既知のECC変換を固定した追加試験は、修正前に通常経路・resize経路の2件とも失敗した。
これに対し、実際の画素中心を表すresize行列とECCの逆変換を正しい順で合成する。
画像を追加でwarpせず、既存の描画・補間処理・評価値は変更しない。
非有限/退化したECC行列を有効な対応として返さない。

実8枚で修正前後を比較した結果:

- 位置合わせ、mask、overlay、302個のcropを含む全診断PNGのSHA一致。
- 全領域ID・bboxと従来のregistration quality値が一致。
- 返されるhomographyは8枚とも修正された。
- 原画像、参照、版状態の不変を確認。

変更境界は変換metadataと診断CLI。provider/model/prompt/schema、mapping生成、
物理セル・crop画素、review/補正/確定/出力、旧job snapshotは変更しない。
本番閾値、資格発行、承認/受付gateを緩めていない。

今回の変更ファイル:

- `backend/app/image_processing/registration.py`
- `backend/scripts/inspect_print_scans.py`
- `backend/scripts/inspect_scan_clearance.py`
- `backend/tests/synthetic/test_registration_applied_transform.py`
- `backend/tests/unit/test_scan_baseline_script.py`
- `backend/tests/unit/test_scan_clearance_observation.py`
- `docs/production-paged-ocr.md` の最新状況へのリンク
- 本報告書

最終の明示SHA付きbaselineは
`C:/Users/hanam/AppData/Local/Temp/llmocr-scan-baseline-pjo23fxk/report.json`。
paired観測は `C:/Users/hanam/AppData/Local/Temp/llmocr-scan-clearance-2a3a_lm4/report.json`。
最終実装での再実行は `C:/Users/hanam/AppData/Local/Temp/llmocr-scan-clearance-_z0pv0x5/report.json`。
全観測結果とbaseline SHAが前回と一致した。
12通りの誤ページ拒否は `C:/Users/hanam/AppData/Local/Temp/llmocr-scan-baseline-in90exaz/report.json`。
誤ページ試験はimageSha256一覧の追加前に実施したため、その記録には一覧がない。
これらはローカル診断成果物であり、所有資格artifactとしてDBへ登録していない。

## 再現コマンド・検証

```powershell
# repository root
backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py
# backend
.venv/Scripts/python.exe -m pytest tests/synthetic/test_registration_applied_transform.py tests/synthetic/test_registration_recovery.py tests/integration/test_registration_diagnostics.py -q -o addopts=''
.venv/Scripts/python.exe -m pytest tests/unit/test_scan_clearance_observation.py -q -o addopts=''
# scanは目視確認したページを明示し、必要枚数繰り返す
.venv/Scripts/python.exe -m scripts.inspect_print_scans VERSION_ID --scan '1=ABSOLUTE_SCAN_PATH'
# paired observationは同じページの未記入SHA:記入済みSHAを明示する
.venv/Scripts/python.exe -m scripts.inspect_scan_clearance REPORT_JSON --pair BLANK_SHA:FILLED_SHA
```

- 変更前OCR contract: 210 passed / 38.11秒。
- 新変換テスト: 変更前2 failed、修正後は既存関連試験と合わせ18 passed / 7.22秒。
- 変更後OCR contract: 210 passed / 44.30秒。
- paired observation unit: 11 passed / 0.09秒。
- CLI不変確認を含む診断unit: 15 passed / 0.23秒。
- 最終関連5ファイル: 41 passed / 7.71秒。通常/resize変換、退化行列拒否、
  既存回復・失敗保存、診断CLI・境界観測・診断画像差替え拒否を含む。
- 変更対象6 PythonファイルのRuff、実装3ファイルのmypy(follow-imports=silent)は成功。
  全体mypyの成功とは扱わない。
- backend全体回帰は `.pt-real-scan-transform-full-0913` で完了。
  **1,510 passed / 1 skipped / 3 warnings / 1,891.22秒（31:31）、終了コード0**。
  `RUN_OPENAI_INTEGRATION_TESTS=false`、`pytest -q --tb=short -o addopts='' -p no:cacheprovider`
  を使用。既存の外部API opt-in skipとopenpyxl Font.copy非推奨通知3件。
  collection後に追加した診断unitと退化行列2件は上記41件で別実行として区別する。
- frontend変更なし。今回新たなfrontend full/build・実Chrome検証は実施していない。

実OpenAI **0 / 100回**、Google 0回。実provider responseがないため、認識誤り・
provider association mismatch・schema errorの件数は未測定。既存OCR contractは
前後210件と実8枚の画素/ID比較の範囲で維持した。本番OCR安全性を完全には検証できていない。

入力提供の不足ではなく、未描画境界を含む一般的な資格方式、正式な環境資格発行、
実入力の送信前safety enforcementが未完成である。スキャン提供だけでこれらの
未実装が解消したとは報告しない。staging/commit/pushなし。

終了時も実版はDRAFT、revisionは
`2b026bcecccc5542cfb729614e43b086bd95fae1e11c5a89e2ee243237f33512` と一致。
印刷用XLSX/PDF/reportと全参照PNGの保存SHAをread-onlyで再確認した。
今回の差分で `git diff --check` 成功。
