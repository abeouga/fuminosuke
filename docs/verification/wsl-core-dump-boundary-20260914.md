# WSLクラッシュダンプ境界の読み取り専用調査

座標照合接続後のbackend full（`.pt-calc-precision-full-0914`）が同一processで稼働中に調査した。
製品コード・WSL設定を変更せず、Office/PDF helperを別起動せず、意図的なクラッシュも行っていない。
この記録は正常な描画やネットワーク隔離だけでは確認できない異常終了時の境界を対象とする。

## 現在の設定と実装

Ubuntu-22.04で読み取り専用に取得した値:

- `kernel.core_pattern`: `|/wsl-capture-crash %t %E %p %s`
- `kernel.core_uses_pid`: `1`
- `kernel.core_pipe_limit`: `0`
- `fs.suid_dumpable`: `0`
- 起動した独立Python probeの `RLIMIT_CORE`: soft 0 / hard infinity。
  これはrenderer unit内の実効値ではない。

通常のdistroからは `/wsl-capture-crash` のfile/linkは存在しなかった。
この不在を、kernelが起動するhelperのnamespaceでも存在しない証拠やダンプ抑止の証拠にはしない。
クラッシュデータが実際に保存された、または帳票が漏洩したという実測はない。

現 `renderer_wsl_worker.broker` はmemory/swap/tasks/cpu/runtime/file-sizeとKillModeを指定するが、
core limitは指定しない。`inner` はmemory/swap/tasks/cpuを実測するが、core抑止を検査しない。
glyph/vectorのworkerを含む検索でもcore設定・検証は見つからなかった。
したがって、既存の `networkDenied` / `hostFilesystemHidden` / resource reportだけから
クラッシュダンプ経路まで閉じたと判定することはできない。

## kernelソースとの照合

