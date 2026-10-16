# AIテンプレート作成 フェーズ6：監査・回帰検証

2026-10-10。開発対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。
フェーズ3設計のフェーズ6（Astra / High）として、ID・関連・手修正保護・予算・遅着応答・保存・出力を監査した。
再現した6件を修正し、今回の対象回帰は解消した。バックエンド全件テストには既存の失敗が残るため、全体成功とは扱わない。
通常DBの移行・機能有効化・実AI/実マイク受入は行っていない。

## 再現した問題と修正

| 問題 | 原因と変更 | 再現・修正後の証拠 |
| --- | --- | --- |
| 反映待ち中に同じ分類を手動指定するとAIが上書きする | JSON一致だけでは手動意図を検出できない。反映要求に手動操作の通番を固定し、変化時は下書きを保持 | `race-before.log` / `race-after.log`、store対象テスト |
| AIと同じ値を手動で再指定した後、AI取り消しで手動意図も消える | 取り消しにも反映時の手動通番を照合する | 同上 |
| 同一入力の解析をサーバーが統合した場合、画面が結果を無視する | 作成応答のrequestIdを自分の要求に限定していた。認証済み作成応答のresourceを照合してanalysisIdへ結び、以降はそのIDを照合。元の要求は応答不明時の再送用に保持 | 同上、実HTTPのcoalescing |
| ブラウザ保存領域不足で復旧処理が再び例外になる | 自動保存を即時保留し、解析・反映・保存を停止。明示復旧で記録できてから再開する。受領済み業務保存をローカル記録失敗で失敗扱いに戻さない | 開始前・反映後・保存receipt後の3条件。実Chromeの保存障害注入と復旧 |
| 反映応答待ちに編集対象が変わると旧対象の案を採用する | 応答反映時にもowner、revision、context、interaction、catalog、scopeを照合する | `scope-before.log`、修正後store対象テスト |
| 未確認のAI下書きを別画面で復元すると自動保存できる | localStorageはタブ共通だが保留journalはタブ固有。保留下書きには自動保存再開のbaselineを付けず、復元時は既存の明示確認へ移す | `restored-before.log`、修正後componentテスト、別Chromeタブの確認表示とDB revision |

古い保留mergeに手動通番がない場合は、遅着案をそのまま反映せず再反映を要求する。
再読込・別画面で保留下書きを復元した場合には、内容の確認操作が追加される。通常の手動操作完了時の保存方式は変更しない。
新しい保存API、OCR変換、DB schema、provider、モデル変更はない。

## 変更ファイル

- `frontend/src/editing/templateAnalysisStore.ts`：手動意図・編集対象の照合、統合された解析の受領、記憶域障害からの復旧。
- `frontend/src/editing/TemplateAnalysisPanel.tsx`：記憶域障害中の操作制限と復旧ボタン。
- `frontend/src/components/LayoutEditor.tsx`：保留下書きの復元時に明示確認を要求。
- 上記storeとLayoutEditorの既存テストファイル：8つの未検証条件を追加。既存assertionは弱めていない。
- 本記録、`AGENTS.md`、`docs/ocr-safety-contract.md`：現在地と検証の限界を記録。

## 実行結果

成果物：`D:/llm-ocr-archive/template-ai/20261010-phase6`。
実行コードとDBは開始時に退避した。専用コピーのbackend 8036 / frontend 5206、実SQLite・storage・HTTP・Chromeを使用し、AI providerだけ固定応答に置換した。

