# T2修正実装：検証済み入力の共有と処理境界の照合

2026-10-12完了。**T2中央値は同条件の旧版751.75msから472.96msへ37.09%短縮し、固定5範囲すべてが500ms以下となった。** 完全manual意味投影5/5、既存83境界、追加48境界、旧30保存応答の再生を通過した。これは製品外のローカル受入であり、次はAstra / XHighによる修正審査である。有料フェーズ25はまだ開始しない。

対象checkoutは `C:/Users/hanam/Desktop/llm-ocr-deploy`。実装・検証は `D:/llm-ocr-archive/template-ai/20261011-phase24-repair` に限定した。作業開始時のディレクトリ名を保持し、日付が変わってからの完了を本書へ記録する。開発工程の指定はGPT-6.1 Sol / High。実API使用0回、共有台帳366/496、残130回。実際のチャットモデル設定を検証した意味ではない。

## 修正した処理

[修正設計](template-ai-t2-revision-design-2026-10-11.md)と[固定した計画](contracts/template-analysis-v7/t2-revision-plan.json)に基づいた。計画と[元のPhase24結果](contracts/template-analysis-v7/phase24-result.json)は変更せず、[修正実装結果](contracts/template-analysis-v7/t2-repair-result.json)を別に保存した。

| 外部コピー内の変更ファイル | 変更内容 |
| --- | --- |
| `runtime/verified_snapshot.py`（追加） | factoryだけが発行する不変入力。新規geometry生成1回、復元snapshotはExpected/画像/policy/digestを照合しgeometryを1回再生成して完全比較。run/processのproofを束縛 |
| `runtime/expand.py` | 検証済み入力型を共通resolverへ明示的に受け入れる。旧raw入口の完全検査を残し、意味解決・compilerを共有 |
| `evidence_context.py` | run内で検証済みsnapshotを共有。現在状態の内容検査と実装照合を分離し、別contextへの復元は再検証。pack、catalog、防御コピーを維持 |
| `run_guard.py`（追加） | 同期区間の開始/終了に実装manifestを内容照合。ファイル集合、追加/削除、同サイズ・同mtime変更、moduleの由来/差替えを検査 |
| `selected_adapter.py` | 現在性・保留claims・依存閉包・共通compile/manual投影の検査を保持し、終端照合前に結果を返さない。例外時も区間を閉じる |
| `transport.py` | 予約/送信前と応答受信後で区間を分ける。連続実行入口`execute()`を追加。Responses1回は2区間/4照合、Decisions2段は3区間/6照合 |

factory proofはlegacy snapshotの偽装subclassではなく別の内部型である。公開hashや`trusted=true`を根拠に検証を省略しない。実装manifestは旧387のruntimeソース/契約に新入力型1ファイルを追加し、外部5モジュールを合わせた393ファイル。geometryアルゴリズム、model/prompt/schema、provider画像/crop、factor生成器、候補/送信上限は変更していない。

別runの同じ入力でもplanを流用できないよう、内部pinへ実装proofとrun nonceを加えた。v7のモデル用出力DTOと候補IDの生成規則は維持する。旧snapshot・応答・既存graphを移行または書換えしない。

既存の`first()/second()`を個別に呼ぶ入口も保持する。この場合は第1段後の呼出し元への復帰を追加の待機境界として扱うため、二段で4区間/8照合となる。連続の`execute()`では中間の復帰を挟まず設計どおり3区間となる。途中停止・取消・古いreceipt・実装変更で追加送信せず、予約は払い戻さない。

compilerが遅延importしていた3モジュールは、初回のrun内で内容照合後に初期化し、以後の区間でmodule差替えや未宣言の遅延ロードを拒否する。初期化費用は計時内に含めた。ファイル集合の検査にはディレクトリ走査時の情報を使用し、不要な重複statを減らした。mtimeだけのcacheへ置き換えていない。実装照合に失敗したleaseは永久失効させ、ファイルを戻しても同じguard/snapshotは復活しない。

## 固定比較の結果

| 範囲 | 今回の旧版中央値 | 修正版中央値 | 短縮率 |
| --- | ---: | ---: | ---: |
| T1 | 504.26ms | 306.03ms | 39.31% |
| T2 | 751.75ms | 472.96ms | 37.09% |
| T3 | 364.25ms | 188.51ms | 48.25% |
| H1 | 388.76ms | 197.38ms | 49.23% |
| H2 | 368.28ms | 191.03ms | 48.13% |

