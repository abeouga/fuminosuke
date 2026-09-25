# 複数ページ結果のレビュー・補正

2026-09-13、本番OCR・レビュー・補正・確定・出力までの要求のうち、専用レビューと補正を追加した。
この工程だけで有効化・全ページ確定・出力の完了とはしない。

後続工程で[全ページ確定・再編集](paged-confirmation.md)と受付snapshot拡張を追加した。
以下は最初のレビュー/補正工程の記録。現在のmigration・確定対応状況は後続記録を参照する。

## 原因と変更境界

`PagedOcrExecution`はページ別artifactと`documentPages`を保存するが、従来はレビュー用の
原候補・補正現在値・revision履歴がなく、旧`OcrResultService`は明示拒否していた。
フロントエンドだけで解消できない永続化境界である。

- 新表`paged_reviews`: job ID、受付bundle SHA、page ID別の原候補/現在値、全体revision。
- 新表`paged_review_revisions`: job ID＋revisionを主キーに、変更source・page ID・全ページ値を保存。
- migration `0032_paged_review`: 表を追加するだけ。旧job/result/templateを移行・再構築しない。
- `PagedReviewService.initialize`: 全ページ成功を原子的に公開。欠落・重複・入力入替・snapshot違いを拒否。
  読み取り値のsourceRegionIdと受付mapping ID、padding込みcrop bboxを照合する。
  当該ページに設定されていない項目への非空値の混入も拒否する。
  同じ成功結果の再公開は冪等で、別結果への置換や補正の初期化は禁止。
- `get/correct`: 現在の版やOCR実行設定を読み直さず、受理済みsnapshotだけを参照。
  REVIEW_REQUIREDの全ページ結果だけを対象とし、全ページ共通baseRevisionを照合する。
  更新と履歴保存が一緒に失敗/成功する。補正値のFieldReading規約と画像品質を再検証する。
- provider/model/prompt/schema、登録・画像合わせ・crop生成・association方式は変更していない。
  新しい保存境界が既存契約に不適合な結果を拒否する。legacy経路は変更しない。

## APIと画面

- `GET /api/v1/ocr-jobs/{jobId}/page-review`
- `POST /api/v1/ocr-jobs/{jobId}/page-review/{pageId}/corrections`
- body: `baseRevision`と`fields`。各fieldはmappingIdとraw/normalized/status/alternatives/reviewReason。
  targetPath・sourceRegionId・boundingBoxは編集payloadへ含めない。
- response: 全ページの明示identity、range、入力revision/SHA、reference SHA、値、検証結果、crop URLと、
  受付時document snapshotに固定された`reviewStructure`。
  cropのartifact名にもpage IDを含む。同名の項目でも別ページの領域へ移らない。
- 2026-09-22の表示拡張で、responseへ各page ID所有の位置合わせ済み実入力画像URLも追加した。
  画像はjob artifactの`page.{pageId}.alignedSource`であり、現在のTemplateVersionや基準画像から
  再生成しない。保存済みreadingのboundingBoxは補正bodyへ送らない。
- `ReviewPage`は専用のpaged拒否codeだけを`PagedReviewPage`へ分岐する。
  一般的な通信失敗をpaged判定としない。OcrPageは専用結果の存在確認後にレビューを開く。
- ページ切替と閲覧・入力は保存中も可能。保存ボタンは重複送信を防止する。
  送信後の追加編集だけを残し、古い応答で画面を巻き戻さない。
  通信断/競合は保存成否不明として全下書きを保持し、明示再取得前の再送を止める。
  下書き/送信中は画面移動・beforeunloadの確認を使用する。
- 専用画面は、帳票プレビューの左右に重ねた前後矢印でページを切り替える。左側には選択中ページの
  位置合わせ済み帳票全体または選択欄のcropを表示する。上部にページタブは置かない。右側は単一ページの
  レビューと同じ見出し・繰り返し表・ツールバー、緑/黄の判定色、セル選択、ダブルクリック編集、判定ポップオーバーを使用する。
  常時表示の判定プルダウンは置かない。ページ移動後もpage ID別の下書きを保持する。
  OCR実行に記録されたAPI呼出回数、入力/出力/合計token、推定費用が取得できる場合は画面上部へ表示する。
  確定後は編集を無効化するが画像・表・ページ切替は維持し、明示reopen成功後だけ編集を再開する。
  legacy単一ページの`ReviewPage`、保存・確定・出力経路は変更しない。

この初期工程ではconfirmAvailable/outputAvailableはfalseだった。後続工程で専用確定/reopenを追加したが、
[専用XLSX出力](paged-output.md)も追加した。文書ライブラリ統合は残っている。
旧APIのMULTI_PAGE_REVIEW_NOT_AVAILABLE/MULTI_PAGE_OUTPUT_NOT_AVAILABLEは維持し、
Page 1だけを既存の確定・出力経路へ渡さない。

## 検証

