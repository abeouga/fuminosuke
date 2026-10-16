# Test Artifact Storage Policy

AIテンプレート作成フェーズ4（2026-10-09〜10）の専用DB・移行・実HTTP・保存・境界・再起動・実API記録は
`D:/llm-ocr-archive/template-ai/20261009-phase4`。[フェーズ4記録](docs/template-ai-phase4-2026-10-10.md)を参照する。
`backup-source`、`backup-db`、`before.json`、`after.json`、`preservation.json`で開始時との差分を照合する。
検証scriptとfixtureは製品外に置く。`runtime_fixture.py`は有料providerだけ固定し、実HTTP/共通保存を検証するための専用入口。
`http-validation.json`、`more-http-validation.json`、`boundary-validation.json`、`lifecycle-validation.json`と`runtime.log`を保存。
`live-*-response*.json`と`live-provider-validation*.json`は実API3回の原応答と拒否を保持する。品質成功に読み替えない。
通常データへscriptを向けない。通常DB未移行。共有API台帳225/496、残271。重い回帰と画面受入は後続工程。

AIテンプレート作成フェーズ3（2026-10-09）の設計検証と保持照合は
`D:/llm-ocr-archive/template-ai/20261009-phase3`。[フェーズ3設計](docs/template-ai-phase3-2026-10-09.md)を参照する。
`validate_design.py`、`proposal-example.json`、`existing-operation-example.json`、`compiled-layout-example.json`、
`authoring-limit-input.json`、`validation.json`を保存。新API実装のテストではなく、schema/既存操作への接続例と50操作制限の再現。
`before.json`、`after.json`、`preservation.json`で既存差分と全テーブルの保持を照合。実API0回。

AIテンプレート作成フェーズ2（2026-10-09）の固定入力・評価器・実API応答・時間・採点・保持照合は
`D:/llm-ocr-archive/template-ai/20261009-phase2`。[フェーズ2記録](docs/template-ai-phase2-2026-10-09.md)を参照する。
初回の不成功は`prototype-v1`と`raw/T1-*-1-*.json`に保持。主要比較はv2の各帳票2反復で、第3反復・種類だけの診断を混ぜない。
`analysis-direct-only.json`は表展開前の採点履歴。現行`analysis.json`は直接結合と表展開・完全性監査を併記する。
HTTP200・構造チェック通過・意味の正しさを区別する。実API40回、222/496、残274。製品コード変更なし。

AIテンプレート作成フェーズ1（2026-10-09）の読み取り専用調査・固定画像・入力・重点期待値・保持照合は
`D:/llm-ocr-archive/template-ai/20261009-phase1`。 [フェーズ1記録](docs/template-ai-phase1-2026-10-09.md)を参照する。
`cases-manifest.json`が採用caseの正本。除外前の`cases/H2`を再利用せず、manifestの`cases/H2-valid`を使用する。
全459セルのうち役割88・結合39・行7の重点期待値であり、全帳票精度の証明ではない。API今回0。

音声・Decisions再設計フェーズ7（2026-10-09）の開始退避、コピーAPI、全体回帰、保存OCR応答・画像SHA・XLSX、
実マイク時刻と不具合再現は `D:/llm-ocr-archive/voice-assistant/20261009-redesign-decisions-phase7`。
[フェーズ7記録](docs/voice-editing-redesign-phase7-2026-10-09.md)を参照する。通常切替は未実施。
`silence-before.log`／`recent-before.log`は製品の欠落再現。`recent-after.log`／`recent-after2.log`はcontext DTOの検証側指定漏れ。
`recent-after3.log`と`recent-edit-results.json`が対象省略42条件の成功結果。関連再実行は`*-recent-final.log`。
`recent-live-diagnosis.json`の不成功を保持し、説明修正後の成功は`recent-live-diagnosis-182.json`。
録音・全文transcript・tokenを新しい時刻ログへ保存しない。追加300回の明示許可後はAPI182/496、残314。
追加許可前の実測ログの196上限は当時の履歴として保持する。
以下は各工程時点の履歴。

