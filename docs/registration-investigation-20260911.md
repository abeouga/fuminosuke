# 最小矩形テンプレートの位置合わせ失敗調査

本書前半は調査時点の記録。後続のユーザー指示に基づく修正・検証は末尾の
「修正実装」を参照する。

2026-09-11。調査対象HEAD: `b613c11a72b20d4c7cafc7ec3dd7ddf9adf52b55`。
既存の未コミット変更を保持した。今回の製品コード・設定・DB・公開テンプレートの変更はない。
AGENTS.md、OCR Safety Contract全体、template-authoring-extensions.mdを確認した。
既存契約は本調査を妨げておらず、AGENTS / Safety Contractの追加改訂は不要と判断した。

## 結論

最小矩形の不足で位置合わせの特徴点が不足したものではない。
XLSX由来の基準画像とスキャンの描画差がある条件で、現行のORB採用条件が
有用な変換候補を棄却し、画像サイズ合わせから始めるECC fallbackが失敗した。
これは文字認識前の `LOW_REGISTRATION_CONFIDENCE` である。

基準画像の描画差がinlier率を低下させた寄与の大きさは未分離だが、
基準画像の文字縮小・折返し・実線と点線等の差は画像で確認した。
紙面と基準画像の差をすべてLibreOfficeの不具合とは断定しない。
原本の印刷条件・フォント・過去の紙面生成過程は未照合である。

## 対象と同一性

- DB上の正式名: `2 債権者登録依頼書001`。
- TemplateVersion: `7657c9e8-dde4-4a41-b14b-412fd835d4de`、v1、ACTIVE。
- 22 layout cells、9 field mappings。基準画像は全帳票を保持し、1240 × 1754 px。
- 保存されたpreview engine: `libreoffice-pdf`、sourceType: `xlsx`、PDF 1ページ。
- `editedMaskApplied=false`。矩形削除で基準画像が9項目だけになった状態ではない。
- 失敗job: `6be19007-148f-409f-a5ee-a95a1268485c`、attempt 1。
- 失敗時刻: 2026-09-11 12:27:44 UTC / 21:27:44 JST。
- stage: `REGISTERING_TEMPLATE`、28%。
- 入力: `C:/Users/hanam/Desktop/債権者登録01.png`、3308 × 4676 px。
- 入力SHA-256: `73f49aac394feadb058a03fae1db83f298bbd7533a0e0ce28021a080b4831d6d`。
  指定ファイル、job input、jobのimage SHAが一致した。
- snapshot基準画像SHA-256:
  `e6af7eef1267e85a316fb1b3effcca1669f818931839342c8789266cb99c2bcd`。
  保存snapshotファイルの実バイト列と一致した。

## 実コード上の経路

`ImageExtractionService.process` は入力の正規化後、
`TemplateAligner.align(normalized.image_rgb, template)` を呼ぶ。
alignにはlayout cellsやfield mappingsを渡していない。
矩形crop、手書き抽出、provider呼出しは位置合わせ成功後である。
したがって本件では9項目のOCR認識結果は生成されていない。

`backend/app/image_processing/registration.py` の現行条件:

1. ORB特徴点を各画像最大5000点抽出し、descriptor比率で候補を選ぶ。
2. RANSACで射影変換を求める。
3. `_orb_quality_sufficient` が一致数、inlier数・比率、残差、点の広がり、
   射影の形状を確認する。
4. 条件を一つでも満たさないと射影変換を捨て、単純resizeからECCを開始する。
5. fallbackではECCスコアと輪郭一致率の両方が必要。

## 同じ入力・snapshotによるローカル再現

製品関数を直接呼び、失敗時のqualityをPythonの診断traceで取得した。
`ImageExtractionService.process`自体は呼ばず、job更新・外部API呼出しは行っていない。
読み込んだ設定の出力はregistrationの数値だけに限定した。
稼働中プロセスの過去の全設定は保存されていないため、以下は現worktreeと現在の
ローカル設定による再現値であり、過去job内で記録済みだった数値ではない。

