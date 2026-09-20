# WSL PDFの字形・Unicode対応検証

## 現在の実装境界

`renderer_glyph_fonts.py`と`renderer_glyph_pdf.py`に、隔離helperで使う検証ロジックを追加した。
旧Windowsの`renderer_font_evidence.py`を変更せず、個別の日本語帳票名・項目名・
フォントファイル名・特定font face名による許可リストを使用しない。

現在は `renderer_glyph_worker.py` の隔離 zipapp と `renderer_glyph_bridge.py` を通じ、
固定環境を記録した版の明示 qualification API に接続している。
`prepared_reference_glyphs.py` が保存済み PDF の検証・成果物所有関係・GET 再検査を担当する。
未信頼 PDF/font の解析は backend 内で行わない。
実ユーザー DRAFT、保存 PDF、意味づけ、mapping/crop、ACTIVE/受付 gate は変更していない。
合成 fixture の Windows unit、実 WSL helper、実帳票の read-only 検証と正式な環境認定を区別する。

## フォント対応の検査

`SourceFace`は呼出し側が固定したfont bytes・SHA-256・collection indexを受け取る。
SHA不一致をparse前に拒否し、TTC内の明示面番号を使う。通常TTFに非ゼロの面番号を渡しても
ライブラリが黙って無視しないよう、呼出し前に拒否する。
同一SHA/面の重複だけをまとめ、同名の異なるfont実体・面は曖昧として拒否する。
sourceのPostScript nameはfontのname tableから取得し、PDFのsubset名と一致させる。
同一 SHA / collection index の face が持つ明示 name ID 6 の別名は保持する。
異なる実体の同名候補は拒否し、部分一致やファイル名から face を推定しない。
sourceの複数Unicode cmapの対応が矛盾する場合も拒否する。

対応対象はTrueType `glyf`の単純/複合字形。variable、CFF、color font等は未対応。
PDFの6大文字subset prefixとsource face名、single-byte symbolic cmap (1,0)の対応を要求する。
実際に文字命令から使用された全コードについて、ToUnicodeのUnicode scalarから
source cmapが指す字形と、embedded cmapが指す字形を比較する。

- unitsPerEm、輪郭、hmtxの幅/side bearingを一致させる。
- 複合字形は再帰展開し、循環・欠落componentを拒否する。
- 字形とcomponentのhint命令、global fpgm/prep/cvtの相違を拒否する。
- font source identity、embedded bytes、ToUnicode bytes、検査済みコード集合のSHAを返す。
- 非表示化・clip・文字位置・元Excelセルの文字列一致の証明は含まない。