T2修正版の3回は **463.64 / 472.96 / 482.30ms**。初回も含めて全て保存した。400ms前後という設計上の目安には届いていないが、事前の合格条件は500msであり変更していない。中央値の余裕は約27ms、3標本だけからp95や実運用の上限を保証しない。

旧版・修正版は別processで実行し、反復1/3は旧→新、反復2は新→旧、各processでT1/T2/T3/H1/H2の順に計測した。毎回入力を読み、context/planを新規生成した。新proof、ファイル集合/内容照合、索引、初期化費用は計時内。oracle読込/選択・採点・追加no-opは外側で、wire組立・実API/モデル・UI・DB保存は含まない。Python import済みという従来の条件を維持し、OSキャッシュは除去していない。

元のPhase24のT2=716.81ms不合格、設計診断の706.24ms未達は履歴として保持する。今回の短縮率は同じ比較工程で測った751.75msを分母とし、過去の値と都合よく組み合わせていない。計装profileの時間を無計装の比較へ加算していない。

最終profileで全5範囲についてgeometry生成1回、実装照合2回、現在性検査4回、pack構築1回、選択後の共通resolver/compiler/manual各1回、schema契約検査2回を確認した。選択後のpack再構築はなく、異なる2schemaの検査を計時外へ移していない。T2の内部`CommandCompiler.compile_operations`は従来どおり意味compileとmanual投影の2回である。

## 品質・失効条件と検証の限界

名前・alias・意味共有・親・mark・行/列・relationまで含む完全manual投影と既存graphの全ID/path込みno-op、physical identityを5/5で保持した。値と名称は保存oracleから供給し、新しいAIの精度を測定していない。

既存83境界と変形例を、修正版の入口で全件実行した。追加48条件では、自分で整合するdigestを付けた偽候補、別Expected、並び変更、別runのproof/plan、mutable alias、実画像bytes、同値手動facetのcompile後変更、pack内容変更を確認した。実装の同サイズ・同mtime変更/追加/削除は隔離コピーで試し、返却前・送信前・API待機中・段間で拒否した。ファイルを戻しても同じlease/guard/snapshotを復活させない6条件も追加した。取消時に新たな区間/予約を開始しない条件も確認した。

旧30保存応答は変更後のruntimeを読み込む別processで再生し、26受理・4拒否、旧採点・全時間・usage、受理26件のmanual投影と既存graph完全no-opを保持した。旧Phase19の411hashと30requestも照合した。旧runtimeだけを再生した回帰確認ではない。

主な検証は外部`boundary_acceptance.py`、`repair_boundary_acceptance.py`、`legacy_replay.py`、`paired_acceptance.py`、`measure_worker.py new profile`、終了監査と成果物照合、および対象repoの`git diff --check`。deployのbackend仮想環境を`-B`で使用した。境界・保存応答再生を先に行い、最終の固定時間比較を後にした。

開発途中の単発492.55ms等は採否に使用していない。初期の遅延import拒否と、走査の重複stat/追加の現在性検査が残っていた592.43msの単発結果も外部の開発記録に残した。初回受入はT2=464.67msで通過したが、点検で実装変更後のlease復活という欠落が判明した。初回ソース/lock/全30計測/結果は`development-revision1`へhash付きで保持し、失効処理と6回帰条件を追加した別実装revision2を、同じ全条件・固定順序で再受入した。今回の最終値はrevision2の3回全てで、同じ実装の測定を繰り返して良い値を選んだ結果ではない。初回削除や閾値変更はない。

製品コード/UIを変更していないため、全件unit、frontend build、サーバー起動、実マイクは実行していない。実endpointの受理、実DB transactionの競合、保存/undo、画面到達までの速度、実音声の認識品質は今回の受入外である。

## 保持・次工程

repo変更は本書、修正結果JSON、AGENTS/Safety Contract/v7索引の追記に限定した。製品ソース・両repoのHEAD・両通常DB全37表/schema・設定・業務863ファイル/781 artifact・旧checkout・Phase11〜24と修正設計の証拠を保持監査で照合する。既存OCR contractを維持したとの判断は、この不変性確認と意味/physical/no-opの受入による。製品全体の現在動作を実機確認した主張ではない。

**次はAstra / XHigh・API0の24修正審査。** ローカル受入は通過したが、独立した修正審査は未実施。フェーズ25の最大20送信は未予約・未消費で、審査受入と次工程の指示まで開始しない。通常未移行/無効を継続する。