### 2026-09-22 共通結果UIの適用範囲

右側の表・セル背景色・ツールバー・直接編集・判定ポップオーバーは
`components/OcrReviewResults.tsx`を単一ページと複数ページで共用する。
画面は入力元の拡張子やXLSXの範囲文字列で分岐せず、専用レビュー応答のページIDと項目を使用する。
「次の要確認」は全ページを巡回し、対象ページとcropを同時に表示する。
選択領域の表示中も現在ページ数を表示し、ページ移動で他ページの下書きを破棄しない。

ただし現行の複数ページ受付は`JobPageService.catalog`の`printLayout`を前提とする。
この共通UIだけで画像由来テンプレートの複数ページ作成・受付まで対応したとは扱わない。
画像由来の受付対応にはページ定義・受付snapshot・出力対応の設計と実装が別途必要である。
この表示工程ではOCRロジック・受付・mapping・補正payload・確定/出力の契約を変更しない。

今回の確認: 対象frontend 49件、`npm test -- --maxWorkers=1`は36ファイル/463件通過、
`npm run build`成功（既存の500 kB chunk警告あり）、`git diff --check`成功。
Chromeの既存実結果で4ページの切替、別ページから要確認セルへの移動、選択cropの読込完了、
セル背景色、記録済みAPI使用量表示を確認した。単一ページ結果の共通ツールバー/表も確認した。
実データの補正保存・確定・XLSX出力は今回操作していない。追加のOpenAI/Google呼出しはない。

### 初期工程の検証記録

- 開始時OCR契約: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、210 passed（60.82秒）。
- 変更後に同じOCR契約コマンドを直列実行: 210 passed / 1 warning（40.88秒）。
- 対象frontend: PagedReviewPage / ReviewPage / OcrPage、49 passed。
- 追加したページレビュー/gateway試験8件が通過。最終frontend全体は33 files / 355 passed
  （17.30秒）、`npm run build`成功。500 kB超chunk警告は残る。
- 既存の汎用mock-resultは別帳票の固定値とheader_identityを返し、crop bboxがnullだった。
  ページ試験用providerを、実requestのmapping/target/cropに対応するblank responseへ変更した。
  他のモック・本番providerは変更せず、既存ページ数・retry・crop画素・本番gateのassertionは保持。
- 途中のbackend試験2本を重ねたことで一方がRENDERER_BUSYとなった。排他制約を緩めず直列で再試験する。
  source/crop検査追加後の初期対象13件は通過。後続のレビュー＋migration対象16件は通過
  （373.46秒）。さらに追加した同時保存/履歴失敗rollbackは全体試験に含めて検証する。
- 最終backend全体: `cd backend; .venv/Scripts/python.exe -m pytest tests -q -o addopts='' -p no:cacheprovider --basetemp=.pt-page-review-full-0913`。
  932 passed / 1 skipped / 3 deprecation warnings、1013.20秒。追加した同時保存/rollbackも通過。
  skipは既存のものを維持しており、今回の失敗をskipで回避していない。
- frontend全体の初回ではtestは通過したが、buildが新テストfixtureの型不足とTesting Libraryの
  不正なexact optionで失敗した。fixtureを型契約に合わせ、型の無効化はしていない。
- 新service/domain/routes/executorの限定mypy成功。変更Python対象Ruff成功。
- Chromeの分離したUI harnessで2ページのナビゲーション・項目・判定・未対応確定/出力表示を
  accessibility treeで確認。サーバーへの通信・実データ登録を行わない試験用APIであり、
  実API/実ブラウザー保存の証拠とはしない。補正操作を行う前にcomputer-useの
  Windows browser URL判定が失敗し、安全制御により停止した。回避せず操作検証を終了し、
  一時harnessファイルは除去した。

## ローカル実環境への反映

- active job 0を確認し、旧backendと付属frontendを停止。
- SQLite backup APIで`backend/data/backups/pre-paged-review-20260913.db`を新規作成。
  既存バックアップを上書きしない。バックアップはgit対象外。
- `0032_paged_review`を適用。起動スクリプトにもupgrade処理があるため、再起動後のheadも照合した。
  既存31テーブルの全行digestは移行前・backup・移行後で同じ
  `083b27cb01e07408055e9315b927225d71074bb36a6eb547b74ace50c9d8a443`。
  新しい2表は空。既存データを新しい結果形式へ変換していない。
- `scripts/dev.ps1 -NoBrowser`で反映。health `ok`、frontend HTTP 200、OpenAPIに専用GET/POSTを確認。
  これはroute登録の証拠であり、実ブラウザー補正・実OCR実行の証拠ではない。
- 対象版`6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`は4ページのDRAFTのまま。
  作業都合で有効化・本番qualificationフラグを変更していない。

実ブラウザー補正と明示的なOpenAI/Google呼出しは行っていない。認識品質・実応答association mismatch等は
未測定。本番gateは閉じたままで、OCR安全性を完全には検証できていない。
