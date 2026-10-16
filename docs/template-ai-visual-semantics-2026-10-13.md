# 画像による意味付け案と共通保存の修正

2026-10-13。継続ゴールは「GPT-6 LunaによるAIテンプレート作成を実帳票での提示、修正、保存まで使える状態にする」です。**記号欄・チェックリスト・見出し名の提案契約と、共通保存の2不具合を修正しました。実画面・Lunaへの接続は未完了で、ゴールはactiveです。** この継続は再現・実装・保存検証を進めたprogressです。

対象checkoutは `C:/Users/hanam/Desktop/llm-ocr-deploy`。通常DBではなく、`D:/llm-ocr-archive/template-ai/20261013-visual-semantics` の専用DBで検証しました。実APIと共有予約は0、累計417/496・残79。将来の18/24/28回と未割当9回の計画配分は変更していません。

## 原因と修正範囲

画像入力だけでは、旧graph.v1に記号・チェックリストの選択肢を持たせられません。`visual_contracts.py` と `visual_graph.py` を追加し、`visual_compile.py` から明示的な `template-analysis.visual.v2` を処理します。旧v1は既存再生用に保持し、v2の失敗時に旧版へ切り替えません。

モデルが返せるのは発行済みセル参照、意味名、表の行・列、見出し、記号の種類と選択肢です。座標、保存先、DB IDを生成させません。記号は単一選択、チェックリストは複数選択とし、選択肢領域はセル全体に限定します。凡例の引用はモデルが主張した根拠であり、原文一致を検証済みとは扱いません。名前は編集用metadataに設定し、元の印字を上書きしません。

記号を繰り返し表の行へ組み込む際は、物理セルごとの意味と保存先を作ってから表へ接続します。複数行の記号が同じ保存先を共有しないようにします。未知参照、重複所有、設定のないマークへの接続、ロール不一致、空・重複選択肢を拒否します。追加した見出し・マークの読み書き依存を統合し、同値手動編集を含む保護対象に接する案は依存単位ごと保留します。paged symbolは未対応として拒否します。

共通の `manual_compiler.py` で、次の製品側不具合を再現しました。

1. 新しいwhole-cell markを下書きから保存すると、意味設定を事前に差し込む処理が先行し、その前に必要なセル分類変更を拒否しました。whole-cell設定には座標の事前登録が不要なので、この差込みだけを外しました。既存の領域型設定の処理は維持します。
2. markのrelationを保存用操作へ変換する際、入力欄以外を一律に省略していました。再現記録では提案の3接続が0になりました。markも変換し、`commands.py` のmeaning.bindで、既存markSetが同じ保存先をただ1つ持つ場合のみ接続を認めます。別の意味への暗黙の付け替えは拒否します。

この2ファイルは通常の共通編集も使うため、未接続の新モジュールだけの変更とは区別します。DB schema移行、provider、prompt、通常の解析runner、frontend、通常OCR、再読取・確定・XLSX出力の実装は変更していません。既存の未コミット差分は保持しています。

## 検証結果

| 検証 | 結果と範囲 |
| --- | --- |
| `validate.py` | 36条件通過。記号、複数選択、表、見出し、所有権、拒否・保護境界。既知R02の4項目を制御した提案として処理し、残り46セルは未解決のまま保持 |
| `integration.py` | 46条件通過。現在のASGI API、EditingRuntime、MutationCore、TemplateAuthoringServiceと実SQLite保存。記号・表・チェックリストの保存、手動見出し修正、revision 1→2→3、古いrevision拒否、新DB接続での再読込 |
| `verify_shared.py` | 13条件通過。同じmark意味の接続とID保持、別意味・未設定markの拒否、保存済み3形態と既存automatic markの無変更往復 |
| 既存入力検証の再実行 | 74条件通過。封印済み検証を別ディレクトリへコピーし、既知5ページ841セル・17範囲と文字入力の境界を現在実装で再確認 |
| 静的検査 | 変更5ファイルのruff/mypy通過。`run_checks.py` とログに実コマンドを保存 |

