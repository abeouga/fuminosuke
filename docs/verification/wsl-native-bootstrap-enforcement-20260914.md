# WSL bootstrap ELF起動依存の実行時照合

## 変更境界

Python検索treeだけでは、host側のPython拡張・変換起動ツールが使用する共有ライブラリの
変更を現在の変換に結び付けて検出できなかった。別実行のldd調査記録を証明として流用せず、
`renderer_wsl_worker.native_bootstrap_identity` を既存のbootstrap前後照合に接続した。

- `bootstrapPythonIdentity` は `wsl-bootstrap-python-tree-v2`。
  `nativeDependencies` に起動依存graph/ファイル内容の集合SHA、件数、bytes、policyを含む。
- 対象は固定hostツール、Python 3.10の拡張 `.so`、libseccomp、固定loader、
  解決された共有ライブラリとloader cache/config。原本・PDF・任意の利用者指定pathは渡さない。
- root所有と非書込権限をalias/resolved ancestry双方で検査し、読取前後のstat/byte数と
  解決先を確認する。metadataだけでhashを再利用しない。対象集合も再取得して比較する。
- 固定x86_64 glibc loaderの `--list` を固定環境で呼ぶ。
  起動対象本体は実行せず、依存先を列挙する。
  仕様は [Linux man-pagesのld.so](https://man7.org/linux/man-pages/man8/ld.so.8.html) を参照。
  installed lddのloader呼出しも読取確認したが、製品はldd shell scriptを実行しない。
- `LD_*` / `GLIBC_TUNABLES` の設定、`/etc/ld.so.preload` の存在、未知行・欠落依存・
  非対応ELF形式・内容や集合の変更は失敗とする。ASLR addressはidentityに入れない。
- 256起動対象、1024ファイル、個別128MiB/合計512MiB、個別呼出し5秒、
  次の呼出し開始までの全体20秒を上限とする。出力は一時fileへ受け128KiBまで検査する。
- Office/glyph/vectorの前後比較、寸法測定とPDF生成の比較、保存後の再描画比較は、
  既存のbootstrap identity全体比較でこの追加証拠も要求する。v1の証拠をv2へ補完しない。

これはELFの起動時解決と内容の照合であり、全dlopenのtraceやsystemd設定、kernel/Windows、
完全な環境資格発行ではない。環境資格・crop安全性・本番OCR/ACTIVE gateは未解除。
旧単一ページOCR、mapping/association、帳票の意味づけ、原本、入力画像を変更していない。

## 実環境の読取結果

製品collectorを2回実行し、一致を確認した（終了コード0、合計約0.72秒）。
60起動対象、111ファイル、42,008,652 bytes。
SHA: `daa9da2ed11885dc39e6309f1620047f93e898fc2d7b00f97dd7ff1f47cb48d8`。
過去のldd診断とは対象・graph方式が異なるため、以前の集合SHAとは比較しない。

## 検証

- 関連単体テスト **180 passed（0.77秒）**。
- 実WSLのPython/ELF内容照合、live/fixed PDF画像比較、glyph helper、
  改変/不足/所有process終了の検証 **11 passed（145.48秒）**。
- ELF読取検証の再実行と保存APIの欠落/変更拒否・DB/layout/artifact非更新 **18 passed（26.15秒）**。
  指定した保存APIケース以外の2件は `-k` の選択対象外。skip追加ではない。
- 対象Ruff、Linux workerのMypy、`git diff --check` 成功。
  最初のRuffでは新しいテストscriptの長い1行を検出し、改行して再実行した。
- 変更前OCR契約scriptは210 passed / 1 warning（前段のPython tree反映後）。
  変更後も **210 passed / 1 warning（38.01秒）**、終了コード0。
  前後の契約検証と変換画像比較の範囲で既存OCR contractを維持した。
  実API認識精度・全体回帰・有効化成功の証明ではない。

再現コマンド（backend cwd、Pythonは `.venv/Scripts/python.exe`）:

```text
python -m pytest -q -o addopts='' -p no:cacheprovider tests/unit/test_wsl_native_bootstrap.py tests/unit/test_wsl_bootstrap_python.py tests/unit/test_prepared_reference_replay.py tests/unit/test_renderer_glyph_bridge.py tests/unit/test_wsl_worker_identity.py tests/unit/test_wsl_runtime_selection.py tests/unit/test_wsl_dimension_observation.py
python -m pytest -q -o addopts='' -p no:cacheprovider tests/integration/test_wsl_native_bootstrap_runtime.py tests/integration/test_wsl_bootstrap_python_runtime.py tests/integration/test_wsl_frozen_runtime.py::test_real_frozen_reference_matches_live_runtime_and_keeps_isolation tests/integration/test_wsl_glyph_helper.py
python -m pytest -q -o addopts='' -p no:cacheprovider tests/integration/test_wsl_native_bootstrap_runtime.py tests/integration/test_pinned_print_preparation.py -k 'installed_dependencies or preparation_pins_both_calls'
python -m mypy app/services/renderer_wsl_worker.py --platform linux --no-incremental
```

root cwd: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`。
worker SHA: `405759edcb6514caccd6b39223c8fda0c99d43dccd25ba9902311276898b3e9e`。
変更ファイルはworker、synthetic bootstrap fixture、新規unit/integration各1ファイル、
既存pinned preparation integration、Safety Contract、本報告。既存差分は保持し、commit/pushなし。

今回の変更後backend fullは **2262 passed / 1 skipped / 3 warnings（2133.72秒、35分33秒）**、
終了コード0。core guard/Python tree/ELF照合の追加と統合テスト改名を含む。
既存のskip 1件とspreadsheet importテストのfont.copy deprecation警告3件を維持する。
前回2157 passedのfullはこれら追加より前の履歴として区別する。OpenAI 0/100回、Google 0回。

全体回帰開始時に、unit/integrationで同名のbootstrap Python/ELFテスト2組が衝突し、
収集エラーで停止した。統合テスト側を `_runtime.py` へ改名して再実行し、上記のとおり完走した。
これは分割実行では検出されなかったテスト配置の不具合であり、cache削除・import mode変更・
skip・assertion変更で回避しない。上の再現コマンドは改名後のpathを使用する。

fullコマンド（backend cwd）:
`RUN_OPENAI_INTEGRATION_TESTS=false` として
`.venv/Scripts/python.exe -m pytest -q --tb=short -o addopts='' -p no:cacheprovider --basetemp=.pt-native-bootstrap-full-final-0914`。
実行中は製品ソースを固定し、別のnative rendererを起動しなかった。
同じ時点のfrontend fullは35 files / 373 passed（15.93秒）、build成功。
全体検証通過を実テンプレートの有効化・本番OCR完了とは扱わない。