実装APIの根拠は[TTFont](https://fonttools.readthedocs.io/en/latest/ttLib/ttFont.html)と
[DecomposingRecordingPen](https://fonttools.readthedocs.io/en/latest/pens/recordingPen.html)。
missing componentを省略せず、指定版4.65.0を使用する。

## ToUnicodeと全ページの調査範囲

固定の宣言形式とsingle-byte code space、1 groupにつき最大100件のbfcharを解釈する。
UTF-16BEの単一scalarはBMP/補助平面の双方に対応する。
宣言件数の不一致、重複code、単独surrogate、複数scalarのshaping、親CMap、
bfrange、未対応programを部分的に読み飛ばして通過させない。
任意のPostScriptを評価せず、header/footerを含む全体が対応文法に一致する必要がある。
現在は既存LO経路で観測した形式の検査を実装した段階であり、全PDF形式のvalidatorではない。

PDFの全ページとindirect font objectを追い、Tj/TJ/単一引用符/二重引用符の全文字命令を集計する。
q/Qによるfont状態の保存復元、BT/ET、font選択、文字状態の形式を検査する。
ToUnicode未割当code、非表示等の未対応render mode、未知operator、不整合な状態を拒否する。
XObject、Pattern、ExtGState、conditional resources、marked content、注釈、AcroForm等は
別の入れ子/状態処理が必要なため、この段階では拒否する。
後半ページの失敗を最初のページの成功で代替しない。

ページ別件数・content SHA・命令順Unicode SHAを返すが、生の文字列をログへ出力しない。
PDFのWidths配列は形式/範囲を検査するだけで、source hmtxとの配置一致は認定しない。
`textInstructionGlyphs`は文字命令の件数であり、clip後に見えている文字数を意味しない。
戻り値は常に`productionQualified=false`と`visibilityAndLayoutVerified=false`を含む。

## 資源上限

PDF/単一fontは64MiB、source fontは64 faces/計256MiB、embedded fontは128 objects/計256MiB。
ToUnicodeは64KiB、1コードは1 byte。PDFは最大50ページ、page contentは16MiB、
全contentは64MiB、全体の命令は500,000件、文字使用は2,000,000件。
compositeは深さ32未満、4,096 visits、65,536 points、graphics stackは256件まで。

これらのアプリ内上限は、PDF/fontライブラリがstreamを展開する途中の資源消費全てを
拘束しない。外部helperのcgroup/timeout/所有者leaseを省略する理由にはならない。

## 検証（parser 単体段階）

1. 新font testの実装前実行は、未作成moduleのimport error 1件で停止した。
2. font/code layer: 34 passed、0.30秒。
3. PDF全ページを加えた初回: 64 passed、0.46秒。
4. source cmap矛盾、global hint、component展開、文書共有の資源上限を追加:
   **81 passed、0.52秒**。

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_renderer_glyph_fonts.py tests/unit/test_renderer_glyph_pdf.py -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-glyph-expanded-0913
.venv/Scripts/python.exe -m ruff check app/services/renderer_glyph_fonts.py app/services/renderer_glyph_pdf.py tests/unit/test_renderer_glyph_fonts.py tests/unit/test_renderer_glyph_pdf.py
.venv/Scripts/python.exe -m mypy --platform linux --python-version 3.10 --follow-imports=silent app/services/renderer_glyph_fonts.py app/services/renderer_glyph_pdf.py
```

対象RuffとLinux/Python 3.10指定のmypyは成功。
初回のimport順/行長/lintと、pypdfのdictionary型検査不足を修正している。
WSLの専用venv/Python 3.10.12でも両moduleのimportを確認した。
fonttoolsはbackendの**dev依存だけ**へ版固定で追加し、既存typing_extensions等を変更していない。

`backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`は
**210 passed / 1 warning、42.50秒**。終了コード0。
呼出し元の検索では、新font moduleを参照するのは新PDF moduleだけで、
既存worker/API/provider/mapping/cropへの接続はない。
この変更境界と契約検証を根拠に、既存OCR contractを維持した。
`git diff --check`も成功した。

先行して実行中のbackend fullは、この81件を追加する前に収集された。
その完走結果を新glyph layerのfull回帰として扱わない。
この段階の実OpenAI/Google呼出しは0回である。

## 隔離 helper と版への接続

helper は固定 runtime の専用 venv を使い、read-only の入力 bundle / code snapshot / font 集合、
非特権 uid、ネットワーク分離、cgroup、呼出し元 lease を要求する。
入力・コード・font 集合の SHA と依存版を前後で検査する。
実 font 候補は固定 font 集合から見つけた後、使用 code の全字形比較で証明する。
font 名が存在するだけでは通過しない。要求 face と実 face が異なる場合は
`allMatchedFacesDeclared=false` として残す。

明示 qualification は DB mutation lock の外で helper を実行し、元版と印刷成果物の
再照合後に同じ source job の別 JSON artifact として保存する。
保存失敗・競合で元印刷ファイルを置換しない。旧 live-runtime report は現在設定で補完しない。
helper 失敗時も既に測定した再描画・geometry 証拠を保持し、glyph は未検証として残す。
生の helper エラーに含まれる path・帳票情報は API 応答に出さない。

GET は所有者・attempt・artifact ID・内容 SHA・版・全印刷成果物 graph・PDF・manifest・
現在の検証 code bundle を再照合し、PDF/font parse や Office/helper 起動をしない。
保存した表示 boolean / summary だけでは検証済みにしない。
字形 report が有効な場合だけ `FONT_GLYPH_UNVERIFIED` を除く。
実 face が宣言 face と異なる場合は `FONT_FALLBACK_UNQUALIFIED` を残す。
environment、geometry、crop、visibility の未認定条件と `qualified=false` は維持する。

実行条件、合成 helper 8 件、実 4 ページ 3,515 文字命令の read-only 照合は
[helper 実行証拠](verification/wsl-glyph-helper-20260913.md)を参照する。

## 残る完了条件

1. qualification 接続後の実環境 API と全体回帰を完走させる。
2. glyph 一致を geometry / visibility / crop / 実手書き OCR の資格に流用せず、各残条件を検証する。
3. 古い実ユーザー版には不足した固定環境証拠を後付けせず、適切な印刷再確定と正式資格検証を行う。

これらを満たす前にACTIVEや本番paged OCRのgateを解除しない。