主な再実行入口はbackendの既存venvによる `python -B D:/llm-ocr-archive/template-ai/20261013-visual-semantics/integration.py` と `run_checks.py` です。ASGIはアプリ内通信であり、ブラウザ・TCPサーバー起動・実APIの検証ではありません。専用DBのschemaはmetadataで作成したfixtureで、migration検証を兼ねません。ネットワークは禁止し、解釈モデルを使わず保存用下書きを制御しました。

初期失敗も保存しています。R02の5行目を含めたfixture選択、非同期loop初期化前の通信禁止、古いrevisionを非同期受付後に拒否すると仮定した検証、存在しないrecognitionMode指定は、検証側の誤りでした。実APIは古いrevisionをHTTP 409で受け付けず、正本を維持することを確認しました。製品不具合の修正前記録は `validation-first.log` と `manual-before-loss.json` です。

## 最終評価用の公開原本4種類

ユーザーの「自治体などが公開する実帳票4種類を選定する」という回答に従い、以下を取得・目視確認して固定しました。既知R01〜R04は開発用のままとし、今回の4種類へ混ぜません。

| ID | 公開元・帳票 | 対象PDFページ | 構造の被覆 |
| --- | --- | --- | --- |
| H01 | [豊見城市・検食の記録（様式18）](https://www.city.tomigusuku.lg.jp/material/files/group/32/03siyousyoyousiki_kyuusyoku.pdf) | 22 / 26 | 階層見出し、反復する選択肢、自由記入欄 |
| H02 | [大和郡山市・運転日誌](https://www.city.yamatokoriyama.lg.jp/material/files/group/65/untennisshi.pdf) | 1 / 1 | 横長表、二段見出し、行別チェック |
| H03 | [宮崎県・物品借用申請書](https://www.pref.miyazaki.lg.jp/documents/55150/55150_20231116151455-1.pdf) | 1 / 1 | 単独項目、結合領域、固定印字と記入欄の区別 |
| H04 | [東京家庭裁判所・現金出納帳](https://www.courts.go.jp/tokyo-f/vc-files/tokyo-f/file/1301miseinenkoukennin_qa1.pdf) | 16 / 22（紙面12） | 多数行、実線・破線、独立した手元保管金欄 |

原本PDFは全ページ保持し、書換え・文字置換はしていません。H03の既定印字も原本のままです。奈良市の候補は404、福岡市の候補は403で取得できなかったため、モデル実行前に差し替え、理由とURLを記録しました。検索結果の存在だけを取得成功とは扱っていません。

保存先は `D:/llm-ocr-archive/template-ai/20261013-final-holdout`。`manifest.json` に出典、原本SHA、対象ページ、除外ページ、画像SHA、開発用資料との分離を記録し、`source-seal.json` で固定しました。矩形検出・Luna推論・採点は0です。これは原本選定の完了であり、独立した正解定義や最終評価の合格ではありません。以後これらを調整へ使った場合は未知評価から退役させます。モデル結果を見て成功しやすい帳票へ交換しません。

## 残る作業と制約

新しい画像契約を解析runner・provider・画面へ接続し、予約済み予算、revision、同値手動編集、中止、公開時の再検査を統合する必要があります。未保存の新しい意味IDを含む下書きは共通手動変換でIDが変わる場合があり、現在のadapterは再bindingを要求します。複雑な見出し依存・全markモード・pagedは今回の保存検証だけで認定しません。

旧保護回帰の全移植、独立審査、Lunaの実API比較、最終4種類の独立goldと採点、Chrome上の提示→修正→保存→再読込、実応答5秒条件が未完了です。同一agentの検証を独立審査とは呼びません。Phase34-Fの受入を偽装せず、将来の固定比較結果を今回の制御fixtureの結果で代用しません。

保存経路の検証対象では物理ID・領域・印字と、保存後のmark/option/relation ID、3本の別targetPath、表の行対応を維持しました。通常OCRと通常データを改変せず、既存OCR contractを維持した範囲はこの変更・検証境界に限られます。OCR安全性全体を今回だけで完全検証したとは扱いません。終了時の差分・DB全表・API台帳・過去証拠との照合は `preservation.json` と `result-hashes.json` を参照します。staging、commit、push、サーバーの起動・停止は行っていません。
