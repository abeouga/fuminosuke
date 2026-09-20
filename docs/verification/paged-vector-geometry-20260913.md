# 保存PDFとページ別OCR領域のベクトル診断

2026-09-13。PDFスキルによるread-only診断。本番の位置精度認定ではない。

## 対象と不変確認

- TemplateVersion: `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`（DRAFT）。
- 保存PDF: `backend/data/artifacts/7b90d415-98d5-44e4-971a-e477ded0fdab/print/faddf957091e43718320f96a71cc5c69/print.pdf`
- PDF SHA: `56bb73ba362702df0e251de465bffa1bd2cfe79e2c2b53201db85aeb78c35fd4`。
- 同じartifact内の全4 PNGを目視した。各SHAはrenderer reportと保存planのreferenceImage SHAに一致。
- DBはSQLite `mode=ro`と`PRAGMA query_only=ON`で開いた。PDF SHAとquality_jsonの前後一致を確認。
  印刷ファイル、plan、mapping、認定booleanを変更していない。

## 測定方法

Codex bundled Pythonの専用診断processでpdfplumberを使用した。backend内へPDF parseを追加していない。
PDFのstroked horizontal/vertical lineの中心座標を150/72でpixel単位へ変換し、保存済みfieldの
四辺と直接比較する。座標のfit・shift・修正は行わない。各辺の85%以上を覆う一意の線だけを採用し、
探索幅は既存8px以下かつfield幅/高さの1/4以下とした。未測定の辺を0pxとして扱わない。
PDFのclip用rectや非stroke pathを罫線の証拠に含めない。

対象は`bindings`を持つ151個のOCR field。保存plan内には見出し等も含む計285 fieldがあり、
これらをすべてOCR対象と数えることは誤りである。対象を位置や文字内容で推測していない。

| ページ | OCR field数 | 四辺測定済み | 四辺未測定 | 観測辺の最大差(px) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 60 | 53 | 7 | 1.5875 |
| 2 | 51 | 49 | 2 | 1.4484 |
| 3 | 37 | 35 | 2 | 1.3901 |
| 4 | 3 | 3 | 0 | 0.7958 |

全140 fieldで四辺が測定できた。部分測定も含め、観測できた辺に2px超の差はない。
第4ページの3 fieldはD94:AI94、D98:AI98、D102:AI102。
既存raster検査の5-anchor条件とは異なる観測であり、旧検査をPASSへ書き換えていない。

未測定11 fieldのsourceRange:

- page1: V5:Y5、AA5:AB5、AD5:AE5、W6:Y7、Z6:AA7、AC6:AD7、AF6:AG7。
- page2: Y40:AH40、Y41:AH41。
- page3: Y79:AH79、Y80:AH80。

これらは日付や括弧内の自由記入欄等で、一部の区切り線がPDFに存在しない。
独立測定できない境界を、近隣線の位置から正しいfield境界として推測しない。

各ページで測定済みfieldを1個だけメモリ上で横に3px/20px移動し、全8ケースで不一致を検出した。
DB・印刷PDF・保存座標は動かしていない。任意の複雑なPDFに対する検出率を証明した試験ではない。

## 次の判断と限界

「第4ページは3個しか枠がない」こと自体と、各OCR領域が実際にずれていることを区別できた。
直接field検証を新WSL geometryの候補証拠として検討できるが、次は未完了である。

- 未描画境界を持つ11 fieldの独立した位置証拠。
- stroke width、dash、clip/overpaint、glyph、raster結果を含む汎用の観測妥当性。
- 実手書きのcontent retention/neighbor exclusion、スキャンregistrationの誤差とcrop確認。
- 新WSL font/runtimeの資格検証と証拠失効、有効化・受付への正式接続。

したがって本診断をproductionQualifiedに変換せず、既存の本番gateと閾値を維持する。
font側では太字/斜体を含む要求属性の記録が必要である。
Fontconfigの一致候補は要求と同一のfontを保証せず、実Officeの採用font証拠とは別物である。
[Fontconfig公式仕様](https://fontconfig.pages.freedesktop.org/fontconfig/fontconfig-user.html)も
family・weight・slant等を別属性として扱い、family内の区切り文字のescapeを定義している。

実OpenAI/Google API呼出し0回。認識精度・実association誤対応は未測定。
productコードを変更しておらず、OCR contractの変更もない。
この診断中もbackend全体回帰は既存processで継続し、Office変換を重複実行していない。

再現script: `C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/inspect_vectors.py`。
実行: Codex bundled `dependencies/python/python.exe`へ同scriptを渡す。
結果はJSON stdoutのみで、PDFやDBへ書き込まない。中間PDF/PNGは作成していない。