| 検証コマンド・操作 | 結果 |
| --- | --- |
| frontend `npm exec vitest run` | 変更前22ファイル135件成功、最終22ファイル143件成功。`frontend-baseline.log` / `frontend-final2.log` |
| frontend `npm run build` | TypeScript・Vite成功。`frontend-build2.log` |
| backend `.venv/Scripts/python.exe -m pytest --continue-on-collection-errors --basetemp=<専用空directory>` | 1,261成功・23失敗・1skip・34収集エラー、318秒。実OpenAI統合無効。`backend-baseline.log` |
| `verify_more_http.py`（`verify_http.py`も実行） | 27＋12条件成功。受付・統合・認証・SSE・部分反映・共通保存・undo・競合・取消・期限・画像変更・古い範囲・保存済み実応答の拒否を検証 |
| `verify_boundaries.py` | 分類・表構造・参照・操作上限等24条件成功。外部APIなし |
| `replay/verify_ocr_replay.py` | 保存帳票72項目とcrop等290画像を照合。旧providerとの25条件で要求・manifest・association・展開結果が一致。欠落・重複・未知ID・crop不足を検査 |
| `replay/verify_output.py` | 実コピーDB/APIで7条件成功。補正2値を1revisionで保存、再確定・再編集、75値・72crop binding保持、XLSX95セル比較。`00789`と`=1+1`は指定セルの文字列 |
| `replay/verify_pages.py` | 実XLSXを2ページに分け、仮想見出し編集・逆操作、ページ越境拒否、checklist可否等5条件成功。印刷参照とgeometry維持、有効化なし |
| 実Chrome | 保存領域障害で保留と再取得を表示し、復旧後に再操作可能。AI3単位を下書きへ反映、別タブで確認必須・自動保存なし。元タブの明示保存でrevision 2、関連5・行2、解析1回のまま。両タブのerror/warnなし |
| 保全・差分 | 両通常DB全テーブル・両HEAD・旧llm-ocrソース不変。通常863ファイル、artifact781件のSHA一致。`preservation.json`、`phase6.patch`、`git diff --check` |

全件バックエンド失敗の一覧は、2026-10-09の `backend-recent.log` と同じ57エントリーである。
`backend-failure-comparison.json`に照合結果を保存した。主因は削除済みrenderer/module、廃止したmigration/API、旧`document.*` mapping、旧業務属性・CSV helper・LayoutService.saveを要求するテスト。
テストの削除、skip追加、期待値の弱体化はしていない。今回バックエンド製品コードは変更していないため、同一コードの全件試験を修正後に再実行してはいない。

OCR replayには日付1件の既存差分がある。旧保存候補の「26 9 27 → 2014年9月27日」に対し、現行の汎用形式は「26 9 27」を保持する。今回追加した差分ではなく、保存済み値を書き換えていない。全72項目の値完全一致とは表現しない。
provider比較の対照は退避済みの旧実装であり、今回の開始前/後の全OCRコード同一性はソースhashで別に確認する。

検証準備で保存応答fixtureの不足、検証用SQLの列名誤りを検出し修正した。製品側の失敗とは区別した。
画面のファイル保存はブラウザツールのパス制約に拒否されたため、同ツールの画像出力を保存した。

## 保持した契約・残る範囲

反映は下書きまで、業務保存は明示確認後の既存manual-authoring・revision・receipt経路を維持する。
OCR provider/prompt/model/schema、mappingId/targetPath/sourceRegionId、crop、受付snapshot、確定snapshot、出力bindingは変更していない。既存OCR contractを維持した。
ただし、OCR安全性を完全には検証できていない。今回の2ページ検証は既存adapterによる意味編集であり、新規paged OCR実行やrenderer qualificationの証明ではない。
複数タブの全同時操作・端末クラッシュ・破損journalの全故障組合せ、全ページ画面操作、AI精度・速度・実マイクは未受入。

API追加 **0回**、共有台帳 **225/496、残271**。通常設定・通常DB未移行・通常editing/解析無効を維持した。
次は **フェーズ7：GPT-6.1 Sol / High、実帳票の速度・品質・手直し時間の受入**。
APIモデルは引き続き `gpt-6-luna`。初回最大40送信の計画を維持し、実マイクが必要な時点で利用者を呼ぶ。本工程では開始しない。