正規化は書類全体を選択した。boundary confidence = 0.997853、fallbackなし。
検出四隅は約 `(0,0), (3305.35,0), (3305.35,4673.08), (0,4673.08)`。
内部の小さな表だけを紙面として切り出した症状ではない。

| 指標 | 再現値 | 現行条件 |
| --- | ---: | ---: |
| ORB source / template特徴点数 | 5000 / 5000 | ― |
| descriptor一致候補 | 392 | 30以上 |
| inlier数 | 130 | 20以上 |
| inlier比率 | 0.331633 | **0.4以上に未達** |
| inlier平均再投影誤差 | 2.275512 px | 5 px以下 |
| 点の広がり | 0.662797 | 0.08以上 |
| ORB射影coverage | 1.084019 | 0.45～1.65、形状条件も成立 |
| fallback ECC | 0.298489 | 0.75以上に未達 |
| fallback輪郭一致率 | 0.331992 | 0.7以上に未達 |

`_orb_quality_sufficient` の棄却理由はinlier比率である。
match数不足、inlier数不足、平均残差超過、空間的な偏り、射影形状の異常ではない。

### 棄却候補を保持した診断実験

本番の判定条件は変更していない。関数内の採用前homographyを診断用に取り出し、
別途warpPerspectiveと既存の `_ecc_refine` を実行した。

| 処理 | 輪郭一致率 | ECC |
| --- | ---: | ---: |
| 現行fallback | 0.331992 | 0.298489 |
| ORB候補を適用、ECC前 | 0.624550 | ― |
| ORB候補を適用、ECC後 | **0.715040** | 0.692762 |

棄却前候補は実際には有用な初期値である。
補正後は現在の輪郭一致率条件0.7を超える。ただしECCはfallback条件0.75を下回る。
現行ORB経路はECCスコアの下限を最終拒否条件にしていないため、
この結果だけから「全ての品質条件を通過した」とは言えない。

補正後画像に保存済み9項目の矩形をそのまま重ね、対応する記入欄に重なることを
目視確認した。crop境界の誤差・筆画clearance・OCR精度を定量検証したものではない。
郵便番号欄など境界に近い筆画もあり、目視だけで安全なcropを保証しない。

診断画像と数値はローカルの `backend/data/diagnostics/registration-20260911/` に保存した。
入力内容を含む画像を文書配下へ複製・外部送信していない。

## 汎用的な解決方針（未実装）

### 第一段階: 診断の保存

失敗時にも拒否理由、各候補のquality、最終変換、基準／入力SHA、補正比較画像を
job/attemptに紐付けて保存する。現在のAppError metadataはmatch/inlier数だけで、
JobRunner._failの保存eventはattemptNumberだけである。
抽出artifactの通常保存まで到達しないため、現状では失敗の数値が残らない。
安全な数値metadataと画像artifactを分け、providerの秘密情報は保存しない。

### 第二段階: 変換候補の生成と最終採否を分離

数値不正・退化・反転などの明らかに不正な変換は棄却する。
それ以外は、inlier比率だけで候補を即座に捨てず、補正を試せる候補として保持する。
従来の強いORB候補、条件不足のORB候補、従来fallbackを区別し、補正後に評価する。
低比率候補を従来ORBと無条件に同じ採用規則で通すことはしない。

採用判断には残差・空間分布・変換の妥当性に加えて、推定に使った点だけではない
独立した印刷構造の整合と、対象field周辺の誤差を使う。
同じ印刷枠が反復する帳票では隣行・隣列への誤整列を拒否できる条件が必要。
候補間が僅差なら勝者を推測せず、手動確認へ回す。

今回の候補保持実験はこの方向を支持するが、安全な採否規則の確立は別途必要。
`min_inlier_ratio=0.3` のような一律の閾値変更だけで完了しない。

### 第三段階: 描画差を考慮した独立検証

