# 固定WSL環境を使う印刷確定

本番化要求の範囲で、既存の明示的な「印刷用ファイルを確定」APIへruntime image指定を接続した。
通常の意味づけ/区分/位置変更のautosaveへOffice変換を追加する変更ではない。

## 原因と変更

`PrintPreparationService._prepare`は、固定runtime選択APIが存在しても、FODS寸法測定と
reference PDF/PNG生成をruntime未指定で呼んでいた。Fontconfig候補リストの比較だけでは、
両変換の間でruntimeやfont集合が変化したことを検出できなかった。

- `AppSettings`/`RawSettings`へ任意の`PRINT_WSL_RUNTIME_ID`を追加した。
  未指定は従来動作。指定は64桁lowercase SHAのみで、空白/改行/path/uppercaseを拒否する。
- `POST print-layout/prepare`がapp設定の同じIDをserviceへ渡す。
  service直接呼出しでも変換開始前に不正IDを拒否する。
- FODS測定とreference生成の両方へそのIDを渡す。image欠落時にlive runtimeへfallbackしない。
- 新指定時だけ、両reportの`runtimeImage`全体（kernel等を含む）と
  `fontDirectoryIdentity`全体の一致をDB保存より前に要求する。
  どちらも欠けているから一致した、という判定は許可しない。
- 測定reportは既存`geometryEvidence.importReport`へ、reference reportは既存の所有artifactへ保存する。
  既存のlayout/cell/mapping/source identity、DRAFT/revision条件、短いDB transactionを維持する。
- 不一致では`PRINT_GEOMETRY_CHANGED`、実image欠落では`PRINT_PREPARATION_FAILED`を返し、
  layout/quality/config snapshot/登録artifactを更新しない。
- 保存後にapp設定が変わっても、ダウンロード時に現在のrendererで再生成しない。
  既存の版・revision・artifact SHAによる保存bytes取得を維持する。

未指定の既存plan/reportや過去jobへ新metadataを後付けしない。設定変更自体でDRAFTを更新しない。
`.env.example`へコメント付き設定例を追加したが、実`.env`/`.env.local`は読み書きしていない。
実アプリの設定反映、実テンプレートの再確定、有効化を実施済みとは扱わない。

## 検証

- 変更直前full: 1097 passed / 1 skipped / 3 warnings（1521.18秒）。
  変更前OCR contractは210 passed / 1 warning（40.38秒）。その後は本実装前まで製品コードを変えていない。
- 新設定の8ケースは未実装時に失敗し、実装後に通過した。
- 設定・新API境界・既存印刷確定の対象セット: 28 passed（35.15秒）。
- 追加の実WSLを含む固定印刷確定セット: 9 passed（48.85秒）。
  同一IDの両呼出し/証拠保存、kernel/runtime/font集合差/証拠欠落の拒否と無更新を確認。
  実WSLでは人工2ページの保存・PDF取得と、存在しないimageの無更新拒否を確認した。
  成功後もDRAFTと既存ACTIVE拒否を維持する。
- 対象Ruff成功。config/route/serviceの限定mypyは3ファイル成功。
- 変更後OCR contract: `backend/.venv/Scripts/python.exe scripts/verify_ocr_contract.py`、
  210 passed / 1 warning（71.51秒）。前後とも同じ210件の契約検証を使用した。
- 変更後full: `pytest tests -q -o addopts='' -p no:cacheprovider
  --basetemp=.pt-pinned-print-preparation-full-0913`。
  同一processの完走結果は **1114 passed / 1 skipped / 3 warnings（1671.95秒）**、終了コード0。
  後続worker identityの新規9ケースは収集後の追加で、このfullには含まれない。

変更ファイルはconfig、print-layout route、PrintPreparationService、`.env.example`、対象testと本記録。
frontend/provider/model/prompt/OCR値/association/mapping/crop/出力先の変更なし。
実OpenAI/Google呼出し0。旧OCR contractを維持する変更境界である。

## 残条件

この接続は、固定環境・全font集合の証拠を印刷確定へ引き渡す処理である。
WSL host bootstrap/workerのidentity、全font形式・実文字配置/clip、実手書きcrop、
正式qualification issuer/失効検査、実アプリ反映と本番有効化はなお未完了。
`environmentQualified`/`productionQualified`/ACTIVE/受付gateを変更して代替しない。
