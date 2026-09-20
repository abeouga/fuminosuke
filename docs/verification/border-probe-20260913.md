# 未描画境界の測定用コピー検証

## 結論

保存した実4ページの151 OCR欄について、従来未観測だった11欄を含む全四辺を
測定用コピー上で観測できた。元の文字描画プログラム・フォント・文字幅・ページ座標系は
比較で一致し、追加線近傍以外のraster差分は0画素だった。

これは未描画境界を測定する方式の実証であり、資格発行・ACTIVE化ではない。
本番adapter/APIへの接続、別帳票での適用条件、正式なgeometry閾値、renderer環境資格、
実入力のregistration/ink clearance、本番OCRから確定・出力までのE2Eは残る。
閾値を下げたり、5-anchor判定を無条件成功に置換した結果ではない。

## 原因と変更境界

旧raster verifierは四辺に印刷線のある5セルを要求する。
実page 4は大きな記入欄が3個しかなく、他ページには元々四辺を印刷しない欄がある。
位置ずれと、観測対象の線がないことを区別する必要がある。

- `backend/scripts/inspect_border_probe.py` を追加。実DBはSQLite `mode=ro` / `query_only`。
  保存印刷XLSXと全参照artifactのSHAを検査し、固定WSL環境で基準を再描画する。
  全ページPNGが保存SHAと一致してから、既存vector検証でUNOBSERVEDだった辺を選ぶ。
  選択は保存 `cellId` / `sourceRange` のみ。帳票名・項目名・OCR文字・近傍セル推定は使わない。
- 別の測定用XLSXコピーでは、既存の明示style付きセルだけに新しいstyle参照を設定する。
  既存borderとxfを複製して測定辺を赤いhair線へ変更し、既存styleの定義は変更しない。
  選択範囲のセルが存在しない、styleを推定する必要がある等の場合は診断を拒否する。
  値・行列寸法・結合・名前・用紙・倍率・他のZIP部品を再保存しない。
- 実コピーの変更は30セルのstyle参照と17組の追加style/border。
  `xl/styles.xml` と選択worksheet以外のパートは保持する。原本や保存印刷XLSXを更新しない。
- 元/コピーを同じruntime/font集合/workerで描画する。PDF/font parseは専用隔離helper内。
  Windows/backend内へPDF parserを戻さない。診断物はOSの新しい一時ディレクトリへ保存する。
- `renderer_vector_pdf.py` に文字プログラム比較用の記録を追加した。
  `renderer_vector_bridge.py` は新しい記録が存在する場合、そのpolicy/SHA/countを検査する。
  旧記録の欠落を補完せず、新診断では記録の存在を要求する。
  通常glyph helper、資格API、承認/受付gate、OCR providerを変更していない。

### 文字プログラムの比較

文字数やUnicode列の一致だけでは文字位置の一致にならない。
文字位置・文字間隔・font選択を含む全text命令、CTM、fill state、fill/clipの全pathを、
元の命令順でcanonical化する。font resourceは埋込bytes・ToUnicode・PDF Widths等をhashする。
page box、rotation、UserUnitも別途一致を要求する。

純粋なstrokeのpath/styleだけを比較から除く。fillまたはclipを伴うpathは除かない。
pathを後でまとめて出力してCTMの前後関係を変えず、元のoperation index順で記録する。
純strokeを除いた後に内容が空になる `q ... Q` の組だけを除く。
text/fill/clip/transformが残るscopeを消したり、異なる座標を丸めて一致させたりしない。
strokeによる上塗りや最終可視性の完全な証明ではなく、`visibilityVerified=false` を維持する。

初回の実比較はclip pathを未対応とし、次の比較でもq/Qの追加による差を検出した。
命令別の件数/operand hashを調べた結果、差はpage 1で12組、page 2/3で各2組のq/Qだった。
空scopeだけの除外を別の失敗再現テストで固定してから、同じ保存PDFを再検査した。
手法の変更前に得た不一致reportは保存し、成功結果で上書きしていない。

