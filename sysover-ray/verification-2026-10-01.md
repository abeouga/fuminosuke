# SysOverRay デプロイ版の起動・再起動・停止検証

確認日: 2026-10-01（日本時間）
対象: `C:\Users\hanam\Desktop\llm-ocr-deploy\sysover-ray`
artifact: `D:\llm-ocr-archive\sysover-ray-deploy\20261001-012843`

## 結果

デプロイ版の実行ファイルを開き、実画面の「起動」「再起動」「停止」を順に操作した。いずれも成功した。最終状態はSysOverRayのみ起動中、backend/frontendは停止中。

| 操作・確認 | 結果 | 根拠 |
| --- | --- | --- |
| Release publish | 成功 | publish.log、実行ファイルの起動 |
| 起動 | backend 8010 / frontend 5180 / frontend経由APIがHTTP 200 | started.json、ui-started.txt |
| backendの状態 | database=ok、jobWorker=running、ocrProvider=openai | started.json / restarted.json |
| CORS | 許可originが `http://127.0.0.1:5180` | started.json / restarted.json |
| 再起動 | backend PID 30972→19032、frontend PID 31244→26224。両方HTTP 200へ復帰 | restarted.json、ui-restarted.txt |
| 停止 | 専用ポートLISTEN 0件、関連プロセス0件、両HTTPとAPIプロキシが応答停止 | stopped.json、ui-stopped.txt |
| 停止の再実行 | 対象プロセスがない状態でも終了コード0 | stop-again.log |
| 別プロジェクトのポート所有者 | 終了コード6で拒否し、試験用serverの生存・待受を維持 | foreign-owner-guard.json / .log |

再起動は操作開始から約12秒、停止は約3秒で完了した。これは今回のローカル環境での記録であり、他環境の時間保証ではない。

## 原因と修正

- コピーされた `SysOverRayを起動.lnk` とoverlay設定が、元のDesktop上のSysOverRay / llm-ocrを指していた。デプロイ側の実行ファイルと起動処理へ変更した。
- 5173・5174は開始時点で別アプリが使用していた。ユーザーの選択に従いfrontend 5180・backend 8010を専用ポートにした。
- `Start-LlmOcr.ps1` を追加し、overlay設定と同じポートで `scripts/dev.ps1` を起動する。dev.ps1へ明示ポート指定を追加し、APIプロキシとfrontend許可originを合わせた。
- 設定の相対パスを検出したプロジェクトルートに解決する。元フォルダへの既定パスを除去した。
- 通常のポート清掃はプロジェクトのコマンドラインと親プロセスを確認する。Windowsのvenv Pythonが起動するbase-Python子プロセスも親を通じて照合する。別プロジェクトは停止しない。
- 新しい起動コントローラーも通常停止の対象に含めた。再起動はHTTP応答の有無に関係なく停止処理を先に実行する。
- 「関連ポートを全停止」の設定範囲も5180・8010だけに限定した。この明示的な全停止操作のUIテストは今回行っていない。

## 起動設定と検証境界

- `EnableGoogleIntegration: false` のv0.1ローカル起動設定を使用した。Google OAuth / Sheetsは子プロセス内だけで無効化し、backend/.envは書き換えていない。Google連携を有効にする場合は8010のcallbackをGoogle側へ登録する必要がある。
- OCR provider・model・prompt・schema・mapping・crop・補正・出力処理は変更していない。起動直前のDBでは既存jobはCONFIRMED 1件で、新しいOCR jobは作成していない。既存OCR contractを維持した。
- HTTP health / APIプロキシ / CORS / 実プロセスと実画面の状態遷移を確認した。帳票読取、外部Google連携、クラウド配備の検証は含まない。
- 既存5173・5174は最初の起動後もHTTP 200だった。その後、別アプリのPIDと稼働状態が変化したため、検証期間全体で不変だったとは主張しない。通常停止logの対象はデプロイ側のcontroller PID 30628 / 26836であり、別ポートの所有者を停止対象にしていない。
- fixture・回帰テストの追加は行わず、今回必要な実機操作に限定した。一時的なruntime採取scriptと試験用serverは終了・除去し、結果JSON・UI記録・ログ・開始時のコピーはartifactフォルダに保存した。

## 主な変更ファイル

- `SysOverRay/OverlaySettings.cs`、`overlay-settings.json`
- `SysOverRay/LlmOcrController.cs`
- `SysOverRay/scripts/Stop-LlmOcr.ps1`、`Stop-LlmOcrPorts.ps1`
- `Start-LlmOcr.ps1`
- `SysOverRayを起動.lnk`、`llm-ocr-deployを起動.lnk`
- `app/` の発行済み実行ファイル・設定・停止script
- `../scripts/dev.ps1`
- `SysOverRay/README.md`、本記録

ビルドコマンド（作業ディレクトリ: llm-ocr-deploy）:

```powershell
dotnet publish sysover-ray\SysOverRay\SysOverRay.csproj -c Release -r win-x64 --self-contained false -p:PublishSingleFile=true -o sysover-ray\app
```

backend / frontend全テストの再実行は行っていない。今回の変更は起動・停止・ローカルポート設定であり、実画面の操作とHTTP・プロセス検査、PowerShell構文解析、.NET publish、差分確認を使用した。
