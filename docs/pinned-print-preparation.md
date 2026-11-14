# 固定WSL環境を使う印刷確定

テンプレートの「印刷用ファイルを確定」（`POST print-layout/prepare`）で、固定したWSL変換用runtime image
（[WSL変換用runtime image](wsl-runtime-image.md)）を使う仕様。
通常の意味づけ/区分/位置変更のautosaveにOffice変換を加えるものではない。

## 背景

`PrintPreparationService._prepare`はFODS寸法測定とreference PDF/PNG生成をそれぞれ行う。
Fontconfig候補リストの比較だけでは、両変換の間でruntimeやfont集合が変化したことを検出できない。
そのため、両方の変換に同じruntime imageを明示し、変換後の証拠の一致を保存前に要求する。

## 仕様

- `AppSettings`/`RawSettings`の任意設定`PRINT_WSL_RUNTIME_ID`で使うimageを指定する。
  未指定は従来動作。指定は64桁lowercase SHAのみで、空白/改行/path/uppercaseを拒否する。
- `POST print-layout/prepare`はapp設定の同じIDをserviceへ渡す。
  service直接呼出しでも変換開始前に不正IDを拒否する。
- FODS測定とreference生成の両方へそのIDを渡す。image欠落時にlive runtimeへfallbackしない。
- 指定時だけ、両reportの`runtimeImage`全体（kernel等を含む）と
  `fontDirectoryIdentity`全体の一致をDB保存より前に要求する。
  どちらも欠けているから一致した、という判定は許可しない。
- 測定reportは既存`geometryEvidence.importReport`へ、reference reportは既存の所有artifactへ保存する。
  既存のlayout/cell/mapping/source identity、DRAFT/revision条件、短いDB transactionを維持する。
- 不一致では`PRINT_GEOMETRY_CHANGED`、実image欠落では`PRINT_PREPARATION_FAILED`を返し、
  layout/quality/config snapshot/登録artifactを更新しない。
- 保存後にapp設定が変わっても、ダウンロード時に現在のrendererで再生成しない。
  既存の版・revision・artifact SHAによる保存bytes取得を維持する。

未指定の既存plan/reportや過去jobへ新metadataを後付けしない。設定変更自体でDRAFTを更新しない。
設定例は`backend/.env.example`にある。

provider/model/prompt/OCR値/association/mapping/crop/出力先は変えない。

## この仕組みが保証しないこと

この接続は、固定環境・全font集合の証拠を印刷確定へ引き渡す処理である。
WSL host bootstrap/workerのidentity、全font形式・実文字配置/clip、実手書きcrop、
正式qualificationの発行・失効検査は、それぞれ別の仕組み（[OCR Safety Contract §12](ocr-safety-contract.md#12-xlsx複数ページocrの安全契約)）で扱う。
`environmentQualified`/`productionQualified`/ACTIVE/受付gateをこの設定で代替しない。
