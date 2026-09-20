# 実スキャンのcrop端と追加余白の観測

2026-09-14。Calc保存プラン変更後のbackend全体試験の稼働中に、保存済み診断画像を
読み取り専用で調べた。製品コード・試験条件・元スキャン・版・DB・成果物は変更していない。
この文書だけを追加した。Office/PDF helper/OpenAI/Google呼出し0。

## 入力と観測

入力は `llmocr-scan-baseline-zrbb0l6u/report.json` と
`llmocr-scan-clearance-nhdhtqqv/report.json`（いずれもWindowsのTemp配下）。
後者の `paired-scan-observation-v1` は印刷近傍の暗画素をUNKNOWNとして保持する診断で、
本番の筆跡判定器ではない。151領域すべてにUNKNOWN画素があるが、151領域の不良を示さない。

第1ページの `layout_xlsx_Z6` の保存bboxは `[790,259,66,92]`。
`inspect_scan_clearance.observe` が報告した新暗画素4個を保存candidate画像で確認した。
全座標は `(790,296)`, `(790,297)`, `(790,298)`, `(790,299)`。
4個ともcropの左端にあり、既存padding 4pxを除いた領域の内側にはない。
保存cropとaligned全ページの目視では、左側の筆記に連なる断片と整合する。
ただし、目視での筆画所有権の解釈は機械的な認定証拠ではない。

保存configには隣接する `layout_xlsx_W6` と `layout_xlsx_Z6` が別mappingとして存在する。
見かけ上の筆記の連続や名称だけで結合・意味づけの修正をする根拠にはしない。
個人の記入内容は本記録へ転記しない。

- filled入力SHA: `c1786c4f220ec12a588b6640fad3ff28ab020b885d6f14e8f0cff49051c8f987`
- `page-1-c1786c4f220e/crop-5.png` SHA:
  `afec5fa9afa2ef5b61bbab5dc9df0de0a87761a3ce74b634e07695eb4e7a4742`
- `96153ac5-2c2b-5022-b7e3-fd09abe00af7-candidate.png` SHA:
  `7d0d636ad12a305d11cbb631c826043accc14bcf3df12ad2916b12892463253a`

## 実装への制約

crop端の候補画素だけでは、対象筆画の切断・隣接筆画の混入・scan noiseを区別できない。
この実例をPASSさせる目的で端の画素を捨てたり、paddingを増減してはいけない。
送信前検証には元領域とpaddingの区別、周辺内容との所有関係、局所位置合わせ誤差を
含む証拠が必要である。空白判定や全領域の安全性はこの4画素の観測から推定しない。
31件の近接候補を31件の切断不良とも、残る120件を安全とも判定しない。
本番のINK_CLEARANCE enforcementと有効化・実OCR E2Eは引き続き未完了。