音声・Decisions再設計フェーズ6（2026-10-09）の開始退避、制御測定、失敗再現、frontend対象33件／build、
実画面通信・PNG、今回差分と保持照合は `D:/llm-ocr-archive/voice-assistant/20261009-redesign-decisions-phase6`。
[フェーズ6記録](docs/voice-editing-redesign-phase6-2026-10-09.md)を参照する。
`frontend-before.log`／`pending-before.log`／`disconnect-before.log`は修正前・修正途中の失敗証拠。
`frontend-target.log`が最終結果。`ui-network.json`は認証headerを保存せず通信時刻・URLだけを記録する。
制御中央値334→61msを実AI／実音声の速度と混同しない。API今回0、156/196、残40。
以下は各工程時点の履歴。

音声・Decisions再設計フェーズ5（2026-10-09）の退避、コピーAPI、手動保存、frontend限定検証、
差分と保持照合は `D:/llm-ocr-archive/voice-assistant/20261009-redesign-decisions-phase5`。
[フェーズ5記録](docs/voice-editing-redesign-phase5-2026-10-09.md)を参照する。
専用8025／5195の実画面受入を完了。`ui-acceptance.json`、`ui-motion.json`、画面PNG／DOM記録を保存。API今回0、156/196。
制御VAD／WebSocket、DOM部品、固定resolverを使う検証を、実マイク・実AI精度や速度の証拠と混同しない。
`manual-api.log`の物理binding拒否traceは意図した負例。`mypy-imports.log`は対象外の依存先エラーも含む。
以下は各工程時点の履歴。

音声・Decisions再設計フェーズ3（2026-10-08〜09）の開始退避、コピーDB、実AI応答・遅延、
正解付き校正、API故障注入、保存実応答の再生、静的検査と保持照合は
`D:/llm-ocr-archive/voice-assistant/20261008-redesign-decisions-phase3`。
[フェーズ3記録](docs/voice-editing-redesign-phase3-2026-10-09.md)を参照する。
実API13回、150/196、残46。音声取得なし。実AIのJSONは合成文字指示の試験結果で、実録音ではない。
`planner-verification-results.json`の65条件、`acceptance-results.json`の54条件と実AI試験を区別する。
`calibration-results.json`／`live-*.json`には校正前の不成功も保持する。
`planner-verification-unchanged-fixture.log`は検証側の初期状態不足の失敗で、製品NO_ACTIONの回帰ではない。
通常DBの変更は共有API台帳だけ。元の業務表・855ファイル・従来試験DBを保持する。
以下は各工程時点の履歴。

音声・Decisions再設計フェーズ2（2026-10-08）の開始退避、通常／試験DB backup、
コピーmigration、実ASGI API検証、保存response／状態・型検査、差分と保持照合は
`D:/llm-ocr-archive/voice-assistant/20261008-redesign-decisions-phase2`。
[フェーズ2実装記録](docs/voice-editing-redesign-phase2-2026-10-08.md)を参照する。
試験の解析器は制御用差し替えであり実AI精度の証拠ではない。実音声取得なし。実AI使用0、137/196。
同フォルダー内の`acceptance.log`には意図した故障注入・拒否のtraceを含む。結果は`acceptance-results.json`で区別する。
以下は各工程時点の履歴。

音声・Decisions再設計フェーズ1（2026-10-08）の開始退避、旧草案、Schema確認、今回差分、
DB／855業務ファイル保持確認は D:/llm-ocr-archive/voice-assistant/20261008-redesign-decisions-phase1。
[フェーズ1設計契約](docs/voice-editing-redesign-phase1-2026-10-08.md)を参照する。
実装・設定・DB変更なし、実AI使用0、台帳137/196。製品build／動作検証は未実施。

音声編集・AI向けAPI再設計のフェーズ0（2026-10-08）の開始ソース／差分、通常・試験DB退避、855業務ファイルSHA、
共有台帳確認、過去HTTP遅延集計、read-only runtime確認、終了保持照合は
`D:\llm-ocr-archive\voice-assistant\20261008-redesign-phase0`。
[フェーズ0の基準と次工程](docs/voice-editing-redesign-phase0-2026-10-08.md)を参照する。
API137/196、残59、今回外部AI使用0。製品コード／業務DB変更なし。以降の古いAPI件数は各工程時点の履歴である。

追加フェーズ3（2026-10-07〜08）の開始時1006ソース/文書・31テーブル・855業務ファイル、
全件回帰・コピーAPI・数字列/意味付け・UI・静的検査/build、実マイクの試験値とreceipt metadataは
`D:\llm-ocr-archive\voice-assistant\20261007-phase19\autocorrection-phase3`。
[進行中の最終検証](docs/voice-target-autocorrection-phase3-2026-10-08.md)。API93/100、数字列00789の実保存を確認。欄名のみの回答で値を引き継ぐ修正を検証し、再起動/追加枠を依頼中。
失敗した初回全件のログと修正した検証補助を保持し、正常条件の2回目と区別する。実音声/全文字幕は保存しない。

