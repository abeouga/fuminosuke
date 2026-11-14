# 複数ページ結果のレビュー・補正

複数ページの読み取り結果を、ページごとに確認・補正する専用のレビュー（API・画面・保存）の仕様。
全ページの確定・再編集は[全ページ確定・再編集](paged-confirmation.md)、
記入済みXLSXの取得は[全ページXLSX出力](paged-output.md)を参照する。

## 保存の境界

`PagedOcrExecution`はページ別artifactと`documentPages`を保存する。レビュー用の
原候補・補正現在値・revision履歴はこの専用の表に持ち、単一ページ用の`OcrResultService`は
複数ページのjobを`MULTI_PAGE_REVIEW_REQUIRED`で拒否する。

- 表`paged_reviews`: job ID、受付bundle SHA、page ID別の原候補/現在値、全体revision。
- 表`paged_review_revisions`: job ID＋revisionを主キーに、変更source・page ID・全ページ値を保存。
  どちらも`backend/alembic/versions/0001_generic_schema_v2.py`で作る。旧job/result/templateを移行・再構築しない。
- `PagedReviewService.initialize`: 全ページ成功を原子的に公開。欠落・重複・入力入替・snapshot違いを拒否。
  読み取り値のsourceRegionIdと受付mapping ID、padding込みcrop bboxを照合する。
  当該ページに設定されていない項目への非空値の混入も拒否する。
  同じ成功結果の再公開は冪等で、別結果への置換や補正の初期化は禁止。
- `get/correct`: 現在の版やOCR実行設定を読み直さず、受理済みsnapshotだけを参照。
  REVIEW_REQUIREDの全ページ結果だけを対象とし、全ページ共通baseRevisionを照合する。
  更新と履歴保存が一緒に失敗/成功する。補正値のFieldReading規約と画像品質を再検証する。
- provider/model/prompt/schema、登録・画像合わせ・crop生成・association方式は変えない。
  既存契約に不適合な結果はこの保存境界で拒否する。legacy経路は変えない。

## APIと画面

- `GET /api/v1/ocr-jobs/{jobId}/page-review`
- `POST /api/v1/ocr-jobs/{jobId}/page-review/{pageId}/corrections`
- body: `baseRevision`と`fields`。各fieldはmappingIdとraw/normalized/status/alternatives/reviewReason。
  targetPath・sourceRegionId・boundingBoxは編集payloadへ含めない。
- response: 全ページの明示identity、range、入力revision/SHA、reference SHA、値、検証結果、crop URLと、
  受付時document snapshotに固定された`reviewStructure`。
  cropのartifact名にもpage IDを含む。同名の項目でも別ページの領域へ移らない。
- responseには、各page ID所有の位置合わせ済み実入力画像URLも含む。
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
  レビューと同じ見出し・繰り返し表・ツールバー、緑/黄の判定色、セル選択、ダブルクリック編集、判定ポップオーバーを使う。
  常時表示の判定プルダウンは置かない。ページ移動後もpage ID別の下書きを保持する。
  OCR実行に記録されたAPI呼出回数、入力/出力/合計token、推定費用が取得できる場合は画面上部へ表示する。
  確定後は編集を無効化するが画像・表・ページ切替は維持し、明示reopen成功後だけ編集を再開する。
  legacy単一ページの`ReviewPage`、保存・確定・出力経路は変えない。
  Page 1だけを単一ページの確定・出力経路へ渡さない。

## 共通結果UIの適用範囲

右側の表・セル背景色・ツールバー・直接編集・判定ポップオーバーは
`components/OcrReviewResults.tsx`を単一ページと複数ページで共用する。
画面は入力元の拡張子やXLSXの範囲文字列で分岐せず、専用レビュー応答のページIDと項目を使用する。
「次の要確認」は全ページを巡回し、対象ページとcropを同時に表示する。
選択領域の表示中も現在ページ数を表示し、ページ移動で他ページの下書きを破棄しない。

複数ページ受付は`JobPageService.catalog`の`printLayout`を前提とする。
この共通UIだけで画像由来テンプレートの複数ページ作成・受付まで対応したとは扱わない。
画像由来の受付対応にはページ定義・受付snapshot・出力対応の設計と実装が別途必要である。
