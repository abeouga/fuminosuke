# AIテンプレート作成フェーズ19：v5変換・入力パックの製品外実装

2026-10-11。実装工程GPT-6.1 Sol / High。対象checkoutは `C:\Users\hanam\Desktop\llm-ocr-deploy`。実装と検証成果物は `D:\llm-ocr-archive\template-ai\20261011-phase19`。

**focused v5の変換処理と入力パックを製品外コピーへ実装した。新入口の49条件、既存12条件の両入口確認、旧24応答の再生は通過した。** [フェーズ18設計](template-ai-phase18-design-2026-10-11.md)のschema/prompt・固定scope・採点・比較protocolは変更していない。実API使用は0回。製品採用不合格・通常未移行/無効を継続する。

## 1. 実装内容と変更境界

Phase17 runtimeを本フェーズの `runtime/` へコピーし、Phase18の [focused v5契約](contracts/template-analysis-v5/README.md) を `runtime/design-v5/` に固定した。事前に `acceptance-plan.json` へ10群の受入条件を保存した。

追加したruntimeファイルは次の2つだけ。既存397ファイルはbyte単位で保持した。

| ファイル | 役割 |
| --- | --- |
| `runtime/focused.py` | v5 shape/参照の検査、label付き/direct/form/recordsを既存semantic処理へ変換、応答の完了・拒否・取消等の判定、compile前の再照合 |
| `runtime/evidence_pack.py` | immutable snapshotとscopeへ入力パックを束縛、元座標・global cell refsを保持、局所画像と全体overviewの生成、3方式の送信body組立て |

v5を明示的に既存compact DTOへ変換し、Phase17の `scoped_resolve` → 共通normalizer → 共通compiler → manual変換へ通す。旧応答をv5としてparseするfallbackはない。保留単位のrole・値所有・親宣言は既存の共通収集に渡し、正常側を先勝ちで採用しない。

label付きはhorizontal/verticalと非空labels、directは明示名と空labelsへ変換する。recordのtitleはheading参照または明示名、value列はlabeled/directを分離する。名前導出はv5契約に従い、textをtrimして空textを除いて結合するが、label参照自体は削除しない。空または100文字超の導出結果はnullのまま共通保留経路へ渡し、代替名を生成しない。formは各行の非空labelを必須とし、既存の親宣言保持を使う。

`needs_context` はunknownと組み合わせ、保留理由を別metadataへ保持する。追加送信は発生させない。完了していない応答、refusal、取消・timeout、複数出力、partial/duplicate-key JSON、モデル不一致は案の入口で拒否する。これは製品UIのエラー表示や実通信の確認ではない。

## 2. 入力を減らすときの保持

全物理snapshotと全write/read集合を保持し、providerへ見せる集合だけを分ける。元のglobal cRefと元座標を維持し、pack内の順番からセルを再採番しない。既存catalogを使う場合は物理・revision・generation・状態digestを照合し、親chainもread contextに残す。

packed方式はwrite cellsの外接矩形＋64 pixelsを元画像へclipし、同じ座標の画素をPNGへ切り出す。局所crop/highと元ページoverview/lowを組み合わせる。近傍printed cellと凡例の全文をread-onlyで残す。H2の遠い記号凡例c20も保持する。F-lowは全ページ画像/highと全セルを使う。

pack digestにはsnapshot/scope、schema/prompt、catalog state、provider evidence、画像bytes SHA、crop origin/detailを含め、resolve/compileでも再検査する。providerが見ていない参照、許可していないgrid、範囲外の値所有・role・親書込みは受理しない。範囲外の未知親を指定した案は依存単位を保留し、その親を作らない。既存のread-only label/parentは書込みを起こさず参照できる場合だけ使う。

通常OCRのcrop生成、矩形の検出・変更、mapping生成、HTTP API、DB schema、保存処理、UIは今回の実装範囲外。

## 3. 短い受入結果

`acceptance.py` は手で作成した期待案と固定画像を使い、v5入口から共通compile/manual変換までを確認する。期待案はAPI出力ではない。元goldの正解を含む入力を作った検証なので、GPT-6 Lunaの正解率とは扱わない。

| 検証 | 結果 |
| --- | ---: |
| 新入口の境界条件 | 49/49通過 |
| Phase17の既存12条件・元のv4入口 | 12/12通過、旧結果と一致 |
| 同じ12条件を明示変換してv5入口へ通す | 12/12通過、受理/保留・claims・案が一致 |
| Phase15の旧v3/v4保存応答再生 | 24/24一致 |
| 上記24案の共通/manual意味投影・物理参照 | 24/24保持 |
| 上記24案の既存graph完全no-op | 24/24一致、全ID/pathを含む |
| 既存runtime/designファイル | 397/397不変、追加2ファイルのみ |

49条件には、5帳票の期待binding/parent/mark/行順、空行、名前・alias、direct列、printed row key、範囲外cell全体の保持、元画像とのcrop画素一致、H2凡例、F/P同一write scopeを含む。snapshot所有者/revision・schema/scope/pack SHA・source bytes/crop originの改変、未知/非表示参照、範囲外の値/role/grid、不正shape、名前・mark根拠不足、同値手動意図と既存意味・親・roleの保護、unknownと値所有の競合も確認した。

