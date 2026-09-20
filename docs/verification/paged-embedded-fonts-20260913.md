# 実4ページPDFの埋め込み字形照合

## 結論と対象

前工程のFontconfig要求候補に加え、実PDFが持つ埋め込みTrueTypeの全字形について、
実Windows font faceとの輪郭・送り幅・hint program一致を確認した。
合成字形も分解して比較した。これは当該PDFの埋め込みfontに関する証拠であり、
テンプレートの全文字対応・runtime隔離・crop・production qualificationを認定したものではない。

- 版: `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`、DRAFTの保存成果物。
- PDF: `backend/data/artifacts/7b90d415-98d5-44e4-971a-e477ded0fdab/print/faddf957091e43718320f96a71cc5c69/print.pdf`
- PDF SHA: `56bb73ba362702df0e251de465bffa1bd2cfe79e2c2b53201db85aeb78c35fd4`。
- 全4ページのFont resourceを列挙。4個の同じ埋め込みstreamを全ページが参照している。
  各ページで4font全てが実際に描画命令から使用されることまで意味しない。
- 対象ページのXObjectは0。ネストしたresourcesを無視して成功にせず、存在すれば診断を停止する。

## 方法

PDFをpypdfのstrict読取で開き、FontFile2の内容をmemory内で取得した。
候補は`C:/Windows/Fonts`のTTF/OTF/TTCを読み、name tableのPostScript nameと照合した。
単にPDFのfont名と候補名が一致するだけでは、字形一致とは扱わない。

