# 元XLSXとPDFのページ別文字数照合

固定WSLの全体回帰を変更せず、スプレッドシート/PDFスキルに従ってread-only分析を行った。
原本・印刷用XLSX・PDFの再保存/再生成・書式変更・再計算・実DB書込は行っていない。

## 対象と範囲

- DRAFT版 `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8` の保存printLayout。
- sourceWorkbook SHA `aa1d088cf5ebae9cfca0f42d0ac3cbe1d8b23f7067196407f6ae3ce9c3cc3176`。
- 印刷用XLSX SHA `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`。
- PDF SHA `56bb73ba362702df0e251de465bffa1bd2cfe79e2c2b53201db85aeb78c35fd4`。
- sourceWorkbook artifactと版はSQLiteのmode=ro/query_onlyで取得し、source SHAとplanを照合した。
- workbook relationshipと保存sheet名で対象worksheetを解決し、保存4rangeを使用した。
- 原本と印刷コピーの対象worksheet全cell XML、sharedStrings、stylesの一致を確認した。
  hidden管理sheetを表示対象へ追加せず、原本指定のhidden列Aも文字集計から除いた。

## 照合方法

元XLSXのshared/inline string・rich text main runと、対応済みの整数表示を読む。
数式/未知の値型/未対応表示形式があれば未検証として記録し、成功assertionを通さない。
今回の対象では未対応値は0だった。文字列のOOXML escapeを一度だけ解決した。

PDF側は[使用文字コードと字形の照合](paged-embedded-fonts-20260913.md)の
`Tf`・`q/Q`・`Tj/TJ`・ToUnicode解釈を再利用し、pypdfの便宜的な抽出順や空白挿入を使わない。
元文字とPDF文字の種類別個数を各ページで比較する。空白を一律削除・正規化しない。

最初の生値比較ではpage 1に空白6個・開括弧3個・閉括弧3個の増加を認めた。
元stylesのnumFmtId 176、`\(\ @\ \)`がY21/AC21/AG21へ適用されていることを確認した。
この表示書式は文字列の前後に括弧と空白を付加する。該当するescaped literal＋単一@だけを
読み取る限定parserで元表示を計算すると、差が全て説明できた。
これはPDF差に合わせて元値や書式を変更したものではない。

## 結果

| ページ | 保存range | 非空の元cell数 | 元表示文字数（改行含む） | PDF表示glyph数 | 元の改行制御 |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | A1:AK28 | 72 | 784 | 782 | LF 2 |
| 2 | A29:AK60 | 49 | 1024 | 1023 | LF 1 |
| 3 | A61:AK89 | 76 | 1160 | 1160 | 0 |
| 4 | A90:AK102 | 7 | 550 | 550 | 0 |

改行をglyphとして数えない比較では全ページが一致した。表示文字の総数は3515。
各ページで非空白1文字をmemory上で欠落させるケースと重複させるケースを作り、
同じ比較で全8件の差を検出した。元ファイルへ書き戻していない。

元XMLのcell順で連結した文字列とPDFの描画命令順の完全一致は、page 4のみ成立した。
page 1〜3は一致しなかった。この事実を配置不良とも配置正常とも断定しない。
字形の位置・clip・セル別の対応はこのmultiset比較では証明できない。
文字の入替えや、同じ文字の欠落と余計な複製が相殺される場合も検出できない。
したがって「全文が正しい場所へ配置された」「本番資格成立」の証拠とはしない。

## 再現・保持

Bundled Pythonを使用した。診断scriptはrepo外
`C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/compare_source_pdf_text.py`。
同directoryの`verify_pdf_character_glyphs.py`を読み取り専用で実行して文字解釈を共有する。
原本と印刷用XLSXの前後bytes、PDF/元fontの前後SHA、版quality_jsonの前後一致をassertした。
fontToolsの古いmodified timestamp警告は残るが、時刻を字形一致判定へ使っていない。

製品コード、依存、OCR contract、mapping/association/crop、原本・保存版・過去jobの変更なし。
実OpenAI/Google呼出し0。`git diff --check`を確認し、並行中のfullを新しい診断の完走証明に流用しない。
全体回帰は`.pt-wsl-font-directory-full-0913`の同一processで継続している。
