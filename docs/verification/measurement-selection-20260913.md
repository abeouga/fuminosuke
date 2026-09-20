# 測定コピー対象の限定 — 2026-09-13

## 状態

後続の実描画で、この対象拡張方式は本番採用しないと判断した。
43項目への追加線は文字program差異と新たな未観測segmentを生じた。
一時的に試した連続fill色設定の正規化も取り消し、既存の厳密な比較を維持した。
下記は候補方式を試した時点の記録であり、採用済み実装の説明ではない。
現在の限定修正と残課題は[欄別描画寄与](field-raster-scope-20260913.md)を参照する。

テンプレート有効化は未完了。これは測定対象の選択処理と診断CLIの追加であり、
既存の資格APIへはまだ接続していない。本番の拒否条件・許容値を変更していない。

先行の[差分描画検証](stroke-differential-20260913.md)では、細点線のうち罫線との
交点で一つの描画命令を消しても画素差が出ないことを確認した。独立した線の観測条件を
緩める修正は行わず、別の測定コピーで座標を観測する候補を準備した。

## 変更境界

- `reference_measurement_selection.py`: 明示field ID / sourceRange / sideを保持し、
  未観測の辺、または `NO_INTERIOR_PAINT_SAMPLES` の辺だけを測定候補にする。
  一つの辺を構成する他のsegmentに実測した欠落・部分欠落がある場合は候補にしない。
  clip、未対応path/cap、曖昧な座標、証拠欠落も新たな測定による救済対象にしない。
- `inspect_measurement_selection.py`: 既存DBをSQLite read-only/query-onlyで読み、
  原本ではなく保存印刷XLSXのコピーを明示的な `--render` 指定で測定する診断。
  defaultは読み取り専用。入力SHA・現在のhelper実装・全ページbindingを検証する。
  native処理は固定WSL rendererと機械単位mutexを使う。実DB更新・資格発行はしない。
- 原本、保存済み印刷ファイル、意味づけ、座標、mapping、OCR値・補正・確定は変更しない。

Spreadsheetsスキルの標準APIについて、未変更OOXML部品のbyte保持保証を確認できなかったため、
既存のbyte保持専用コピー処理を使用する。対象外の部品・旧スタイル・セル値・寸法は保持する。
PDFの解析はbackend本体で行わず、既存WSL helperに限定する。

## 実帳票の読み取り専用観測

対象は版 `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` の検証用保存先。
ライブDBではない。保存plan SHA:
`7595cf111b328b1e4433075f6f612d91a73a22f154200cd211b39d4d64a1f51a`。

使用PDF SHAは `ffa3992302a1d765bdad9b210abf66c8311b149e43d415bcdf3a6236e2be522d`。
差分描画報告の `llmocr-stroke-differential-z5io1dwm/vectors.json` をその入力PDFと
現在のhelper実装に照合し、保存済みPNGから観測を再計算した。

| ページ | 項目数 | 元の未観測辺 | 元のraster未検証segment |
| --- | ---: | ---: | ---: |
| 1 | 60 | 16 | 1 |
| 2 | 51 | 4 | 9 |
| 3 | 37 | 4 | 0 |
| 4 | 3 | 0 | 0 |

測定対象候補は43項目。名称や固定座標からの選択ではない。
実行前後で入力ファイルのbytesと版status/quality JSONは一致した。
この読み取り専用実行ではXLSXコピー生成・Office変換を行っていない。

## 対象検証

```powershell
# backend
.venv/Scripts/python.exe -m pytest tests/unit/test_measurement_selection_diagnostic.py tests/unit/test_reference_measurement_selection.py -q -o addopts='' -p no:cacheprovider --basetemp=.pt-measurement-selection-final-0913
.venv/Scripts/python.exe -m ruff check app/services/reference_measurement_selection.py scripts/inspect_measurement_selection.py tests/unit/test_reference_measurement_selection.py tests/unit/test_measurement_selection_diagnostic.py
.venv/Scripts/python.exe -m mypy --follow-imports=silent app/services/reference_measurement_selection.py scripts/inspect_measurement_selection.py
git diff --check
```

- 対象unit **26 passed（0.26秒）**。選択境界、6種類の元罫線スタイル保持、
  defaultの非描画・非更新、workbook/image/PDF/helper改変拒否を確認した。
- 初回のCLIテストは25 passed / 1 failed。helperの拒否は実際にはRuntimeErrorであり、
  テストがValueErrorを期待していた。helper改変時の例外型とmessageを明示して修正した。
  改変拒否そのものを除外・skipしていない。
- 4ファイルのRuff、2モジュールの限定mypy、差分whitespace検査は成功。
  通常のmypyの依存追跡では既存print_layout_sourceのopenpyxl stub欠落3件が出る。
  限定検査の成功をbackend全体の型検査成功とは扱わない。

先行backend full `.pt-stroke-differential-full-0913` はこの追加前に収集済みで実行中。
この26件や測定対象の新方式を含む全体回帰成功とは扱わない。
共有rendererの競合を避け、全体回帰終了後に実WSLで測定コピーを描画して比較する。
文字プログラム・font resource・ページgeometry・測定線近傍以外の画素不変と、
全fieldの測定結果を確認するまでは本番APIへ接続しない。

実OpenAIは作業全体 **0/100回**、Google 0回。本番OCR・ACTIVE・実取得の成功は未検証。
既存OCR contractを維持した。今回の追加コードは本番経路から未参照である。

## 実測後の判断

- 元の「未観測辺のみ」の測定コピーは維持した。選択対象を43項目に拡張する
  `reference_measurement_selection.py` / CLIは診断専用のままで、本番から参照しない。
- 拡張コピーXLSX SHAは `383ea68646471a9f25320c21fa49e787a8c8079bbccb4636952165a18025f435`。
  最初の実描画はページ2の保持text program命令数が1374から1373へ変わり、厳密照合で拒否した。
  続く正規化実験でもページ1に未検証segment 7/18が残り、採用しなかった。
- 正規化の仮実装5行とその実験用12テストは取り消した。既存の比較・拒否テストは削除していない。
  対象拡張を期待した自作テストも、採用しない仮説を残さず既存選択範囲の固定へ変更した。
- 先行全体回帰は1810 passed / 1 skipped / 3 warningsで終了した。
  対象選択の限定を追加した時点のunitは34 passed。後続の欄別観測とは検証範囲を区別する。