追加フェーズ2の開始時1005ソース/文書、再現・修正前後の結果、短いコピーAPI/UI確認、静的検査/buildと保持照合は
`D:\llm-ocr-archive\voice-assistant\20261007-phase19\autocorrection-phase2`。
[独立レビュー記録](docs/voice-target-autocorrection-phase2-2026-10-07.md)。全件回帰・実マイクは追加フェーズ3。

欄名自動補正の追加フェーズ1（2026-10-07）の開始時1004ソース/文書、通常DB、855業務ファイルSHA、
今回だけの差分、静的検査/build/保持照合は `D:\llm-ocr-archive\voice-assistant\20261007-phase19\autocorrection-phase1`。
[追加実装記録](docs/voice-target-autocorrection-phase1-2026-10-07.md)を参照する。
API63/100、実API追加0。重いテストと実マイクは追加フェーズ3。通常切替は未実施。
以下の43回/TTS試験の記載は、以前の試行時点の記録として保持する。

更新日: 2026-10-07（deploy版の現行検証経路を反映）

フェーズ19の進行中証拠・実マイク用業務コピー・正常台帳の保持照合・切替/近似候補検証は
`D:\llm-ocr-archive\voice-assistant\20261007-phase19`。実音声・STT全文は保存しない。
[フェーズ19記録](docs/voice-authoring-phase19-2026-10-07.md)を参照する。
API累計43/100。実TTS等と通常切替は未受け入れ。補助の失敗logを削除せず最終成功と分ける。

フェーズ17の開始時990ソース・文書、DB backup、855業務ファイル、stream/局所録音/通知のコピーAPI、
Chromeの模擬マイク・実VAD/MediaRecorder・録音decoder・レビュー保存復旧、テンプレートcomponentでの意味作成、
build/静的検査と依存監査は `D:\llm-ocr-archive\voice-assistant\20261007-phase17` に保存する。
[フェーズ17記録](docs/voice-authoring-phase17-2026-10-07.md)を参照する。
今回分は`phase17-only.diff`、保持照合は`preservation.json`/`copied-preservation.json`、
APIは`latest-*-run.txt`/`*-verification.log`、ブラウザーは`browser-results.json`/`template-ui-results.json`と画像。
模擬音声・STT/解釈/TTS制御応答を実マイク/実OpenAI成功の証拠としない。途中失敗も保存する。
外部AI追加0回、累計31/100回。通常DBの新schema未移行・新API無効・業務への試験書込なし。

フェーズ16の開始時983ソース・文書、DB backup、855業務ファイル、会話API応答fixture、
実OpenAIの文字指示4シナリオ、製品TSと実HTTPのqueue/復旧、手動/ページ回帰、生成型・静的検査は
`D:\llm-ocr-archive\voice-assistant\20261007-phase16` に保存する。
[フェーズ16記録](docs/voice-authoring-phase16-2026-10-07.md)を参照する。
今回分は`phase16-only.diff`、保持照合は`preservation.json`/`copied-preservation.json`、
成功runは`latest-*-run.txt`、フロントは`frontend-runtime-results.json`。失敗ログも保持する。
実APIの試験文字指示/有限catalog/responseはコピー環境の証拠とし、実利用者の録音を含まない。
追加API6回は通常DB台帳に予約した。累計31/100回。通常schema未移行、業務への試験書込なし。

フェーズ15の開始時976ソース・文書、DB backup、855業務ファイル、手動commandのAPI検証、
Chromeと実backendによる保存/故障/競合/線接続/undo/再読込、生成型、buildと静的検査は
`D:\llm-ocr-archive\voice-assistant\20261006-phase15` に保存する。
[フェーズ15記録](docs/voice-authoring-phase15-2026-10-07.md)を参照する。
今回分は`phase15-only.diff`、保持照合は`preservation.json`と`copied-ocr-preservation.json`、
API各runは`latest-*-run.txt`、UI証拠は`ui-requests.jsonl`、`ui-*.json`と`manual-wire-saved.png`。
試験script・コピーDB・未完成の下書きを通常環境へ戻さない。通常APIは無効、外部AI API追加0回、累計25回。