fontToolsの[TTFont](https://fonttools.readthedocs.io/en/latest/ttLib/ttFont.html)と
[DecomposingRecordingPen](https://fonttools.readthedocs.io/en/latest/pens/recordingPen.html)を使用。
合成参照を輪郭へ展開し、unitsPerEmで正規化した全座標・操作順・advanceを丸めず比較した。
glyph名/GIDがsubset化で変わるため、文字名を同一性の根拠にせずsignature集合と比較した。
欠落componentは省略せず例外とする。さらに各glyphのhint programと、globalの
`cvt ` / `fpgm` / `prep` tableの内容SHAを比較した。

候補font全体のSHAとface indexを記録し、比較後に読んだfile bytesが同一であることも確認した。
PDFも前後bytes一致。抽出fontを書き出さず、原本・PDF・DB・保存reportを更新していない。

## 実測結果

| PDF subset | glyph entries（空字形含む） | 合成字形 | 一致候補 | 輪郭・advance・glyph hint一致 |
| --- | ---: | ---: | --- | ---: |
| BAAAAA+MeiryoUI | 273 | 21 | meiryo.ttc / face 2 | 273 |
| CAAAAA+MeiryoUI-Bold | 142 | 12 | meiryob.ttc / face 2 | 142 |
| DAAAAA+MeiryoUI | 278 | 23 | meiryo.ttc / face 2 | 278 |
| EAAAAA+MeiryoUI | 2 | 0 | meiryo.ttc / face 2 | 2 |

合計695はsubset内glyph entry数の和であり、独立したUnicode文字数やOCR項目数ではない。
合成字形56件を含み、一致しないglyphは0。global hint tablesも4subset全てで候補と一致した。

候補全体SHA:

- meiryo.ttc: `457b306f719a002f1d037b2a3942f2abfa8a55d7bb109bf8950341a3129adb46`
- meiryob.ttc: `7d54f5a6a4202dae7b54d5a854770cb4fecd33f8975c0996e89c6ce31f4b0808`

埋め込みstream SHA（表と同順）:

- `339152e5f3ccd8a93dccd7ff41632678539c2cc69f87004ddb94738a998fd6a3`
- `486c895798d09d374d46f3916c2194bf95d50642b6103e010e49a2db4a0c3b43`
- `a46e21e97a0e0a8cccd2507cec87402ee59bd3a6ded0fb651033dc026abfb136`
- `ddb2bc0b1810e264d8aecc4499ad66f92e56ea5915cfb87602bd324b234d61a2`

各subsetで、memory上の1字形だけのadvanceを変更するケースと、1輪郭点のXを1 font unit
変更するケースを独立実施した。4subset×2条件の不一致を検出するassertionを追加した。
実PDFやsystem fontに書き戻していない。改変を合わせるための丸め・許容幅を設けていない。

## 実行環境・限界

診断用fontTools 4.65.0を`--no-deps --only-binary=:all: --target`で
`C:/Users/hanam/.codex/tmp/paged-font-verification-deps-20260913`だけへ導入した。
backendの依存一覧/venv、WSLのOffice/font/runtimeは変更していない。
backend venvのPython 3.12から当該外部moduleを明示参照した。

再現script（固定の実データ診断。production helperではない）:

- `C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/inspect_pdf_fonts.py`
- `C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/compare_pdf_glyphs.py`

候補一覧の`mstmc.ttf`はSFNTとして読めず、その名前を報告したうえで候補から除外した。
したがって全Windows font形式の網羅性を主張しない。fontToolsの古いmodified timestamp警告も
記録され、timestampは一致判定に利用していない。

未検証の事項:

- PDF文字code/Encoding/ToUnicodeと元XLSX文字・glyphの対応、描画命令からの使用範囲。
- CFF、variable font、未知PDF resource構造、巨大/循環/悪意あるfontの安全な汎用処理。
- 他帳票の字形・theme/fallback、更新後のfontやrendererに対する失効・再認定。
- WSL専用helper内への検証処理の収容、runtime全体の固定、正式な資格発行とAPI接続。
- 独立geometry/crop安全性、実手書き・スキャン・実OCR。

現`ReferenceQualificationService`はWindows版`render_office`とそのenvironment identityに依存する。
WSLの新modelを認定する分岐・issuerはまだない。既存のWindows用font検証もpage 1の
simple outlinesだけであり、この診断の合成字形・全ページ証拠へ自動置換されてはいない。
次は検証済み範囲を隔離WSLの正式な証拠経路へ接続する必要がある。
単に保存booleanやACTIVE/受付gateを解除して代替しない。

今回はread-only診断と本記録だけで、製品コード・既存OCR contractの変更なし。
OpenAI/Google API呼出し0回、実association/recognition精度は未測定。
製品回帰試験を再実行したとは扱わず、前回の終了待機の未解決失敗も残る。

## 後続: 実際の文字コードからUnicode・字形への照合

同じ保存PDFを再serialize/re-exportせず、全4ページのcontent streamを読んだ。
`Tf`と`q/Q`でfont状態を追跡し、`Tj/TJ`の文字列を元byteへ戻して使用codeを数えた。
`BT/ET`とstackの整合性を検査し、未対応のtext operatorやXObjectがあれば停止する。

対象の4fontは全てTrueType、Symbolic flag=4、Encoding指定なしで、埋め込みcmapは
platform 1 / encoding 0だけだった。[PDF 32000-1:2008 §9.6.6.4](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/PDF32000_2008.pdf)
のこの形式の規則に従い、文字列の各byteから直接glyphを解決する。
ToUnicodeは単一byte codespaceとbfcharだけを受理し、rangeや複数Unicodeへのshapingは
未対応として拒否する。内容を抽出できたという理由だけで字形一致とは判定しない。

前工程で特定した2つの元font faceのSHA/nameを照合したうえで、各使用codeについて、
ToUnicodeが示すUnicodeから元fontのcmapを引いた字形と、埋め込みcmapが引いた字形の
輪郭・送り幅を直接比較した。合成字形を展開し、前工程と同じ丸めなしsignatureを使用する。

| ページ | 表示glyphの延べ出現数 | 当該ページのfont object＋byteの組数 | 実際に使用したfont数 |
| --- | ---: | ---: | ---: |
| 1 | 782 | 298 | 3 |
| 2 | 1023 | 318 | 3 |
| 3 | 1160 | 342 | 3 |
| 4 | 550 | 245 | 4 |

全体で延べ3515 glyph、重複を除くfont object＋byteの組は640。
fontごとの使用codeは255 / 129 / 255 / 1で、全てのToUnicode宣言codeが実際に使用されていた。
640組の全てで元Unicodeに対応した輪郭・送り幅が一致した。空白も除外していない。
この640は異なるUnicode文字数でもOCR入力項目数でもない。

各fontの使用codeを1つ選び、ToUnicodeの意味だけを変えるケースと、embedded cmapの
glyph参照先だけを変えるケースをmemory内に作り、同じ照合関数へ戻して実行した。
4font×2条件の全8件で、その変更codeだけに不一致が発生した。
PDF/system fontへ書き戻さず、両者の前後SHAも一致した。

診断scriptはrepo外の以下2つ。backend/WSLの製品コード・依存・認定artifact・DBに変更なし。

- `C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/inspect_pdf_character_maps.py`
- `C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/verify_pdf_character_glyphs.py`

PDFスキルに従ったread-only分析である。前節の未検証項目のうち、このPDFの使用命令と
byte→embedded glyph→ToUnicode→元font glyph対応は今回の証拠で補った。
元XLSXの全内容が欠落なく正しい場所へ配置されたこと、PDF Widths/配置/clip、任意帳票、
未対応font形式、隔離helperへの実装、実手書きcrop/本番資格はなお別の条件である。
画像を再生成して差を隠すことや、production gateの変更は行っていない。
並行中の`.pt-wsl-font-directory-full-0913`全体回帰は別の検証で、同一processの稼働を確認した。
この読取診断に伴うOpenAI/Google呼出しは0回。
