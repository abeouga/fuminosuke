# 文之助の実マイク受け入れと利用手順

2026年10月6日、フェーズ10〜11、GPT-6.1-Sol / highの実行記録。
対象は `C:\Users\hanam\Desktop\llm-ocr-deploy`。実マイクの代表操作と利用手順の整備を完了した。
製品のbackend/frontendソース、DB schema、OCR処理は変更していない。

## 実マイクで確認した操作

利用者の参加を求め、Chromeと実マイクを使用した。STT・変更指示の抽出・TTSは実OpenAI API。
専用業務DBコピーで操作し、外部AI送信の予約だけはdeploy実DBの共通台帳へ記録した。
読み上げの可聴性は利用者の回答、保存と復元は画面・再取得・DBで確認した。

| 対象 | 実行と結果 |
| --- | --- |
| OCR受付番号 | 実音声から `CHK-001 → 00123` の案を作成。先頭ゼロを保持。利用者がボタンで適用し、revision 1→2 |
| OCR取り消し | 案の実TTS再生後、別録音で「取り消しを適用」。承認turnを保存transactionで消費し、revision 2→3。raw候補と補正後の全データが開始時と一致 |
| テンプレート名称 | 実音声から `受付番号 → 受理番号` の案を作成。承認前のTemplateVersion全列が開始時と一致 |
| テンプレート適用 | 固定句以外の承認で拒否され、待機中に案が失効。同じ値を手入力で再提示し、実TTS→実マイクの「変更を適用」で保存。revision 1→2、選択セルのfieldLabelだけが変更された |
| テンプレート取り消し | 復元差分を表示し、ボタンで適用。revision 2→3。セル・ID・座標・関係を含むlayout全体が開始時と完全一致 |
| 固定句以外 | 「それでよろしく」等の返答、「承認する」を承認へ推測変換せず拒否。拒否による業務保存はない |

テンプレートの最初の音声案と最終適用は、期限切れによる再提示を挟んでいる。
同じ音声proposalが途切れず適用まで進んだという証拠とは区別する。
今回の実マイク検証は1利用者・代表2操作の確認であり、複数話者や雑音条件の認識性能は未評価。
分類変更・選択肢名称変更の実発話は今回試していない。4操作の保存境界・故障条件はフェーズ9の記録を参照する。

## APIの実消費

開始13回、今回追加12回、累計 **25／100回、残り75回**。
内訳は今回STT6、変更案抽出2、TTS4、OCR0。累計STT9、変更案抽出4、TTS12、OCR0。
固定句不一致や期限切れがあっても、送信済みの呼び出しを払い戻していない。
全予約はdeployの `api_call_ledger` に残り、業務コピーの台帳を正本へ戻していない。

## 変更した文書

- `README.md`：対象選択、録音、案の照合、固定句承認、保存結果、取り消し、期限切れ・結果不明時の手順。
- `AGENTS.md`：現在の完了ブロック、検証範囲、累計API消費と残る限界。
- `ARTIFACT_STORAGE_POLICY.md`：実マイク試験用コピーと証拠の保存先。
- この実行記録。

音声指示と音声承認の区別を利用手順へ明記した。固定句以外の拒否は既存仕様に従う。
製品の承認ロジックを今回変更していない。既存の未コミット変更を保持し、staging・commit・pushは行っていない。

## 証拠と検証方法

成果物は `D:\llm-ocr-archive\voice-assistant\20261006-phase10`。

| 証拠 | 内容 |
| --- | --- |
| `baseline-hashes.json` / `before.db` / `data-hashes.json` | 開始時782ソース・文書、DBオンライン退避、855データファイル。READMEの開始内容は今回の挿入部分だけを除去して個別に退避 |
| `live-results.json` | OCR保存・音声undo、テンプレート承認前の変更ゼロ、音声保存後の単一fieldLabel差、undo後の全layout一致、操作・revision・承認turn |
| `template-after-apply.json` / `template-after-undo.json` | 実APIからのauthoring再取得結果 |
| `ocr-real-voice-proposal.png` / `ocr-real-voice-button-save.png` / `ocr-real-mic-undo.png` | OCRの案・保存・音声取り消し画面 |
| `template-real-voice-proposal.png` / `template-real-mic-save.png` / `template-real-mic-undo.png` | テンプレートの実発話案・音声承認保存・取り消し画面 |
| `verify_final.py` / `final-audit.json` | 実DB全行・855 SHA・コピーの復元・外部API台帳・今回変更範囲・文書参照の照合 |
| `runtime-final.json` | 通常8010 health、5180 HTTP 200、音声設定、累計25回。試験用8013/5183のlistenerなし |

最終照合はdeploy rootから `backend/.venv/Scripts/python.exe D:/llm-ocr-archive/voice-assistant/20261006-phase10/verify_final.py`、
文書差分は `git diff --check`。今回の製品ソース変更はないため、unitの追加・全件unit/E2E・buildの再実行は行っていない。
フェーズ9のbuild・境界検証は過去の実行証拠として扱い、今回の新規テスト通過とは報告しない。

試験用の同じサーバーからビルド済み画面を配信した。専用フロントエンドの接続先設定・起動は
自動承認レビューに拒否され、詳細な理由は返されなかったため、この配信方法へ変更した。
試験終了後はサーバーを停止し、Chromeを通常の `http://127.0.0.1:5180/` へ戻した。
生の実録音・文字起こし全文をDBやファイルへ保存していない。診断ファイルは固定句の分類と文字数だけである。

実業務DBはAPI台帳以外の30テーブル全行・全列が開始時と一致し、855データファイルのSHAも一致する。
OCR provider・prompt/model/schema・association・crop・受付snapshot・補正・revision・出力adapterは変更していない。
既存OCR contractを維持した。新たな実画像の認識精度やpaged/renderer資格の認定を示すものではない。
このモデルブロックを完了し、次の開発を自動で開始しない。
