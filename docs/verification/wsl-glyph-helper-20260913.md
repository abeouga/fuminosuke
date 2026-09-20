# WSL glyph helper 実行証拠（2026-09-13）

## 実行境界

固定 SquashFS `9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01`
内の Python 3.10 / fonttools 4.65.0 / pypdf 6.10.0 を使用した。
検証コードは実行ごとに所有された read-only zipapp として渡し、前後の SHA を照合する。
PDF/font parse は uid 65534 の専用 helper 内だけで行う。
ネットワークは loopback のみで外向き接続拒否を実測。ホスト filesystem を隠し、
no-new-privileges、memory 1 GiB、swap 0、tasks 64、CPU 100% と所有者 lease を適用する。

これは helper の試験結果であり、環境全体の production qualification ではない。

## 合成 fixture と失敗注入

新 glyph unit tests: **130 passed / 0.95 秒**。
`tests/integration/test_wsl_glyph_helper.py`: **8 passed / 104.53 秒**。

- 既存 fixture の文字を含むページと空白ページをそのまま検査（文字命令数 `[2, 0]`）。
- 別 fixture で両ページの文字を検査（`[2, 5]`）。
- reference の font 集合、worker、font bytes の変更を拒否。
- 実行 code bundle の差し替えを SHA 照合で拒否。
- 存在しない固定 runtime を拒否し、host runtime に fallback しない。
- 呼出し元 EOF で所有する専用 unit が終了し、成功 report を発行しない。

実 LO 出力で、PDF name の直前の `/` は空白不要な区切りであること、
同じ TTC face が複数の明示 PostScript name を持つことを確認した。
厳密な文法と同じ font SHA / collection index を維持してこれらを扱う。
似た名前や異なる face を統合しない。
Calibri を要求した合成 fixture の日本語が別 font face で描画されることも確認した。
固定 font 集合から実際の候補を探し、全使用 code の glyph を照合する。
この場合 `allMatchedFacesDeclared=false` を明示し、要求 font が実際に使われたとは主張しない。

## 保存済み実帳票の read-only 検証

TemplateVersion: `6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`。
source job: `7b90d415-98d5-44e4-971a-e477ded0fdab`。
保存済み print generation: `faddf957091e43718320f96a71cc5c69`。

保存済み `print.xlsx` の bytes を変更せず、上記固定環境で reference を再生成した。
XLSX SHA: `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`。
再生成 PDF SHA: `9d82ef297188f08eaa293445057fc2be714e42f762520ec487f4df9e33790371`。
helper code bundle SHA: `a7315e33c5fdeb929f8c6a8b7a9d58ec44d32b06cfad95fac8eb2110af6ff8bf`。

| rendered page | 保存 PNG と bytes 一致 | text instruction glyphs |
| --- | --- | --- |
| 1 | true | 782 |
| 2 | true | 1,023 |
| 3 | true | 1,160 |
| 4 | true | 550 |

全 3,515 文字命令、4 embedded font objects の 640 使用 code を照合して PASS。
元 face は `MeiryoUI` / `MeiryoUI-Bold`、各 TTC collection index 2。
今回は `allMatchedFacesDeclared=true`。この名称を製品側の許可リストには使用していない。
輪郭・advance / side bearing・hint・Unicode 対応を照合した結果であり、
clip 後の可視文字数、元セルの全文字一致、位置・crop clearance の証明ではない。

保存済み XLSX / PDF / renderer report / 全 4 PNG の前後 SHA はすべて不変。
新しい PDF/report はこの調査の一時結果で、保存済み PDF や DB の資格情報を置換していない。
旧 renderer report に欠けている固定環境 identity を後付けで認定したことにもならない。
`productionQualified=false`、`visibilityAndLayoutVerified=false`、DRAFT と ACTIVE gate を維持。
実 OpenAI API **0 / 100 回**、Google API 0 回。

## qualification API 接続後の検証

`prepared_reference_glyphs.py` と `ReferenceQualificationService` に接続した。
保存 PDF は `load_prepared_reference` が既に所有者・SHA を検査した bytes を使用する。
helper は mutation lock 外で実行し、保存直前に印刷 graph / revision / config を再照合する。
結果は別の所有された JSON artifact で保存し、GET で同じ検証コードと入力へ結び直す。
元印刷ファイル、layout、mapping、過去 job、ACTIVE/受付判定は変更していない。

検証結果:

- 変更前 `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`:
  **210 passed / 1 warning / 67.19 秒**。repository root で実行。
  初回は誤って backend を cwd にして script 不在で終了したため、その実行は検証成功に数えない。
- 所有artifactの保存/GET境界 + bridge: **59 passed / 0.33 秒**。
- 既存 qualification / replay API と所有境界: **50 passed / 90.67 秒**。
- 所有境界 + 新 API fixture + 実固定環境 qualification:
  **34 passed / 95.02 秒**。
- 最終 glyph unit / 所有境界 / API fixture:
  **164 passed / 15.72 秒**。
- 変更後 OCR contract: **210 passed / 1 warning / 42.41 秒**。
- 対象 Ruff、4 service の mypy、`git diff --check`: 成功。

API fixture 初回は不足した requested/resolved family により準備時409で10件停止した。
新 fixture に完全な font manifest を設定して修正。次の所有者変更 fixture 1件は
存在しない job への外部キー違反で停止したため、別の実在 test job を作成して所有者違反を検証した。
既存 fixture/assertion や製品の安全条件は弱めていない。

失敗注入は artifact bytes / owner / attempt / version / glyph count / code identity、
再hashされた不正report、証拠のない成功summary、helper失敗の安全な表示、
検証中config/PDF変更の409と無書込み、実font fallbackの未認定条件保持を含む。
実WSL API試験は合成帳票で印刷確定→helper→保存→GETを通し、DRAFTと有効化拒否も確認した。
実ユーザー版の資格保存や有効化を実行した証拠ではない。

backend full は次のコマンドで実行した:

```powershell
.venv/Scripts/python.exe -m pytest tests -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-glyph-qualification-full-0913
```

実行 cwd は `backend`。同じ実行の完走結果は
**1,351 passed / 1 skipped / 3 warnings / 1,922.76秒（32分02秒）**、終了コード0。
収集後に追加した vector helper とそのテストは含まない。

同じ full を待つ間の frontend 再検証:

- `npm test`（cwd `frontend`）: **34 files / 369 passed / 19.82 秒**。
- `npm run build`: 成功。Vite build 2.08秒。JS bundle 645.86 kB の既存サイズ警告あり。
- ACTIVE 優先の印刷ボタン、取得失敗、途中保存と工程切替、ページ仮想見出し、
  hover接続線、paged review の fixture を含む。Chromeの実UIを操作した証拠ではない。

既存 OCR contract を維持した根拠は、変更範囲が qualification 証拠に限定され、
変更前後の契約検証が同じ210件を通過し、元の mapping/crop/OCR provider を変更していないことである。

## 残作業

glyph 接続後 full は完走した。引き続き environment closure、geometry tolerance、
crop safety、手書き入力での
OCR・レビュー・補正・確定・出力の検証が必要である。
先行 backend full の 1,178 passed は新 helper 追加前の収集結果であり、この変更の full 回帰ではない。
