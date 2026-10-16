# 修正6-B：優先領域の表示と操作可能時刻

2026-10-14 JST。対象checkoutは `C:\Users\hanam\Desktop\llm-ocr-deploy`。
実行モデルはGPT-6.1 Sol / High、turn `01a138a5-1709-78f3-b58c-f40a2c2f2fba`。

**R4の表示・計時を修正し、制御応答の実Chromeと共通保存で確認した。6-Cの独立再審査と6-Dの実Luna測定は未実施である。改善ゴールは未完了。**

起点は[6-C再審査](template-ai-region-rereview-2026-10-14.md)。開始時に同審査の109ファイル封印、現ソース、通常データと共有API台帳436/924・残488を照合した。

## 変更内容と境界

到着順に並べていた案を、受付後に確定した領域計画の順序で表示する。後着の優先領域も先頭に入る。
既存のページ順を維持し、各ページ内の順位を使用する。

frontendに公開されるcellIdsは実際の変更セルだけであり、所有領域全体や計画順位を示さない。
そのため `VisualRunner.validate` で実計画の順位を `displayOrder` として付与し、HTTPのEditUnitへ追加した。
これはモデル回答から受け取る値ではない。frontendやモデルに書込み範囲を決めさせず、既存のregion ID・digest・所有検査を維持する。

`displayOrder` は0開始・非負・nullableの表示情報である。旧案はnullのまま従来順で表示でき、画面の可視性検査は旧案にも適用する。
公開APIはtemplate-analysis.v3。DB移行、provider契約marker、モデル向けschema/prompt/画像pack、モデルGPT-6 Lunaとreasoningを変更しない。

反映ボタンを案一覧の上へ置き、各案の詳細は展開式にした。代表的な変更内容を最大2件、常時表示する。
要約の表示件数を品質分母には使わない。採点はその領域全体の回答・compiled案・保存結果に対して行い、残る内容は「変更内容を確認」で閲覧できる。
一つの要約が正しいことだけで領域やページ全体の完成とは扱わない。

計時は以下を区別する。

- `rendered`：選択controlが画面内で表示され、hit-testが通る。
- `actionable`：対象の変更要約、選択control、反映controlが表示され、hit-testが通り、既存の反映可能条件を満たす。
- `selected`、`merge_requested`、`applied`、`saved`：選択・反映要求・下書き反映・明示保存の別時刻。

選択待ちだけで反映ボタンが無効な場合は、チェック操作を開始できる時刻を記録する。人が選択するまで待たない。
画面外、overflowによる切り取り、固定表示による遮蔽、非表示document、busy/recovery/失効/保護を操作可能と数えない。
DOM側とstore側の両方でguardする。markのdetailにcontrolの有効性・可視性・hit-test・要約の表示を残す。

描画後2フレームで判定し、scroll、resize、詳細の開閉、transition、documentの可視性、focus、panel/bodyの寸法変更で再判定する。
常時pollingは追加しない。古いcallbackは解除し、別analysisへの準備完了記録は拒否する。

AI解析開始後に利用者の操作がなければ、先頭の反映可能案が隠れている場合だけ自動で表示位置を調整する。
入力中、pointer/keyboard/wheel/touchによる操作後、反映後はこの自動移動を行わない。手動操作後は、その案が実際に見えるまで計時しない。

## 変更ファイル

| ファイル | 変更 |
| --- | --- |
| `backend/app/template_analysis/contracts.py` | 読取専用のdisplayOrderをDTOへ追加 |
| `backend/app/template_analysis/visual_runner.py` | 実計画から表示順位を付与 |
| `docs/contracts/template-analysis-v3/http.schema.json` | DTOから生成したHTTP schema |
| `frontend/src/editing/template-analysis.generated.ts` | 同じDTOから生成した型 |
| `frontend/src/editing/TemplateAnalysisPanel.tsx` | 計画順の表示、変更要約、詳細展開、反映controlの位置、描画後の再判定 |
| `frontend/src/editing/TemplateAnalysisPanel.css` | 詳細展開の局所style |
| `frontend/src/editing/analysisControlVisibility.ts` | viewport・祖先の切り取り・CSS表示・hit-testの検査 |
| `frontend/src/editing/templateAnalysisStore.ts` | 可視性と要約の追加guard |
| `frontend/src/editing/TemplateAnalysisPanel.test.tsx` | 再現した2欠落の回帰検証、既存の計時検証のDOM条件 |

上記9ファイルに、本報告とAGENTS/Safetyの現在地追記を加える。既存差分をstaging/commitしていない。
通常OCR、共通GeometryCompiler/compose/authoring保存、canonical ID、mapping/targetPath、crop、revision、出力の製品実装は変更していない。

