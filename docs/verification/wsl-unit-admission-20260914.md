# WSL実効unit設定の解析前検査

## 原因・変更範囲

従来brokerはsystemd-runへ時間・終了・file-size制限を要求していたが、
子が帳票を解析する前に実効unit値を照合する経路がなかった。
追加した境界テスト2件は変更前に失敗した。旧workerが実際に無制限で
帳票を処理した証明ではなく、解析前検査と証拠受理条件の不足を再現したもの。

固定runtimeのOffice/FODS/referenceとglyph/vectorに次を追加した。

- broker専用のadmission directoryを子へreadonly mountする。元の入力/出力mountは保持する。
- 起動直後、所有するunit名・ControlGroup・active/running状態を照合する。
  systemctl出力は16KiB、1照会2秒、解析開始待ち5秒以内に制限する。
- 実効RuntimeMaxUSec、TimeoutStopUSec、KillMode、SendSIGKILL、Restart、
  RemainAfterExit、FSIZE/COREのsoft/hard、memory/swap/tasks、
  root/user/group設定、Transient、DropInPathsを明示的に検査する。
  未起動は開始許可ではなく待機。欠落・重複・予期しない値・失敗応答は拒否する。
- 一致した記録をroot-owned fileとして作成し、readonly directory内へatomic renameする。
  子は実際のreadonly mount・regular file・非書込mode・件数/size・読取中の不変を検査する。
  さらに自身のRLIMIT_FSIZEを検査する。許可記録がなければfont/XLSX/PDF解析へ進まない。
- 起動許可待ちにも既存owner heartbeat/EOFの停止処理を適用する。
  失敗時は所有unitだけを停止し、既存temp回収を使用する。
- reportのunitControlsには検証済みの安定設定だけを保存し、unit UUIDを再描画identityへ混ぜない。
  bridge・保存prepared graph・再描画比較・環境lease・glyph/vector証拠で完全一致を要求する。
  観測のない旧reportへ成功証拠を補完せず、固定runtimeなしの旧変換動作は維持する。

これは実行境界の追加検査であり、環境資格発行器の代替ではない。
systemdの未出力hookを不存在と推定していない。信頼されたhost管理者による
変換中の設定変更や、Windows/WSL全体の完全な依存traceの証明とも区別する。
原本・実DB・geometry・意味づけ・mapping/crop・provider/model/prompt/schema・補正/出力は変更しない。

## 再現した実装ミスと修正

最初の実WSL試験は2 failed / 3 passed。正常系がadmission fileの所有者検査で失敗した。
追加の実値確認では、子からuid=65534、mode=100444、links=1、bytes=469と見えた。
user namespace内のuidをhost root=0と比較する実装が誤っていた。
所有権をroot broker側で確認し、子側は実際のreadonly mountとfile属性を検査するよう修正した。
既存テストの期待エラーや画像一致条件を削除・緩和していない。

## 検証

- 直前のbackend full baseline: 2348 passed / 1 skipped / 3 warnings（2315.61秒）。
  本変更より前の結果であり、本変更後の全体成功ではない。
- 変更前OCR契約script: 210 passed / 1 warning（62.70秒）。
- 追加境界の変更前: 2 failed（0.42秒）。
- 初期単体/bridge/lease: 223 passed（0.71秒）。
- 修正後の単体/保存参照/再描画/glyph/geometry: 312 passed（2.02秒）。
  glyph報告の欠落/不正拒否3件も追加した最終実行: 315 passed（1.93秒）。
- 実WSLの固定/従来画像比較、設定不一致拒否、worker差替え拒否、親切断、
  glyph/vector helper: 15 passed（210.01秒）。
- 対象8 PythonファイルのRuff成功。Linux worker 2ファイルと
  Windows側bridge/証拠検査2ファイルの対象Mypy成功。
  全プロジェクトMypy成功とは扱わない。
- 保存/再取得/再描画/印刷確定API: 81 passed（206.36秒）、終了コード0。
- 変更後OCR契約script: 210 passed / 1 warning（38.37秒）、終了コード0。
  変更前と同じ対象。実OpenAIによる認識品質測定は含まない。
- 本変更後のbackend fullは未実施。後続の環境資格実装と合わせて全体回帰が必要。

OpenAI 0/100回、Google 0回。元XLSX・提供スキャン・実DBへの書込なし。
有効化、実スキャンOCR→レビュー→補正→確定→出力、実公開カード取得は未完了。
既存OCR contractを維持する変更であり、OCR安全性を完全には検証できていない。

再現コマンド（backend cwd、Pythonは `.venv/Scripts/python.exe`）:

```text
python -m pytest -q --tb=short -o addopts='' -p no:cacheprovider tests/unit/test_wsl_unit_admission.py tests/unit/test_wsl_execution_evidence.py tests/unit/test_wsl_environment_evidence.py tests/unit/test_renderer_glyph_bridge.py tests/unit/test_renderer_vector_bridge.py tests/unit/test_prepared_reference_replay.py tests/unit/test_prepared_reference_glyphs.py tests/unit/test_prepared_reference_geometry.py
python -m pytest -q --tb=short -o addopts='' -p no:cacheprovider tests/integration/test_wsl_frozen_runtime.py tests/integration/test_wsl_glyph_helper.py tests/integration/test_wsl_vector_helper.py
```
