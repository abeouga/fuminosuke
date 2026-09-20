# UNOの桁あふれと独立したrange sizeの観測

## 目的

Calc新geometryの本番化には、FODSの寸法プロパティがuint16で桁あふれしていないことを
FODS以外から確認する必要がある。小さい出力を近い整数へ戻すだけでは不十分である。
今回は製品コードや資格gateを変更せず、別の実測経路を検証した。

固定バージョンの[セルUNO実装](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/sc/source/ui/unoobj/cellsuno.cxx)では、
行高・列幅プロパティにmm100変換後のuint16 castがある一方、rangeのSizeはGetMMRectを使用する。
[GetMMRect](https://github.com/LibreOffice/core/blob/libreoffice-7.3.7.2/sc/source/core/data/documen3.cxx)は
元のtwip寸法を矩形へ累積してからmm100へ変換する。rangeのinclusive sizeと原点による丸め差を
従来の寸法プロパティと区別する。印刷のFillInfoグリッドと同じ計算だと解釈して置換しない。

## 専用環境への追加

承認済みのWSL依存導入範囲で、aptのsimulateを先に実行した。
`python3-uno=1:7.3.7-0ubuntu0.22.04.12` の1 packageのみを追加し、
既存packageの更新・削除は0。追加容量は714 kB、Office本体は7.3.7.2のまま。
既存のbuild scriptで別SHAのSquashfsを作成した。既存imageを上書きせず、
アプリ設定・既存版のruntime選択を切り替えていない。

- 新image: `/opt/llmocr-renderer-runtimes/ee8b87dbbffd6b0817e1346aaabeef744c14b9632ea206d7af903cc239ecb03c.squashfs`
- 622493696 bytes、gzip、builder SHA `c042e01bfb896bacb2c56c0e4dcb8a4da7792f372c3061fa430d2a696a6ee5c0`。
- 旧image `9b513e12512fc7dfe9cb71eee69649d002b02bdd78153c8669d54299466a1e01` ではUNO import不在。
- 新imageでは `/usr/lib/python3/dist-packages/uno.py` とcomponent contextの取得が成立。
  uid65534、read-only runtime、loのみ、Windows文書mountなしで確認した。

このimport probeはOffice文書を開いておらず、正式な環境資格ではない。

## 隔離内の実測

repo外のworkerコピーへ診断関数を追加し、元と同じcgroup・network・worker snapshot・
owner leaseを使用した。UNO pipeも同じ隔離内だけで使用し、ネットワーク接続を開けていない。
macro禁止/更新禁止/Hiddenで人工メモリ内Calc文書を作り、行高と列幅へ同じ寸法を指定した。
続けて検証コピーの印刷XLSXをReadOnlyで開き、明示print areaの全行列を取得した。
最後に従来のCLIで同じ所有入力をFODSへ出力し比較した。この診断は同一ファイルの別importであり、
単一の読み込みinstanceに束縛した製品証拠の保存処理とは区別する。

| 指定twip | 指定mm100 | 行高/列幅プロパティmm100 | range Sizeの幅/高さmm100 |
| --- | --- | --- | --- |
| 232 | 409 | 409 | 410 |
| 390 | 688 | 688 | 689 |
| 37152 | 65532 | 65532 | 65533 |
| 37153 | 65534 | 65534 | 65535 |
| 37154 | 65536 | 0 | 65537 |
| 40000 | 70556 | 5020 | 70557 |
| 65534 | 115595 | 50059 | 115596 |
| 65535 | 115596 | 50060 | 115597 |

実帳票は37列（非表示1列）・102行。最大range sizeは列712、行10796 mm100。
可視寸法についてFODSから復元したtwipのmm100とUNO寸法プロパティが全件一致し、
range sizeはすべて1超65535以下。非表示列はFODS寸法0、range sizeは1以下。
range sizeから寸法プロパティを引いた差は列0〜1、行0〜2であり、常に1ではない。
この差を固定offsetとして保存座標へ加えてはいけない。

## 保存証拠・検証

診断は終了コード0。元の印刷XLSXは実行前後byte一致。実DB・layout/config・スキャンは変更0。
製品サービスのworker選択は診断process内でのみrepo外コピーへ向けた。実アプリ設定は不変。

- 出力: `C:/Users/hanam/AppData/Local/Temp/llmocr-uno-dimensions-2cwkqkux/report.json`
- report SHA: `e74f706a37509769e954e0bcb86918511deed43da57ae2dbbd055e876f466e51`
- 入力印刷XLSX SHA: `a3df374f120a5db6ef0853f2dfb02dd8dd706ceb6afb7495c2bd68015b606d62`
- 診断worker SHA: `000582da3f9fe45edef429099debf9fd9fc938165a98ee6127669404e8309b2b`
- repo外診断: `C:/Users/hanam/.codex/tmp/paged-vector-inspection-20260913/uno-dimensions/renderer_wsl_worker.py`
  と `probe_wsl_uno_availability.py`。

既存の2044件fullはこの新診断workerや新runtimeの資格証明には使用しない。
製品コード未変更のため今回の新規backend回帰試験は実行していない。

## 次の境界

原点・非表示・末端・異常型などを含むrange sizeの検証条件を固定し、FODSと独立観測を
同じ原本・同じ読み込み・同じ環境へ束縛する製品実装が必要。
全有効値の証明や他環境への一般化は今回の8点のnative実測だけでは主張しない。
保存証拠のSHA/実装identity/所有権と失効を検査し、旧evidenceへ観測を後付けしない。
これは非overflow検証の実装根拠であり、環境資格・文字可視性・印刷誤差の本番基準・
送信前ink clearance・ACTIVE/受付成功を代替しない。OpenAI 0/100、Google 0。
