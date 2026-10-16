# 文之助 操作パネルと選択状態の実装記録

2026年10月5日のフェーズ2〜3の成果です。[設計書](voice-assistant-design-2026-10-05.md)のブロック2を、`C:\Users\hanam\Desktop\llm-ocr-deploy` に実装しました。OCRレビューとテンプレート編集で、対象を選び、文字列または分類から変更案を確認できる状態です。今回の変更案はローカルの型付きプレビューであり、AIによる発話解釈、サーバー検証、承認、保存はまだ接続していません。

## 実装した範囲

| ファイル | 役割 |
| --- | --- |
| `frontend/src/assistant/contracts.ts` | OCR・template別のcontext、操作、preview、編集adapterの型。preview providerに保存処理を持たせない |
| `frontend/src/assistant/useInteractionContext.ts` | 画面instance、選択世代、編集世代。A→B→Aや編集→元に戻す場合も古い案を識別 |
| `frontend/src/assistant/localPreviewProvider.ts` | 明示入力から変更前後を作るローカルpreview。HTTP・外部API・保存を行わない |
| `frontend/src/assistant/AssistantPanel.tsx` | 変更前後、原本範囲、取消、未保存選択、保存中・失敗の表示。古い非同期応答を破棄 |
| `frontend/src/assistant/templateSelection.ts` | editorから画面へ渡す選択情報。cell、markSet、choice、ページ、保存失敗を公開 |
| `frontend/src/assistant/assistant.css` | 通常の文字・操作密度。広い画面は右列、1440px以下は幅390px以内の右ドロワー |
| `frontend/src/components/LayoutEditor.tsx` | 選択adapter、同期的なautosave停止、明示保存・破棄、保存失敗状態。既存編集と意味接続は維持 |
| `frontend/src/components/LayoutMarkEditors.tsx` | 既存の選択肢をIDで操作対象に指定するボタン |
| `frontend/src/pages/ReviewPage.tsx` | OCR対象のmappingId/targetPath/revision、dirty、入力途中、競合、確定状態との連携 |
| `frontend/src/pages/TemplatePage.tsx` | version・選択・工程・ページ・dirty・保存状態との連携。マスク・印刷編集を別扱い |
| `frontend/src/main.tsx` | assistant用のCSSを読み込む |

変更案はeditorのdraftへ入れません。「変更を適用」と音声入力は準備中として無効です。既存の手動保存APIは、未保存変更に対して「保存して続行」を明示的に選んだ場合のみadapterから利用します。これは変更案を適用するAPIとは別の用途です。

テンプレートの一般revisionはまだ存在しないため、contextのresourceRevisionはnullです。現在はbaseline signatureと編集世代でローカル案を失効させます。これをサーバーの競合検知の代わりにしてはいけません。選択肢はcellId、markSetId、choiceIdを持ち、別セル・ページ・工程へ移った場合には選択肢の指定も解除します。

マスク・印刷の未保存変更、OCRの入力途中・書類名変更、競合、公開済み・確定済みは、既存の編集操作で解決するよう案内します。保存後の再取得失敗など正本が不明な状態では、既存の入力を保持し、adapterによる破棄も拒否します。

## 検証した内容

| 証拠 | 結果と範囲 |
| --- | --- |
| `npm run build` | TypeScriptとVite build成功 |
| 短い境界integration | 実コンポーネントを架空入力・保存callbackで動かし9件成功。処理時間は約0.4秒、全体約2秒。全件unit/E2Eは未実施 |
| OCR実画面 | 5180の未確定結果で1項目を選び、CHK-001→CHK-002の案を表示。表の値はCHK-001のまま、適用は無効 |
| テンプレート実画面 | DRAFT版の1セルで名称変更案を表示。別セルの選択で案が消えることを確認 |
| 保存response比較 | OCR resultとtemplate layoutは、実画面操作の前後でGET応答が一致 |
| 差分確認 | `git diff --check` 成功。開始前の変更を退避したファイルと比較して今回の変更範囲を確認 |

9件では、先頭ゼロ・空欄のpreview、古い非同期応答、dirty・保存中・失敗状態、手編集での失効、選択肢保存値の表示、複数選択、予約済みautosaveの停止、明示保存、失敗時の入力保持を検証しました。callbackでの確認は実DB保存の検証ではありません。

検証用config、integration、response、画面証跡は製品外の `D:\llm-ocr-archive\voice-assistant\20261005-phase23` に保存しています。Vitestの初回実行は、CドライブをrootとしたままDドライブの絶対includeを指定したためファイル解決に失敗しました。検証rootをDドライブへ変更した後に9件を実行し、成功しています。製品コードの失敗を隠すassertion変更はありません。

境界確認の再実行コマンドはfrontendディレクトリで次のとおりです。

```powershell
.\node_modules\.bin\vitest.cmd run --config D:/llm-ocr-archive/voice-assistant/20261005-phase23/phase23.config.mts --reporter=default --reporter=json --outputFile.json=D:/llm-ocr-archive/voice-assistant/20261005-phase23/boundary-results.json
```

backend、DB、設定、依存関係は変更していません。OCRの生成・補正payload・mapping生成・crop・snapshot・revision・確定・出力処理にも変更を加えていないため、既存OCR contractを維持しています。保存・出力・OCR品質の全体回帰を今回検証済みとは扱いません。外部AI APIの使用は累計0/100回です。

## 次フェーズへの引き継ぎ

次はブロック3、フェーズ4、Astra／maxです。開始前に現在の差分を再確認し、設計書の保存とrevisionの契約を基準に実装します。

1. OCR補正・confirm・reopen・再処理を含む共通revisionとtransaction境界を整える。既存履歴を振り直さない。
2. TemplateVersionの整数revision、統合authoring API、不変設定artifactとDB参照切り替えを実装する。既存手動書込経路を残さず棚卸しする。
3. 既存DBコピーと新規DBのmigrationを確認する。動的create_allの初期migrationとの二重追加を避ける。
4. request receipt、冪等キー、操作履歴のtransaction基盤を作る。
5. frontendのcontextと保存adapterを新APIへ接続する。ローカルpreviewのID・世代・before値をサーバーが信頼する設計にはしない。

fieldLabelの保存、4つの許可操作、サーバー提案・承認・適用はブロック4へ、永続undoはブロック5へ、STT/TTSはブロック6へ引き継ぎます。実マイクが必要な時点で利用者に参加を求めます。この実装記録をもってフェーズ2〜3の終了とし、次ブロックには自動着手しません。
