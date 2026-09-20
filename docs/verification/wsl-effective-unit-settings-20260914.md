# WSL transient unitの実効設定確認

## 調査範囲

正式な環境資格発行の前提を確認するため、既存Ubuntu-22.04内に、この調査専用の
短時間 `sleep` unitを2個作成した。原本・PDF・スキャンを渡さず、Officeも実行していない。
新しいパッケージ、常駐service、システム全体の設定は導入・変更していない。
両unitは調査コマンド内で、それぞれの正確な名前を指定して停止した。コマンド終了codeは0。

## 観測

1件目は現rendererと同じ資源・停止制御の起動プロパティを使用した。
unit: `llmocr-qualification-probe-0b6cfca741bc402fb770b02fc12d2b35`

`systemctl show` が返した実効値:

```text
Restart=no
TimeoutStopUSec=2s
RuntimeMaxUSec=1min
RemainAfterExit=no
CPUQuotaPerSecUSec=1s
CPUQuotaPeriodUSec=infinity
MemoryMax=1073741824
MemorySwapMax=0
TasksMax=64
LimitFSIZE=67108864
LimitFSIZESoft=67108864
LimitCORE=1
LimitCORESoft=1
RootDirectory=
RootImage=
User=
Group=
KillMode=control-group
SendSIGKILL=yes
LoadState=loaded
ActiveState=active
DropInPaths=
Transient=yes
```

2件目では `--all` を付け、起動/終了hook・EnvironmentFiles等も指定した。
unit: `llmocr-qualification-probe-b886661e67ea43129a3767e9e01c7d14`
返ったのはControlGroup、LoadState、ActiveState、DropInPaths、Transient。
ExecStartPre/Post、ExecStop/Post、EnvironmentFilesは出力されなかった。
**出力欠落を、hookや追加環境設定が存在しない証明として扱わない。**

## 実装との照合と限界

`renderer_wsl_worker.inner` は入力を解析する前に、namespace、権限、
cgroup memory/swap/pids/CPU値、通信拒否、core guardを検査する。
一方、`renderer_wsl_worker.broker` と `renderer_glyph_worker.broker` は、
RuntimeMaxSec・TimeoutStopSec・KillMode・LimitFSIZE等を起動引数として要求しているが、
その実効unitプロパティを入力解析前に取得・保存する経路はまだない。

今回のsleep unitで設定が反映されたことと、実帳票を処理する全workerの同じ条件を
認定できたことは別である。資源超過・native crash・親切断の再試験でもない。
この観測を環境認定証や本番許可として保存していない。
最新の印刷成果物や実DRAFTのDBにも変更していない。

前ターンまでの証拠検査修正とは別のread-only/一時unit調査で、製品コード変更なし。
OpenAI 0/100回、Google 0回。