フェーズ14の開始時970ソース・文書、DB backup、855業務ファイル、コピーDBでの意味付けAPI検証、
実XLSXの2ページ保存、graph inverse・保存済みOCR比較、生成型と静的検査は
`D:\llm-ocr-archive\voice-assistant\20261006-phase14` に保存する。
[フェーズ14記録](docs/voice-authoring-phase14-2026-10-06.md)を参照する。
開始時に未対応だった13操作は `baseline-operation-gap.json`、今回分の差分は `phase14-only.diff`、
通常DB・業務ファイル・既存差分の保持は `preservation.json`、各成功runは `latest-*-run.txt`。
失敗・再実行を含むrunを残し、最終成功のlogと区別する。通常APIは無効、実API追加0回、累計25回。

フェーズ13の開始時958 Git対象/未追跡ファイル、DB backup、855業務ファイル、
新schemaの専用コピー、短いAPI/DB結合・隔離実起動・schema生成・型検査・保持照合は
`D:\llm-ocr-archive\voice-assistant\20261006-phase13` に保存する。
[フェーズ13記録](docs/voice-authoring-phase13-2026-10-06.md)を参照する。
今回分diffは`phase13-only.diff`、保持照合は`preservation.json`、run参照は`latest-run.txt`。
製品sourceは変更したが、通常DBの移行・新API有効化・実データへの試験書込は行わない。
実API追加0回、累計25回。コピーDBを実DBへ戻さない。

フェーズ12の開始時957 Git対象/未追跡ファイルのSHA、31 DBテーブルの内容digest、
DB本体等を除く860データファイルのSHA、編集前文書、文書検証・終了時比較は
`D:\llm-ocr-archive\voice-assistant\20261006-phase12` に保存する。
設計正本は [フェーズ12統合設計](docs/voice-authoring-phase12-design-2026-10-06.md)。
製品source・DB schema・実データは変更せず、実API追加0回、累計25回。
過去フェーズと採取対象が異なるため、ファイル件数だけで業務データ増減を判定しない。

フェーズ10〜11の開始時782ソース/文書・DB・855データファイル、実マイク試験用業務コピー、
API台帳・復元比較・画面画像・起動ログは `D:\llm-ocr-archive\voice-assistant\20261006-phase10`。
READMEは開始時の一括ソース対象に含まれていなかったため、今回挿入した利用手順だけを除去した
変更前内容を退避し、独立の差分へ記録した。製品ソース変更はない。
実APIは追加12回、累計25回。生の実録音と文字起こし全文は保存せず、
診断は固定句の分類と文字数だけを記録する。業務コピーを実DBへ戻さない。

フェーズ9の開始時781ソース/文書・DB・855データファイル、隔離API/DB・SDK応答fixture、
保存OCR応答の比較、通信失敗/再読込/undoの画面証拠、build・起動ログは
`D:\llm-ocr-archive\voice-assistant\20261006-phase9` に保存する。
実APIは追加0回。コピーDBでの模擬予約と実DBの累計13回を区別する。

フェーズ8のDB退避・制御音声・保存API応答・短い検証・ログ・画面画像は
`D:\llm-ocr-archive\voice-assistant\20261006-phase8` に保存する。
実録音や会話全文を通常データへ保存しない。検証用音声は架空の指示を読み上げた制御入力である。
実API予約の正本はdeploy DBの `api_call_ledger`。予算境界の100回試験は別DB・通信fixtureで行い、実消費13回と区別する。

## 移動履歴

検証済みのpytest一時生成物を、CドライブからDドライブへ移動した。

