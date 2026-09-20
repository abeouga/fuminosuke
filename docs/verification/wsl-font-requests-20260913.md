# WSL font要求の保持と実4ページ比較

## 原因・変更境界

`renderer_wsl_worker.resolved_fonts`はfamilyだけで重複除去し、太字・斜体を落としていた。
またfamilyをFontconfig検索式へそのまま渡すと、`- : , \`が名前ではなく構文になる。
実WSLのparser診断は[本番化記録](../production-paged-ocr.md)末尾の旧診断を参照。

新しい`wsl-fontconfig-face-v2`はfamily・weight・slantの組を保持する。
XMLの名前/boolean宣言を検証し、familyの特殊文字をescapeする。要求は128組に制限し、
要求patternと実解決family/style/weight/slant、font file、collection index、SHAを記録する。
Windows bridgeも新policyを要求する。過去の保存reportを変換・再生成しない。

これは事前のFontconfig候補記録である。OfficeがPDF描画に実際に使用した全font、
glyph fallback、theme・variable font・runtime依存の完全な追跡を実装したものではない。
Officeのfont設定、原本、印刷倍率、geometry、crop、mapping、providerを変更していない。
本番認定とACTIVE/受付gateは閉じたままであり、描画成功を認定へ昇格させない。

## 実データ比較

実DRAFT版`6d2187c0-ac7f-4d0c-90c8-a0fc381c2cc8`の保存済みprint.xlsxを、
既存の隔離WSL rendererで再変換した。新成果物は検証processのmemory内だけで比較し、
保存済みartifactを上書きしていない。DBはSQLite `mode=ro` / `query_only=ON`で照合した。

- 入力XLSX SHA: `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`。
- 4ページ全PNGのSHAと寸法は、保存artifact、旧renderer report、DBのreferenceImageと一致。
- page 1: `7144e03ee1646a67586344630794267be9b9d7da00be50b12bf3a7f942ddeefa`
- page 2: `5625043653da5df6d9970d630b6bcc1171e4085117051d6e9eb9424fd7ebb874`
- page 3: `0055b7d2e7dca8653b560e2757e731d4a758c424599fe3d2992c524c31eeccb1`
- page 4: `cde30781a7e1d87ae82bb185de049293d43b30d7e9e23cf78b427d30247dad47`
- Meiryo UIのregularとboldを区別。bold候補は`meiryob.ttc`、index 2、
  SHA `7d54f5a6a4202dae7b54d5a854770cb4fecd33f8975c0996e89c6ce31f4b0808`。
- 保存XLSX/PDF/全PNG/reportの前後SHA、版quality_jsonの前後bytesは一致。
- PDFは生成時刻等を含むため、再生成PDFのbyte一致を条件にしていない。

診断scriptはrepo外
`C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/compare_font_render.py`。
OpenAI/Google呼出し0回。実ブラウザ操作・実印刷・手書きcropの検証ではない。

## 対象検証と未解決の全体回帰

- 新helperの修正前試験: 13 failed（未実装interface）。実不具合の根拠は上記parser/要求消失。
- helper・既存renderer境界: 31 passed（3.50秒）。
- 実WSL・印刷準備: 19 passed（52.68秒）。Calibriのregular/bold/italic/bold-italicについて
  4つの異なるfile/faceとSHA、要求/解決weight/slantを実隔離環境で検証した。
- report policyと境界の追加実行: 34 passed / 1 failed（3.94秒）。
  失敗は既存Windows終了確認ケースであり、下記の未解決事項として保持した。
- Ruff: 対象7ファイル成功。Linux workerの限定mypy 1 source成功、
  Windows bridge/environmentの限定mypy 2 sources成功。
- 変更後`backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`:
  210 passed / 1 warning（53.41秒）。既存OCR contractの対象試験は通過。
  backend全体・実OCR・renderer本番認定の代替にはしない。
- 最終の新規unit 2ファイル: 17 passed（0.13秒）。`git diff --check`成功。

直前のresource変更後backend全体は
`pytest tests -q -o addopts='' -p no:cacheprovider --basetemp=.pt-paged-resources-full-0913`で
1028 passed / 1 failed / 1 skipped / 3 warnings（1524.78秒）。全体成功ではない。
失敗したprivate temp試験は共有system tempの別所有marker付き残存領域を収集した。
その発生元は未確定。試験rootを固有tmp_pathへ限定し、実ACL・lease・cleanup経路と
既存の全assertionを保持した。製品cleanup処理は変更していない。

別の`test_timeout_tree_and_unrelated_process`は、終了直後の子PIDが残るため間欠失敗した。
単独再実行は1 passed（2.12秒）だが、それを解決の証明として扱わない。
独立probeで子の正確なprocess handleを保持した3回のうち2回、`run_contained`から戻った時点で
exit code=1かつhandle未signaledを観測し、その後のhandle waitは完了した。
PID存在の瞬時確認と終了完了は同義ではなく、既存のactive count待機だけで完了を保証できていない。
[MicrosoftのTerminateJobObject仕様](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject)と
[非同期TerminateProcess仕様](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)
とも照合した。試験のsleep追加・assertion緩和や製品containmentの推測変更はしていない。
これはWSL cgroupの終了試験とは別のWindows経路の未解決事項である。

`renderer_environment`の削除済みmetadata hash cacheを示す古い説明値も
`none-content-rehashed-v1`へ訂正し、completeTrace/environmentQualified=falseを維持した。
現在のbackend全体回帰・OCR安全性を完全には検証できていない。
