# AIテンプレートの実帳票入力で判明した制約

2026-10-13。継続ゴールは「GPT-6 LunaによるAIテンプレート作成を、実帳票で案の提示・修正・保存まで使える状態にする」。**未達です。現在の試作は実帳票の入力段階で候補を落とすため、そのまま製品へ接続できません。** 前回の候補30/30・T2内部483msは、固定した小範囲の検証結果として保持します。実帳票全体への適用性を示す結果ではありません。

開発対象は `C:/Users/hanam/Desktop/llm-ocr-deploy`。今回の資料・検証・設計境界は製品外 `D:/llm-ocr-archive/template-ai/20261013-template-real-forms` に保存しました。製品ソース・前回のprototype3・通常データは変更していません。前回のgoal turnは修正と検証が完了した「progress」、今回も再現証拠により次の修正対象を特定した「progress」です。

## 実帳票と確認範囲

- R01：[沖縄県の農薬使用状況記録簿](https://www.pref.okinawa.lg.jp/_res/projects/default_project/_page_/001/010/630/youshiki2.pdf)。繰り返し表と複数枠にまたがる見出し。
- R02：[衣浦東部広域連合の自主点検表](https://www.kinutoh.jp/shobo/yobo/shinsetodokede/documents/05jisyutenkenhyou.pdf)。日常・定期の2ページ。結合見出し・縦書き分類・表外の記号凡例。
- R03：[大阪府の旅行命令簿兼精算旅費内訳書](https://www.pref.osaka.lg.jp/documents/13886/3-2-320ryokoumeireibokenseisanryohiutiwakesyoyousiki_kinyurei.pdf)。1ページ目を対象とし、2ページ目の記入例は除外。配布様式の既定印字は保持。
- R04：[鈴鹿市の証明書交付申請書](https://www.city.suzuka.lg.jp/_res/projects/default_project/_page_/001/001/962/a1_3shokusinsei.pdf)。3種類の申請欄と共通情報・窓口処理欄。

公開原本4ファイルをSHA付きで保持し、Poppler 150dpiで全6ページを描画・目視確認しました。解析対象は5ページです。美作市の最初の候補は、PDFが記入要領・白紙がDOCであるため、モデル評価前に別の公式PDFへ変更しました。原本の消去・書換え・記入例を空欄化する加工は行っていません。

これら4系統の失敗を次の設計へ利用するため、`source-use.json` で**開発用の診断資料**に区分しました。修正版の未知資料4系統として数えません。意味の正解認定・独立審査・実モデル採点も未完了です。

## 再現できた問題

1. **通常deployのPython環境にはPaddleOCRがありません。** 現行`LayoutDetector`で5ページの枠を検出しても、全セルの`text`が空です。改変していないprototype3は全5ページを送信前に保留します。Lunaへ画像が届く前の問題です。
2. **文字認識の復旧だけでは不足します。** 旧checkoutの既設ライブラリだけを読み取り専用で利用し、deployのアプリコード・専用model cache・ネットワーク拒否で処理しました。全841セルのIDとgeometryは文字認識なしの結果と一致。通常deployへライブラリを導入した結果ではありません。
3. **読み取った行が枠を跨ぎます。** R01では「使用場所」と隣の見出しが一つのOCR領域となり、`LayoutDetector._text_is_in_cell`の中心点判定で片方だけへ付属します。`factor_plan.generate`は先頭行の全セルに文字がある条件を要求するため、実際に見出しが印刷されていても表全体の意味候補が0になります。
4. **構造と処理単位にも不足があります。** R02は印字があっても意味付け候補0、R03は79 componentとなり既存64上限で全体保留です。R04の候補は部分的です。PDFのnative文字を同じgeometryに割り当てた診断ではR01に表候補が生じますが、R02/R03は解消しません。これはPDF文字を製品入力へ正式採用した結果でも、正解の保証でもありません。

| 対象 | 検出セル | 文字のあるセル | ローカルOCR 秒 | 意味候補のあるcomponent | 送信経路候補 |
| --- | ---: | ---: | ---: | ---: | --- |
| R01 p1 | 200 | 8 | 31.49 | 0 | held |
| R02 p1 | 257 | 38 | 29.76 | 0 | decisions1 |
| R02 p2 | 54 | 31 | 40.52 | 0 | decisions1 |
| R03 p1 | 251 | 168 | 69.05 | 0 | held |
| R04 p1 | 79 | 75 | 66.95 | 4 | responses |

初期化は別に6.36秒です。各値は1回の観測で、中央値・p95ではありません。印字欄もセル数に含みます。「意味候補のあるcomponent」はfields/form/records候補を一つ以上持つ単位であり、正しい設定数・入力欄の解決数・モデル精度ではありません。R02のDecisions経路は見出し等の単独分類だけで、意味付けできたことを意味しません。

## 次に変更する境界

ローカルOCR文字列を解析成立の必須条件から外し、版所有画像と物理セルをLunaが判断できる入力へ改めます。確実に候補が作れる範囲のDecisionsは残し、構造・名称の自由な提案が必要な範囲はResponsesを使います。名称の提案と印字原本の書換えを分けます。

ページ全体を一つの上限判定で保留する処理を改めます。実際の表・見出し依存に基づいて作業単位を作り、検証が完了した案から提示します。単なる上限引上げ、配列順の機械分割、保留の強制適用は採用しません。全セルの処理済み・未解決範囲を保持します。

保存は引き続き「案を反映」→手動修正→「確認して保存」→再読込です。既存のmanual compiler・MutationCore・TemplateAuthoringServiceを使い、AI専用の保存経路を増やしません。対象外セル・物理対応・revision・同値の手動編集・取消・古い応答・全claimsの検査は維持します。

今回は不足の再現と変更境界の固定までです。新しい画像主体の提案経路、ページ分割、実API、実画面への接続はまだ実装・検証していません。詳細は `next-repair-boundary.json`。旧335項目の対応付けや34-F独立審査を済ませたという扱いにせず、旧受入ファイルを偽装して送信しません。旧35/36/37の18/24/28回と未割当9回は保持し、新比較へ黙って流用しません。

## 検証と保全

実行したのは `prepare_sources.py`、`inspect_sources.py`、`detect_baseline.py`、`check_text_dependency.py`、`detect_with_local_text.py`、`diagnose_input_layers.py` です。矩形841、native罫線とのIoU診断837/839、OCRあり/なしの物理対応841/841を確認。native抽出は装飾等を含む診断で、意味のgoldではありません。原本・描画・文字領域・入力・plan・各ログを保持しました。製品全体のテストは今回の変更範囲に含みません。

API使用・共有予約は0、台帳417/496、残79。製品OCR/provider、mapping/association/crop、受付snapshot、補正revision、確定・出力の実装は変更しておらず、既存OCR contractを維持しました。終了時の保全照合と差分検査は `preservation.json`、成果物の固定は `result-hashes.json` を正本とします。