既存12条件は、保留formと別fieldの親競合、宣言順逆転、同じ親、列重複/重なり/不正順/空ラベル、親facet保護、値所有/role競合を含む。新しい名前を付けた同じ試験を追加せず、元の入力と期待結果を再利用した。

旧24応答は元のv3/v4契約で再decodeし、元request/scope/source hash、採点、誤結合/欠落、受理/保留、名前/共有/mark/親/表/行を照合した。旧E4の不合格値や保存時間を新しい性能結果へ置き換えていない。

検証スクリプトの初回実行では括弧、owner/revisionの引数差替え、出力field名に誤りがあり修正した。範囲外の未知親は全体例外ではなく依存案の保留となる既存契約に合わせ、bindingなし・範囲外親不変を確認した。`first-run-acceptance-results.json` / `second-run-acceptance-results.json` に途中結果を保持した。製品挙動や既存assertionを弱めて通過させたものではない。

## 4. API予算のオフライン検証

`ledger_check.py` は製品外の独立したSQLite台帳と実際の `ApiBudget` request/response hookを使用した。試験上限は2回、HTTPはMockTransportで、実OpenAI送信は0回。

最初の模擬送信で予約のcommitがtransportより先に行われ、応答でRESPONSEになった。次の模擬通信断はRESERVEDのまま回数を消費し、再起動後も2件を保持した。3回目は上限によりtransport前に停止し、別hostも送信前に拒否された。通常台帳は読み取りだけである。

これは既存の共有台帳hookの確認であり、実OpenAI接続、実HTTP障害、並行送信やPhase20の30回上限実装を受入済みにしない。Phase20は自身の固定scheduleと送信履歴上限も照合する必要がある。

## 5. 次回の送信条件を固定

`freeze_implementation.py` で新実装と固定designのhash、環境、30試行のrequest/snapshot/scope/pack/image digestを `implementation-manifest.json` に固定した。source/evidence hashは411件。実画像は `pack-images/`、再構築可能な送信内容は `frozen-requests/` に保存した。APIキーは含めない。

次の方式・回数・採点を維持する。

- GPT-6 LunaのF-low、P-low、P-none、5既知scope×2反復、最大30送信。
- 全方式で同じwrite poolとv5、出力上限24576、並列1、timeout60秒、retry/warmup/追加診断0。
- 方式ごとに重点分類146/146、結合74/74、行14/14、記号16/16、重点誤結合0。重点外の追加案も確認する。
- 品質を満たしたscope-ready中央値5秒以下、5秒以内8/10以上、各帳票最低1/2。全ページ基準は別に保持する。

`offline-preparation-timings.json` は今回のローカル準備時間だけを記録した。provider応答時間・実画面への到達時間・5秒達成の証拠ではない。

最終照合で411件のsource/evidence hashを再確認し、保存した画像と送信内容から30件のwire bodyを再構築して全request hashの一致を確認した。結果は `artifact-validation.json`。文書の相対参照2件、AGENTS/Safety Contractの既存本文を保持したことも確認した。

## 6. 保持監査と未検証範囲

repository変更は本書とAGENTS/Safety Contractの現在地追記だけ。新実装・使い捨て検証・画像・ログ・独立台帳はすべて製品外に置いた。staging/commit/pushはしていない。

主要実行は `audit_state.py before/after`、`setup_runtime.py`、`acceptance.py`、`replay_and_guard.py`、`ledger_check.py`、`freeze_implementation.py`、`git diff --check`。全件unit/E2E、frontend build、通常サーバー起動は実行していない。今回の境界に必要な製品外の短い検証と保存応答再生を実行した。

終了監査で両checkoutのHEAD、両DB各37表とschema、設定、863通常ファイル、781 artifact SHA、Phase11〜18の証拠は前後一致した。旧llm-ocrは変更なし、deployは文書2ファイルへの追記と本書の追加だけで、`git diff --check` は通過した。結果は `preservation.json` と `diff-check.log` に保存した。通常製品ソース、OCR provider/prompt、physical identity、mapping/crop、受付snapshot、補正revision、確定/出力実装は変更しておらず、**既存OCR contractを維持した。**

**API使用0回、累計336/496、残160回。** 次回30回は未使用。台帳上限と通常設定は変更していない。

未検証は、実endpointのv5 schema受理、新モデル応答の精度/速度、汎用scope planner、全ページcoverage完了、scope先行公開の実HTTP/DB/event/receipt/取消、保存・再取得、画面・実マイク、矩形生成、通常OCR/XLSX出力、製品全体回帰。製品導入や通常機能の有効化はしていない。

## 7. 次の区切り

フェーズ19は製品外実装・固定比較の準備として完了。次は**フェーズ20：GPT-6.1 Sol / High、固定条件で最大30回の実API測定**。開始時にmanifest、入力、台帳残数、環境を再照合し、schema不受理・scope/physical破綻・hash/台帳不整合は停止する。通常の意味誤り/timeoutは失敗として記録し、追加送信で救済しない。

フェーズ21はAstra / XHigh、原則API0で採否を判断する。各フェーズの区切りで指示を待ち、比較・製品接続へ自動進行しない。