罫線・交点・長い線分など印刷構造を補助的な検証に使い、手書き量や
フォント・折返しの違いだけで全体評価が低下する問題を分離する。
罫線のない帳票もあるため、罫線専用の方式に全帳票を置き換えない。
画素許容誤差はDPI・field寸法・隣接領域の距離と関連付け、単に膨張幅を増やさない。
読み取り矩形を1個に減らしても、全紙面の位置合わせ根拠は保持する。

さらに実印刷とXLSX基準の差が大きい帳票には、実際の空帳票を基準画像として
登録し、XLSX由来fieldとの対応を確認する経路を検討できる。
これは取得の手間とTemplateVersion / snapshotへの明示保存が必要であり、
公開版の基準画像だけをその場で差し替えてはいけない。
この案を新printLayout rendererのqualification解除と混同しない。

### 変換記録の関連課題

現在 `RegistrationResult.homography` はORB射影または単位行列のままで、
ECC追加変換とfallbackのresizeを合成して返していない。
今回の28%失敗の直接原因ではないが、補正後cropと元画像の対応を検証する設計では
最終適用変換を正しく保存する必要がある。影響先を確認せず返値だけを変更しない。

## 実装時の完了条件

1. 本件の保存入力とsnapshotで、9 mappingのID・座標・保存先を変えず位置合わせを成立させる。
2. 対象fieldごとの境界誤差・筆画欠損・隣接欄混入を、人手で確認した期待領域と比較する。
3. 同じ帳票の全矩形／最小矩形で位置合わせ結果が変わらないことを固定する。
4. 他の複数帳票、異なる手書き量、縮尺・回転・余白・遠近、文字描画差でも比較する。
5. 類似別帳票、反復行の一段ずれ、一部欠落、白紙、無関係画像で誤採用を拒否する。
6. 既存成功ケースのcrop・mapping・レビュー対応が変わらないことを確認する。
7. Safety Contract §8に従い、変更前後のOCR contract、関連境界、backend全体を検証する。
   frontendを変える場合は対象テスト・buildも追加する。
8. 文字認識まで検証する場合のみ既存予算内で実APIを使い、認識誤りと誤対応を別に測る。

## 今回の検証と限界

- 実ファイル・DBを読み取り専用で照合し、同じ入力とsnapshotでエラーを再現した。
- 診断用候補保持実験と保存mapping重ね描きの目視確認を行った。
- backendで `.venv/Scripts/python.exe -m pytest tests/synthetic/test_opencv_pipeline.py -q --basetemp=.pt-registration-investigation-20260911` を実行し、8件通過。
- `git diff --check` 通過。製品コードを変更していないため、backend全体・frontend・
  OCR contract scriptの変更前後検証は今回実施していない。
- OpenAI / Google API呼出しは0回。実認識精度、association mismatch、mapping誤対応件数、
  schema error、recognition-only errorは未実測。実UIでの再実行・成功は確認していない。
- 製品コード・設定・公開版・過去jobを変更しておらず、既存OCR contractを維持した。
  本件の原因調査と解決設計までであり、不具合修正完了・OCR安全性の全面検証とはしない。

## 修正実装

2026-09-11、ユーザーの「修正作業を開始してくれ」に基づいて実装した。
今回追加した製品変更は `backend/app/image_processing/registration.py` と
`backend/app/services/extraction_service.py` に限定する。既存の未コミット変更を保持する。

### 採用した解決

低inlier率の候補をそのまま採用する案よりも、双方向で一意に対応する特徴点を使って
改めて変換を推定する方が、既存閾値を維持したまま本件を解決できることを確認した。
片方向候補を単に0.3の閾値で通す修正は行っていない。

1. 従来経路を先に実行する。成功した場合は画像も採否もそのまま返す。
2. 従来経路が `LOW_REGISTRATION_CONFIDENCE` で失敗した場合のみ、source→templateと
   template→sourceの両方でdescriptor比率条件を満たし、相互に対応する点を選ぶ。
3. この点群でRANSACを再実行し、一致数・inlier数・比率・残差・射影形状の既存閾値を要求する。
   空間分布はsourceとtemplate両方を確認する。