- 移動元: `C:\Users\hanam\Desktop\llm-ocr\backend\.pt-*`
- 移動元: `C:\Users\hanam\Desktop\llm-ocr\backend\.pytest-*`
- 移動先: `D:\llm-ocr-archive\2026-09-17\backend-test-artifacts\`
- 対象: 364ディレクトリ
- 容量: 事前集計で約23.18 GiB
- 結果: D側364ディレクトリ、C側の対象ディレクトリ0件
- 検証: コピー後に全364ディレクトリをrobocopyの差分なしモードで照合してから、C側を除去

対象ディレクトリには、pytestの各テスト用DB、画像、`result.json`、ログなどが含まれる。これは実行時の正式データではなく、検証実行の一時成果物である。

## 今後の必須ルール

同種の生成物をCドライブへ作成しない。pytestを実行する場合は、必ずDドライブ上の絶対パスを `--basetemp` に指定する。

例:

```powershell
python -m pytest tests -q --basetemp="D:\llm-ocr-archive\pytest-work\20260917-backend-full-01"
```

次の指定は禁止する。

```text
--basetemp=.pt-...
--basetemp=.pytest-...
```

`backend`をカレントディレクトリにした相対パス指定は、Cドライブ上に生成物を作るため使用しない。過去の検証Markdownに記載された相対 `--basetemp` は履歴であり、現在の実行コマンドとしてそのまま再利用しない。

## スクリプト固有の注意

行・列音声指定フェーズ2の変更前コード・コピーDB・再現・検証・生成物・ログは
`D:\llm-ocr-archive\voice-assistant\20261008-record-cell-phase2` に保存する。

汎用の行・列音声指定フェーズ1の退避・コピーDB・検証スクリプト・生成schema・build・ログは
`D:\llm-ocr-archive\voice-assistant\20261008-record-cell-phase1` に保存する。

デプロイ用コピーの `scripts/verify_ocr_contract.py` は汎用形式への非互換変換で廃止した。過去の記録にある同コマンドは現行の検証手順ではない。現在は依頼範囲の短い実DB/API検証、保存response比較、XLSX照合、frontend buildを使用し、全件unit/E2Eは合意した後半フェーズで実行する。

文之助フェーズ4の検証コード・コピーDB・画像・ログ・移行退避は `D:\llm-ocr-archive\voice-assistant\20261005-phase4` に置く。実行コマンド・証拠と未検証範囲は [フェーズ4記録](docs/voice-assistant-phase4-2026-10-05.md) を参照する。検証スクリプトを旧リポジトリや実データへ向けて実行しない。

文之助フェーズ5〜6は `D:\llm-ocr-archive\voice-assistant\20261005-phase56`。開始前DB・855ファイルのコピーとSHA、分離DBのmigration/integration/UI結果、XLSX、画面画像を保存する。現在の検証と次ブロックは [フェーズ5〜6記録](docs/voice-assistant-phase56-2026-10-05.md) を参照する。

文之助フェーズ7は `D:\llm-ocr-archive\voice-assistant\20261005-phase7`。開始前631ソース/文書・DB・855データファイル、隔離API/DB・実画面・故障注入・XLSXの証拠を保持する。最新の実装と次ブロックは [フェーズ7記録](docs/voice-assistant-phase7-2026-10-06.md) を参照する。

## 今回の移動対象外

文之助フェーズ18の開始前source/DB/855業務ファイル、全件テストの成功・失敗ログ、現行契約の検証、故障再現、実XLSX、Chrome画像、保持照合は `D:\llm-ocr-archive\voice-assistant\20261007-phase18`。pytestのbasetemp・mypy cache・再検証renderer TEMPもこの下に分離する。全件テストの失敗を退役済みfixtureの復活やskipで隠さず、[フェーズ18記録](docs/voice-authoring-phase18-2026-10-07.md)の分類と証拠を残す。

以下は移動していない。

- `backend/data`: 現行DB、アップロード、OCR成果物、バックアップ
- `backend/.venv`: バックエンド実行環境
- `.git`: Git履歴およびGit内部オブジェクト
- `.mypy_cache`、`.pytest_cache`、`.ruff_cache`: 既存のキャッシュ
- `frontend/node_modules`: フロントエンド依存関係

このファイルを、以後の作業でpytest生成物の保存先を判断する際の参照元とする。

## 行・列音声指定のフェーズ3成果物（2026-10-08）

開始時ソース/通常DB/855業務ファイル、全体テストと失敗分類、コピーAPI、保存OCR応答、
XLSXセル対応、台帳の予算/制約保守、実OpenAI文字解釈、Chrome画面証拠は
`D:\llm-ocr-archive\voice-assistant\20261008-record-cell-phase3` に保存する。
[フェーズ3検証](docs/voice-record-cell-phase3-2026-10-08.md)を現在地とする。
実マイクは利用者回答待ち。文字解釈と模擬音声を実認識の成功証拠にしない。
旧台帳CHECK保守の前後コピーと全行/外部キー照合を保存する。通常業務や新APIの切替とは区別する。