## 実4ページの結果

| page | OCR欄 / 四辺観測 | 最大差 px | 測定線近傍以外の差分 | 文字プログラム一致 |
| --- | --- | --- | --- | --- |
| 1 | 60 / 60 | 1.65625 | 0画素 | 一致 |
| 2 | 51 / 51 | 1.44841 | 0画素 | 一致 |
| 3 | 37 / 37 | 1.39008 | 0画素 | 一致 |
| 4 | 3 / 3 | 0.79583 | 0画素 | 一致 |

線近傍とは赤画素maskの各方向2画素の拡張であり、本番cropのpaddingではない。
この領域には既存描画も150/46/56/0画素含まれるため、raster比較だけでは
文字配置の保持を証明しない。上記の別の命令比較を行った理由である。
測定したコピーの全4PNGを目視した。コピー再生成後のPNG bytesも目視済み出力と一致。
PDF・Spreadsheetスキルの元ファイル保持と再描画確認手順を診断へ適用したが、
未編集OOXML保持というプロジェクト契約を優先し、通常のworkbook再保存はしていない。

検査版: `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`、DRAFT。
保存印刷XLSX SHA: `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`。
測定コピー SHA: `f9420233a4b56b32836cfd185cbeaea39a32d0ad90028fb65396c143c3aa8bfc`。

最終測定入力: `C:/Users/hanam/AppData/Local/Temp/llmocr-border-probe-27etx0v5`。
report SHA: `4c3449686b7abf4d08c4e1764c93833cedd18f7e394cda50f53e4572927897dc`。
最終文字比較: `C:/Users/hanam/AppData/Local/Temp/llmocr-border-text-ej2nj08l/report.json`。
report SHA: `edf56aaf8e0119c9f22f91f20ee7a43d65c38b1d52e72a015c9a89d9eef5a01d`。
vector bundle SHA: `77bdc694b0065564df4bdfb1f7065f2311458fe688ac22fab68dbd76520642ab`。
同じ原/コピーPDFを入力SHAで照合し、最後の文字検査ではOffice変換を再実行していない。

## テストと未検証範囲

- 変更前OCR contract: 210 passed / 1 warning / 37.90秒。
- 最終変更後OCR contract: 210 passed / 1 warning / 38.05秒。
- 新しいコピー保持・拒否・raster変位/重なり検査: 9 passed。
- 最終vector/コピー関連unit: 185 passed / 0.92秒。
  文字座標、Tc/Tw/Tz/TL/Ts、TJの数値間隔、font widths、clip geometry/winding、
  fill/combined paint、CTM順序、空q/Qの限定除外、report SHA/countを含む。
- 途中の実WSL helper回帰: 2 passed / 57.76秒。
  最終文字比較の拡張後は別途、実4ページの保存PDF2個を固定helperで再検査し、全ページ一致。
- 対象7ファイルのRuffと、source 3ファイル限定mypyは成功。
- `git diff --check` 成功。frontend変更・新規UI検証・実API送信はない。
- 全backend回帰: **1602 passed, 1 skipped, 3 warnings / 1947.34秒**。
  新しい36件を含む同一実行の完走を確認した。スキップと警告は既存のもの。

全体検証は `RUN_OPENAI_INTEGRATION_TESTS=false` とし、backendで
`.venv/Scripts/python.exe -m pytest -q --tb=short -o addopts='' -p no:cacheprovider
--basetemp=.pt-border-probe-full-0913` を起動し、2026-09-13 19:20頃に終了コード0を確認した。
同じ実行を継続して確認し、観測timeoutだけを理由に再起動していない。
実行中に別途追加した起動依存の9件と、その完走後のroot起動修正は本結果に含まない。

実OpenAIは作業全体 **0/100回**、Google 0回。認識正解率・実responseのassociation mismatch・
schema errorは未測定。原本・スキャン・実DB・保存成果物・過去jobを変更していない。
既存OCR contractを維持した。全体目標は未完了であり、OCR安全性を完全には検証できていない。