4. ECC補正後も形状・向き・元のinlierに対する平均残差を再検証し、ECCと輪郭の既存閾値も要求する。
5. 画像を3×3に分け、32 edge画素以上ある区画のすべてでtemplate輪郭の支持率を確認する。
   少なくとも4区画を要求し、各区画に既存輪郭閾値0.7を適用する。
   sourceの追加手書きはこの方向付き指標の分母に入れず、全体の対称輪郭一致検査も別に維持する。
6. 受付snapshotのcrop対象枠と直近3 pxの印刷境界にも同じ検証を行う。
   3 pxは検証範囲だけであり、cropのpaddingや保存座標を増やさない。
   32 edge画素未満の空領域は根拠に数えない。

方式名は `MUTUAL_ORB_RANSAC_ECC`。新経路はECCを合成した最終変換も返す。
従来経路のhomography返値は互換性のため変更していない。
全体や各欄の輪郭検査は独立した画像画素に基づくが、帳票内容の意味的な一致を保証するものではない。
同じ幾何を持つ別様式の完全識別や、すべての印刷差・撮影条件への対応を主張しない。

### 失敗時の診断

`RegistrationFailure` に安全な数値と比較画像を分離して持たせる。
抽出serviceは失敗時にも正規化画像、基準画像、拒否比較画像、診断JSONを既存の
ArtifactWriterでjob / attemptへ保存する。page指定時は既存のpage分離を維持する。
JSONにはinput、template、configのSHAとregistration設定、拒否理由を含む。
API keyや設定全体を保存しない。runnerの失敗状態・コードは従来どおりで、OCRへ進めない。

### 実帳票での結果

元のDBをread-onlyで開き、対象jobのsnapshotと入力だけを新しい隔離DB・storageに複製した。
本番の失敗job、ACTIVE版、画像、mappingを更新せず、実際の `ImageExtractionService.process` を実行した。
snapshotの画像SHAとconfig SHAを実serviceで検証し、OpenAI向けの9 mappingを選択する
経路を通ったうえで9 cropを生成した。providerは呼んでいない。

| 指標 | 修正後 |
| --- | ---: |
| 双方向一致点 | 237 |
| inlier数・比率 | 96 / 0.405063 |
| ECC | 0.773421 |
| 全体輪郭一致 | 0.781681 |
| ECC後の元inlier平均残差 | 3.992101 px |
| 3×3区画のtemplate輪郭支持率 | 0.814720～0.966966 |
| 9 field付近のtemplate輪郭支持率 | 0.977332～1.0 |
| crop数 | 9 |

9 mappingのID、targetPath、cropのbboxを保存snapshotと比較して一致した。
生成cropを一覧画像で目視確認した。先頭の空欄、法人名、代表者名とフリガナ、郵便番号、
住所、電話番号、所属部署、担当者の物理領域を確認した。文字の認識精度は未測定。
境界に近い筆画があるため、全筆画のclearanceを保証する報告ではない。

ローカル検証成果物:

- `backend/data/diagnostics/registration-fix-20260911-replay/verification.json`
- 同ディレクトリの `crop-contact-sheet.png`、`legacy-comparison.json`
- 同ディレクトリの `additional-forms.json`、`additional-forms-paths.json`

### 回帰・境界検証

- 変更前と変更後の `scripts/verify_ocr_contract.py`: ともに194 passed（既存warning 1件）。
- `tests/synthetic/test_registration_recovery.py` と既存 `test_opencv_pipeline.py`: 計22件通過。
  新経路の単体検証では従来失敗を明示的に注入し、縮尺・斜行・射影・手書きのある画像で
  既知の独立した格子点に対する誤差2 px未満と入力不変を確認する。
  欄単位の欠落、区画欠落、部分画像、白紙、無関係画像、ECCの行ずれ・反転・退化・失敗を検証する。
  この注入テストを実画像の自然な失敗再現と混同しない。
- `tests/integration/test_registration_diagnostics.py`: 2件通過。
  実DB・storage・runner・正規化・位置合わせを使い、attempt 1/2の診断保存、FAILED/28%、
  snapshot不変、後続の手書き抽出非実行を確認する。