実行kernelと同系列のMicrosoft公開
[linux-msft-wsl-6.6.87.2/fs/coredump.c](https://github.com/microsoft/WSL2-Linux-Kernel/blob/linux-msft-wsl-6.6.87.2/fs/coredump.c)
を確認した。pipe経路ではsoft core limitが1ならhelper起動前に中断し、それ以外は
limitをinfinityへ置き換える。したがって、単に `LimitCORE=0` を足す案はこの境界を閉じない。
一般のファイル経路とは異なる。[kernel設定の公式説明](https://docs.kernel.org/admin-guide/sysctl/kernel.html#core-pattern)
もpipeによるhelperへの引渡しを定義している。

soft/hardを1にするだけの案も不十分である。独立した無入力Python processで
`setrlimit(RLIMIT_CORE, (1, 1))` の後に `(0, 1)` へ下げられることを確認した。
最初のprobeはuid 0だったため非特権の証拠として使わず、`wsl -u nobody` のuid 65534で再実行した。
後者も `(0, infinity) -> (1, 1) -> (0, 1)` が成立し、終了コード0。
自身の一時processのresource limitだけを変え、ファイル作成・crash・host policy変更はない。

これは候補対策の改変可能性を示す安全なprobeであり、rendererが実際にlimitを下げた証拠ではない。

## 次に必要な検証

本番環境認定には、専用rendererと子プロセスのcore抑止条件が維持されることを要求する。
kernel固有のsoft-limit値を使う場合は、非特権processによるその変更も防止する必要がある。
その実装と、変更拒否・exec/fork後の維持・正常Office/PDF互換性・所有者終了時の挙動を
独立した合成fixtureで検証してから、実行evidenceと環境identityへ結び付ける。
単なる `ulimit` / boolean追加や、probe自身だけの抑止を全native処理へ一般化しない。

WSL全体のcore_patternを書き換える方法は他の作業へ影響するため実施していない。
この調査でホスト全体の設定変更権限が追加されたとは解釈しない。
現時点では正式な環境issuerも本番gateも未変更。有効化は未完了。
原本・スキャン・本番DB・既存OCR contractは不変。OpenAI 0/100、Google 0。

## 後続の無入力process限定prototype

上記の読み取り専用調査の後、製品外の
`C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/probe_core_limit_filter.py`
でprocess-localな対策を検証した。製品worker・稼働中アプリ・全体試験・WSL全体の設定は変更していない。
Ubuntu-22.04のuid 65534、x86_64、kernel `6.6.87.2-microsoft-standard-WSL2`で実行し、
固定renderer imageではなくhostのlibseccomp 2.5.3を使用した。
library SHA-256は `0a10b36f83b58889352ef9de563fddeb80ba649d6a7108f32f31cfb08693e2a3`。

soft/hard core limitを `(1, 1)` に設定してから、no_new_privsとTSYNC付きseccomp filterを導入した。
`setrlimit`のresourceと`prlimit64`のresourceは下位32 bitで照合し、COREへの変更をEPERMで拒否する。
`prlimit64`のnew_limitsがNULLのgetterは許可する。他resourceの読取と同値設定も保持する。
syscall番号はlibseccompの名前解決を使用し、不一致architectureはKILL_PROCESSとした。
API定義は[libseccomp v2.5.3 header](https://github.com/seccomp/libseccomp/blob/v2.5.3/include/seccomp.h.in)に照合した。

確認結果（実行終了コード0）:

- 親、filter導入前から存在するthread、execした子でcore limit `(1, 1)` を維持した。
- 各対象で通常のresource値4と上位bitを加えた値に対するsetrlimit/prlimit64の4変更を拒否した。
- 各threadの実効no_new_privsは1、seccompは2だった。
- exportしたBPFは192 bytes / 24 instructions、SHA-256は
  `9382165a2839306b47868187ad837d0674a97cc3b6cb94bb59b3d9c21efd1c36`。
- 前後のhost core_patternは同一だった。

続いて対象を明示して、帳票・スキャンを一切読まない所有子processだけを意図的に異常終了させた。
子はexec後に上記の実効制限と変更拒否を再確認してから`os.abort()`を実行した。
親のwaitpid結果はwait status 6 / SIGABRTで、`WCOREDUMP`はfalseだった。
timeout時の終了対象もこのprobeが生成した子PIDだけに限定した。実行終了コードは0。
これはkernelの終了情報とsource-definedなhelper起動抑止条件の確認であり、
host全体の保存先を走査してダンプfile不在を証明したという意味ではない。

このprototypeではOffice/PDF/fontを読み込んでおらず、製品への組込み、固定image内の依存束縛、
正常なOffice互換性、全ABI/変更経路の閉鎖、正式な環境資格は未検証である。
従って本番gate、environmentQualified、productionQualifiedは変更しない。

### 捕捉BPFの独立再計算

同じBPFのbytes/SHAを製品外の `inspect_core_filter_bpf.py` に固定し、標準libraryだけの
classic-BPF interpreterで3954条件を再計算した（終了コード0、全条件一致）。
COREと他resource、上位resource bit、NULL/non-NULL new_limits、対象pidの違いを区別した。
native syscall番号0〜511ではCORE変更2経路以外をALLOW、x32番号と別architectureをKILLと判定した。
これは捕捉filterの命令解釈が意図した規則に一致する検査であり、実kernelへの再導入や
全syscall番号空間・新kernel/別ABI・Office互換性の実測ではない。製品コードと全体試験は変更しなかった。

### 製品組込み用の候補

全体試験の対象コードを固定したまま、製品外の `core_guard_candidate.py` に
`install_core_guard()` を実装した。Linux-only import、ABI/uid制約、bounded library/BPF読取、
失敗時の即時拒否、4件の実変更拒否probe、実効limit/seccomp/NNP検査と証拠出力を持つ。
候補SHA-256は `22fe032c53497bf0ea57c2ac4462144a1f2300a6933f147d747eb706c81eff92`。
hostの無入力nobody processで終了コード0、生成BPF/library SHAは上記prototypeと同一だった。
候補のRuff、Linux対象のMypyは成功した。初回にはClassVar未指定、変数再使用の型不一致、
Windowsを対象にしたLinux APIの型エラーがあり、前二者を修正し実対象OSを指定して再検査した。

固定runtime `ee8b87dbbffd6b0817e1346aaabeef744c14b9632ea206d7af903cc239ecb03c` の
Squashfsを読み取り専用で列挙し、`usr/lib/x86_64-linux-gnu/libseccomp.so.2` のリンク先が
root-ownedの `libseccomp.so.2.5.3`（125360 bytes）であることを確認した。
同fileをディスクへ展開せずSHA計算し、上記host probeのlibrary SHAと同一だった（終了コード0）。
nobodyでのimage直接読取はstoreの権限で拒否され、既存brokerと同じrootで指定imageだけを読んだ。
mount、展開先への書込、image変更、Office起動、製品環境の切替は行っていない。
候補はまだ製品workerへ反映しておらず、この検証を本番資格へ昇格させない。

## 製品への反映（候補検証後）

座標照合追加後のbackend fullが2157 passed / 1 skipped / 3 warnings、終了コード0で完了した後に反映した。
`renderer_wsl_worker.install_core_guard` は検証済みfilter SHAそのものを要求し、生成filterの違いを
未検証として拒否する。固定mount・network/resourceの検証後、Office/font入力解析前に導入する。
`renderer_glyph_worker.inner` も入力ZIP/PDF/fontを読む前に同じ処理を行い、vector helperはこれを継承する。

Linuxのroot brokerも入力byteをコピー・保持するため、両brokerの入力読取前に
明示したuid 0用のguardを導入した。隔離childはuid 65534を引き続き要求する。
systemd unitは `LimitCORE=1` で起動し、childは独立して変更拒否filterを導入する。
systemd経由の新processへ親のfilterが自動継承されるとは仮定しない。
host core_pattern、WSL全体の設定、runtime image、原本、スキャンを変更していない。

各新規応答に `coreDumpGuard` / `brokerCoreDumpGuard` を追加し、Windows側のOffice/glyph bridgeは
両方のpolicy、ABI、core limit、NNP、seccomp mode、filter SHA/bytes、変更拒否数、library SHA形式を検査する。
これはcurrent invocationの追加証拠であり、保存済みの旧reportへ後付けしない。
既存worker/code bundleの内容SHA照合と資格fingerprintにより、旧実装の資格を新実装へ流用しない。
この変更だけで環境全体を認定したり、ACTIVE/受付gateを開放したりはしない。

### 反映後の検証

- 関連unit（bridge、旧保存glyph/geometry証拠の読込、欠落/改変/boolean拒否、入力前の失敗）:
  **262 passed（1.88秒）**。
- 実WSLのlive/fixed Office変換とページ画像比較、glyph検証、vector検証:
  **3 passed**（継承検証の失敗1件を含むセット全体は108.59秒、終了コード1）。
  broker/child双方のguardを確認し、ページ数・画像・glyphの既存assertionを維持した。
- 製品workerを使用した無入力nobody probeの既存thread、exec子、所有SIGABRT子:
  修正後 **1 passed（1.81秒）**、終了コード0。実CORE変更はEPERM、実効limitは(1,1)、
  子のwait statusは6かつWCOREDUMP=false、host core_patternは不変。
- 対象Ruff成功。Linux対象のworker 2ファイルとWindows対象のbridge 2ファイルのMypy成功。
- `git diff --check` 成功。反映前worker SHAは
  `ddd0682df190c9911d2d8f446779f374b6b0bd64a67c30d47540828de3d940da`、
  反映後は `aadf50da45602d272941fc16805cb90bfddca36fb5010c26b243a62b9b287b66`。

初回unitの1件失敗は新証拠をdimensionObservation内へ誤って置いたfixture編集であり、
応答reportへ移して既存寸法assertionを保持した。初回継承試験はPython resource wrapperが
EPERMをValueErrorへ変換することをテストが想定していなかった。
実エラーを記録して原因を確認し、例外条件を緩めず、raw syscallの戻り値とerrnoを直接照合する試験へ修正した。

反映後のOCR契約scriptは **210 passed / 1 warning（44.64秒）**、終了コード0で完了した。
反映後のbackend fullは未実施であり、以降の環境認定接続を含む実装全体に対して実行する必要がある。
上記2157件のfullは反映前の結果である。frontendは今回変更していない。
既存OCR contractを維持し、mapping/physical identity、既存crop、provider/model/prompt/schemaは不変。
認識正解率・association mismatch等の実API測定は未実施。OpenAI 0/100、Google 0。