## 検証

修正前に「画面外の案にもactionableが付く」「後着の優先領域が先頭にならない」の2テストが失敗した。
修正後の対象31件、最後のfrontend全23ファイル161件、TypeScript/Vite buildを通過した。
対象backend2ファイルのRuffとmypyを通過した。

制御HTTP、実ASGI、専用SQLite、共通保存の14条件を通過した。途中反映、digest差替え拒否、同値の手修正保護、geometry変更拒否、receipt/取り消し、22入力と54物理ID、保存・別接続再読込を含む。
追加した表示順位が公開DTOを通って計画の0〜3に対応することも確認した。
検証script、元応答、失敗・成功ログは製品外へ保存した。全backend unit/E2Eや新規の記入済みOCRは実行していない。

専用8084/5284と8086/5286を既存の起動許可の範囲で起動した。providerのHTTPだけを制御し、外向きsocketを拒否した。
通常DBを試験先にせず、前の8078/5278、8080/5280、8082/5282の保存証拠を上書きしない。

最終形のChrome結果は `final-ui-acceptance.json` を正本とする。

| 条件 | 結果 |
| --- | --- |
| 後着の優先領域、1920×898、初期scroll=0 | 固定範囲 `(0,0,963,911)` が最後に届いても先頭表示 |
| クリック→その領域のactionable | **4807.7ms**。要約2見出し、checkbox、反映controlが表示・hit-test通過 |
| 画面外の案 | 未記録。表示された第三領域は後から記録され、まだ切り取られた末尾案は未記録 |
| 人が選択するまで待つ | 同じactionableは1件だけ。選択・反映・保存は別mark |
| 4領域の反映・明示保存・ブラウザー再読込 | receipt1、revision1→2、54セル・22入力先・異なる22mapping/targetPathが一致 |
| 手入力中の後着 | 「先頭の固定文字」の文字、focus、viewport上の入力欄の位置を保持。隠れた優先領域は計時しない |
| 手入力後に案を表示 | 新しいactionableが付く。手入力を保存したという試験ではなく、専用版はrevision1・receipt0 |
| 優先領域が先着、1920×600 | 自動scroll=480で要約・選択・反映controlが表示される。これは追加の小画面確認 |

4807.7msは制御応答による計測機構の検証であり、実Lunaの速度実績には含めない。
fixture投入と起動はクリック前に行い、文脈取得・snapshot/画像準備・検証・画面通知はクリック後の計時に含めた。
実Luna品質、実Lunaの5秒、安定したp95、全帳票の完成は未証明である。
途中版の4882.6msと旧画面検証も保存しているが、最終形の証拠と混同しない。

最終の画面2ケースと小画面ケースにconsole errorはない。初期の手入力用frontendをbackend準備前に開いた際の7件のVite接続拒否は、時刻付きの元ログを保持した。解析・手入力はhealth200後である。
起動scriptへhealth待ちを追加したが、その追加部分の再起動試験は行っていない。稼働中の環境を止めていない。
検証用のauthoringレスポンスkeyとDOM selectorの誤認、programmatic scrollへの自動表示を考慮しなかった検証assertionは `preparation-errors.txt` に記録した。
失敗時のbackupと証拠を残し、製品guardやgold、既存source testを緩めていない。

## 実API計画と次工程

元の `region-v2-20261014` の配分G1=4、G2=24、G3=60、計88・上限96・未配分8を維持する。
全24 reference要求を再生成し、元の画像・検出結果・要求hash・provider契約・Luna/noneが一致した。
新しい表示コードのhashは別の `allocation-review.json` へ追記固定し、過去のmanifestを上書きしない。
実trialのresource/session、snapshot/plan/region ID、設定・本文hashは、6-C通過後かつ外部送信前に固定する。
G1の逆転・混入・保護違反・対応不一致・速度未達による停止と、無断再試行・low fallback・旧未送信23試行の再利用禁止を維持する。

R04の承認済み145位置・143機能・手動2、新しい3ページの承認済みgold、最終未使用4帳票を変更していない。
実APIと通常予約は**0回**、共有台帳436/924、残488。専用の制御予約**24回**は別記し、共有残数から控除しない。

証拠は `D:\llm-ocr-archive\template-ai\20261014-region-visibility-fix`。
最終の保持照合は `preservation.json`、固定ソース・証拠は `completion-1/hashes.json` を正本とする。
通常データ、旧checkout、過去の封印、既存差分を保持し、既存OCR contractを維持した。

次は **6-C / Astra / XHigh** の独立再審査。合格後に **6-D / Astra / High** の実API・実画面測定へ進む。
本実装工程を独立審査済みとは扱わない。新しい別エージェントは起動していない。改善ゴールは未完了である。