- HEADの変更前registration実装を別moduleとしてロードして比較した5ケースでは、
  aligned画素列、homography、qualityがすべて完全一致した。
- 別の3種類の人工帳票は、従来・修正後とも従来ECC fallbackで成功し、輪郭スコアも同一だった。
  これらを追加経路だけに強制投入した場合は3件とも幾何条件で拒否された。
  追加経路がすべての帳票を救済する結果ではなく、従来の成功を維持する必要性を示している。
- 対象Ruff、対象mypy（`--follow-imports=silent`）、`git diff --check` 通過。

- backend全体: `.venv/Scripts/python.exe -m pytest --basetemp=.pt-registration-full-20260911`
  は811 passed、既存1 skipped、4 warnings、607.23秒。失敗なし。
  新規追加は上記synthetic 14件とintegration 2件であり、既存テストの削除・skip追加・期待値弱体化はない。

frontend、provider、prompt/model/schema、Google、出力、mapping生成、crop計算、
公開テンプレート、過去snapshotの変更はない。既存のidentity・association・補正保存の
OCR contractを維持した。位置合わせの追加復旧条件はSafety Contract §3.5へ明記した。

### 稼働環境と実OCR

2026-09-11 22:02 JST、QUEUED / PROCESSINGが0件であることを再確認してバックエンドを
再起動した。frontendも5173で再起動し、実Chromeでバックエンド接続中・worker稼働中を確認した。
バックエンドhealthもstatus=ok、database=ok、jobWorker=running、ocrProvider=openaiを返した。

認証cookieなしの直接POSTは401で拒否されたため、認証を変更せず、
既にログイン済みのChromeで対象テンプレート・指定画像を選び、1ジョブを新規作成した。
元の失敗jobをretry／上書きしたものではない。ブラウザー上で28%を通過し、
100%・レビュー画面・9セル表示まで確認した。

- 新job: `3c028333-d92e-4c45-8130-a7cbfadacf8f`。
- 状態: `REVIEW_REQUIRED` / `READY_FOR_REVIEW` / 100%、未確定、revision 1。
- 入力SHA、fieldMappings全体、template config SHAが元の失敗jobと同一。
- 本番の位置合わせ方式: `MUTUAL_ORB_RANSAC_ECC`、輪郭一致率0.781681。
- OpenAI実呼出し: 2回、9領域画像（通常8領域とdocument identity 1領域）。
- model: `gpt-5-mini`、prompt: `mapped-compact-v1`、schema: `1.0`。既存設定のまま。
- association token不一致0件。manifest→crop画像SHA→mappingId / targetPath /
  sourceRegionId→mappedCells response / boundingBoxの9件を照合し、誤対応0件。
- schema errorは観測0件、保存validationはvalid=true。
- 補正保存・内容確定・Google出力は実行していない。本作業でGoogle APIを直接呼び出していない。

実測記録は `backend/data/diagnostics/registration-fix-20260911-replay/live-verification.json`。

**文字認識の残件は位置合わせ修正と区別する。**
代表者フリガナはMODEL_UNCERTAINであり、レビュー画面の要確認1件として保持されている。
郵便番号はcrop内に末尾の1があるが、実responseは末尾を落とした `123 002` で、
readable / OKになっている。少なくとも1件のrecognition-onlyの欠落を目視確認した。
境界拡大や帳票固有の文字補完で結果を変更していない。
この2項目は人間の確認が必要であり、100%到達を全項目の認識正解と扱わない。
全帳票・全筆画の認識率やclearanceを保証する検証ではない。

### 今回の変更ファイル

- `backend/app/image_processing/registration.py`
- `backend/app/services/extraction_service.py`
- `backend/tests/synthetic/test_registration_recovery.py`
- `backend/tests/integration/test_registration_diagnostics.py`
- `docs/ocr-safety-contract.md`（§3.5のみ今回追記。既存の未コミット追記を保持）
- 本書

`AGENTS.md`の追加変更、staging、commit、pushは行っていない。
