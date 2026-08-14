# design.md — lab-vlm-cascade

本書がリポジトリ設計の正本。実装との矛盾が生じた場合は本書を改訂してから実装を変更する。

## 0. 目的

曖昧クラスを含む画像分類（例: inside と food の境界事例）を、コスト・可用性・ルール変更即応性の3制約下で運用する「カスケード＋判定層＋監査ループ」アーキテクチャの一般実装と数値検証。

- 公開ポートフォリオであると同時に、GCP実務へ部品単位で転用可能なリファレンス実装とする
- 設計パラメータは事前に固定せず、定義済みの分析で決定する（§8 decision matrix）。同一分析を実データで再実行できる移植性を持つ
- データは公開の Yelp Open Dataset のみ。数値はすべて本リポジトリで実測したものだけを掲載する
- 特定企業の事例・数値・環境情報は一切含めない

## 1. アーキテクチャ

```
photos ─▶ Stage1: encoder zero-shot ─▶ Stage2: decision layer ─▶ 確定 (clear / rule_resolved)
              │ probs, margin              │ 低margin・指定confusion pair のみ
              ▼                            ▼
        predictions (parquet)        Stage3: VLM escalation ─▶ 確定 or irreducible
                                           │
offline: 監査ループ (cleanlab / VLM合議 / κ定点観測) ◀──┘
single source: rulebook.md (versioned) ─▶ クラスプロンプト / 判定表 / VLMプロンプトを生成
```

- Stage1: 画像エンコーダのゼロショット分類。クラスプロンプトは rulebook から生成
- Stage2: しきい値＋優先順位ルールの決定的判定層。モデルなし、コードとconfigのみ
- Stage3: 委譲スライスのみ VLM（Gemini）で構造化出力判定。自己一致または2モデル一致で確定、不一致は irreducible
- 監査: バッチでラベル・予測を検品（cleanlab、VLM合議、gold κ）

## 2. データ

- Yelp Open Dataset photos: 約20万枚、label ∈ {food, drink, menu, inside, outside}
- 取得は公式ページから各自ダウンロード。**画像・生データはリポジトリ非同梱**（scripts/ に手順とチェックサム検証のみ）。README等にYelp画像を掲載しない。同梱の利用規約PDFの確認を M0 の最初の工程とする
- サンプリング: work set = 層化 25,000枚 / eval set = 凍結 2,000枚（クラス別400目安＋natural分布スライス併設）。eval は photo_id リストのハッシュで凍結し、W&B reference artifact で版管理
- ラベルの扱い: Yelp付与ラベルは「来歴の混在した既存ラベル」とみなす（実務で頻出する状況の一般形）。gold は M4 で目視スポットチェックした部分集合のみとする

## 3. 技術スタックと実務転用マップ

| 本リポジトリ | GCP実務での対応物 |
|---|---|
| uv + Python 3.12 | 同一 |
| OpenCLIP / SigLIP (Hugging Face) | 同一コンテナを Vertex AI Custom Job で実行 |
| DuckDB + parquet 三層 (raw / core / marts) | BigQuery 三層 (ds_raw / ds_core / ds_marts) |
| Gemini API 直呼び | Vertex AI Gemini Batch inference（50%割引） |
| Tracker fan-out: W&B Free ＋ Vertex AI Experiments へ二重送出 | Vertex AI Experiments（承認が出れば W&B を追加送出） |
| Optuna（本探索）＋ managed Vizier（無料枠内のパリティ検証1本） | Vertex AI Vizier（後退線 Optuna） |
| cleanlab | 同一 |
| reports/ の md 自動生成 | 同一（GitHub共有・PRレビュー） |

**runs正本パターン（fan-out）**: 実験記録の正本はローカル parquet の runs テーブルに置き、Tracker アダプタ（config: `tracking: [wandb, vertex]`、任意の組合せ・空も可）が各バックエンドへ投影する。実験ごとにバックエンドを排他切替すると履歴が分断され横断比較が壊れるため、**切替ではなく二重送出**とする。どのバックエンドが不通でも run 本体は完走する。同一 run を W&B と Vertex Experiments の両UIで比較できること自体が、トラッカー可搬性の実証＝本リポジトリの主張の一つ。

**Vertex 利用条件**: Vertex AI Experiments は run 自体に追加課金がなくメタデータ保存の従量のみ。Vertex AI TensorBoard はログ保存 $10/GiB/月 の課金があるため本リポジトリでは**有効化しない**（学習曲線は W&B と素の TensorBoard で代替）。managed Vizier は無料枠（月100トライアル）内でのみ使用し、超過する探索は Optuna（または OSS 版 google-vizier）で行う。

**W&B 利用条件**: Free プランは個人プロジェクト限定のため、本リポジトリ（個人ポートフォリオ）に限定して使用する。W&B には画像・生データをアップロードしない（メトリクス・小テーブル・reference artifact のみ）。ストレージ 5GB/月の制限内に収まり、「画像はストレージ参照のみ」という実務パターンと同型になる。

## 4. rulebook 仕様

- `rulebook.md` の構成: (a) 分類意図＝閲覧者がどのタブにその写真を期待するか (b) クラス定義 (c) 曖昧ペアごとの優先順位ルール (d) 境界事例の文例
- 版管理: semver（`rulebook_version`）。runs・reports に必ず刻印する
- 生成: `scripts/gen_from_rulebook.py` が Stage1 クラスプロンプト・Stage2 判定表（YAML）・Stage3 プロンプトを rulebook から生成する。手書きの重複定義を禁止（単一ソース）
- 判定出力: `primary`, `secondary`（任意）, `flag ∈ {clear, rule_resolved, irreducible}`

## 5. スキーマ（DuckDB / parquet、BigQuery互換型のみ使用）

- `labels(photo_id, label_source, label, provenance, ts)`
- `predictions(run_id, photo_id, stage, probs_json, margin, primary, secondary, flag, model_id, cost_tokens, ts)`
- `runs(run_id, git_sha, rulebook_version, eval_set_id, model_id, config_json, metrics_json, wandb_url, ts)`
- `eval_sets(eval_set_id, photo_ids_hash, definition, created_at)`

## 6. マイルストーン

### M0 セットアップ
uv環境、データ取得と規約確認、サンプリングとeval凍結、三層テーブル初期化、Tracker アダプタ実装と W&B project／GCP プロジェクト（Vertex AI Experiments）の初期化、`tracking` config の縮退動作テスト（wandb のみ／vertex のみ／none で完走すること）、Gemini の現行モデルIDを確認して config にピン留め（"latest" 系エイリアス禁止）。
完了条件: `reports/m0_setup.md`（データ統計・クラス分布・eval定義）

### M1 ゼロショットベースライン
エンコーダ2種以上でクラス別 P/R/F1、混同行列、margin 分布。
検証命題: 混同ペア（inside×food 等）が定量的に特定できる。
完了条件: `reports/m1_baseline.md`＋W&B run

### M2 判定層と risk–coverage
risk–coverage 曲線からクラス別・ペア別しきい値を設計。しきい値探索の本体は Optuna（キャッシュ済み確率に対する純関数評価、数千トライアル）。加えて managed Vizier で同一探索空間のスタディを**無料枠内（≤100トライアル）で1本**実行し、Optuna との到達解を突き合わせて work-parity を記録する。さらに conformal prediction（split conformal / RAPS）による予測集合ベースの委譲規則——集合サイズ=1なら確定、≥2なら委譲＋secondary付与——をしきい値方式と比較し、**層別（クラス別・margin帯別）の被覆充足**まで検証する（D10。周辺被覆のみの保証は曖昧例の層で崩れうるため）。優先順位ルール v1.0 を判定層に実装しユニットテストを付ける。
検証命題: カバレッジを X% 委譲すると残存誤り率が Y% 下がる、の定量化。
完了条件: `reports/m2_decision_layer.md`（risk–coverage 図、採用しきい値、委譲率）

### M3 カスケード vs 全量VLM
委譲スライスのみ Gemini 構造化出力で判定し、全量VLM（同一eval）と精度・コストを実測比較。呼び出しは config 上限内（既定: eval 2,000枚＋委譲分、MAX_ESCALATION=5,000）。
あわせて**プロンプト4条件アブレーション**を委譲スライスで実施し、D4の下位判断「Stage1確率をStage3に渡すか」を実測で決める:
- A: 縮小画像＋ペア限定ルールのみ（既定案）
- B: A＋margin帯ラベル（low / mid のみ、数値なし）
- C: A＋Stage1全確率
- D: ペア限定なしの5クラス判定＋全確率
測定: 精度、2モデル一致率、irreducible率、コスト。Cで一致率だけ上がり精度が伸びない場合はアンカリング（合議の独立性喪失）の兆候としてAを維持し、Cで精度が有意に上がる場合のみ確率注入を採用する。
検証命題: カスケードが全量VLM比コスト Z% で同等精度圏に入る。
完了条件: `reports/m3_cascade_vs_vlm.md`（精度・コスト表、1万枚あたり試算、アブレーション表）

### M4 ラベル監査
cleanlab（out-of-sample 予測確率）＋VLM 2系合議で既存ラベルの疑義を検出し、上位N件を目視スポットチェックして的中率（precision@N）を測定。gold 部分集合で人×VLM の κ を算出。
検証命題: 既存ラベルの誤り率推定と検出的中率。
完了条件: `reports/m4_label_audit.md`

### M5 ルール変更即応
rulebook を v1.1 に改版（例: inside×food の優先規則変更）→ 再学習なしで判定表・プロンプトを再生成 → eval 再実行 → ラベルフリップ数と影響差分レポートを自動生成。
検証命題: ルール変更が判定層・プロンプト層だけで即日反映できる。
完了条件: `reports/m5_rulebook_change.md`（版間diff、フリップ表）

### M6（任意）蒸留
VLM合議の soft label で linear probe → LP-FT、WiSE-FT 補間。委譲率の低下を実測。
完了条件: `reports/m6_distill.md`

### M7（任意）self-improving rulebook loop
M4の監査不一致集合を訓練信号に、GEPA / ProTeGi 型のテキスト最適化で rulebook 改訂案を自動生成 → frozen eval＋**holdout rotation**（評価分割を回転させ過適合を検出）で評価 → 影響差分レポート → **人の承認ゲート** → M5 の再評価パイプラインへ接続する閉ループを実装する。境界事例集の節は ACE 流の増分キュレーション（丸ごと書き換え禁止、構造化差分更新のみ）で維持する。副題として、Stage3 昇格サブワークフローの予算制約付き構造探索（単発VQA／自己一致／2モデル一致／OCRツール併用／記述→ルール適用の候補空間を AFlow 流に探索）で D4 を自動化する。

**承認ゲートの実装（PRを承認プリミティブとする）**: 改訂案はブランチ `rulebook/vX.Y-rc` ＋自動PR（本文＝影響差分レポート）として提出する。CIが gen_from_rulebook → frozen eval＋holdout rotation を再実行し、結果をPRコメント＋status check化（基準未達はマージ不能）。`rulebook.md` に CODEOWNERS＋required approving review（保護ブランチ、publicリポは無料）を設定し、**マージ＝承認確定**とする。蒸留・一括再ラベル等の高コスト下流処理は Actions environments の required reviewers で二段目ゲートを設ける。Slack は通知・催促・議論面（`/github subscribe <owner>/<repo> pulls reviews`、scheduled reminders）に限定し、承認行為は置かない（diffの見えない場所での承認と監査痕跡の分散を避ける）。

**頻度制御（PR洪水の防止）**: ①改訂案生成は週次監査バッチに従属させ、イベント駆動にしない。②発火条件: ペア別不一致の蓄積が閾値超のときのみ生成を起動。③改善ゲート: holdout rotationで最小改善幅（CI下限>0）を満たさない候補は**PR化せず自動棄却**し、runsにログのみ残す。④レート上限: 1サイクル最大1PR、かつ `max_open_prs=1`（未処理の自動PRがある間は新規PRを作らない）。頻度は成り行きでなく発火閾値で制御するガバナンス変数とし、PR発生頻度・却下率・レビュー所要時間をD9の計測項目に含める。

**変更の3層ポリシー**: Tier 1＝境界事例集への追記（最頻・最軽。週次PRに同梱）／Tier 2＝ルールの新規追加（minor bump、同梱可）／Tier 3＝既存ルールの修正・優先順位変更（既存判定のフリップ発生源。**単独PR＋フリップ表必須**、フリップ率>基準で目視サンプルレビュー。D8と接続）。

**撤退条件**: 却下率の高止まり、またはレビュー負担が改善価値を上回ると計測された場合はループを停止し、手動M5運用（人が起点、機械は影響分析のみ）へ戻す。この条件をD9の採否基準に含める。

検証命題: 自動改訂が同一入力に対する人手改訂と比べて、改善幅・コスト・安定性で優位か（D9）。
完了条件: `reports/m7_self_improving_loop.md`（人手改訂との対照、holdout間の安定性、探索コスト、承認ゲート通過率、PR頻度・却下率）

## 7. 指標定義

- selective risk / coverage: 判定層が確定した集合における誤り率と、その集合の被覆率
- escalation rate: Stage3 へ委譲した割合
- cost/image: 入力トークン＝258（両辺≤384pxへリサイズ）＋プロンプト分。モデル単価×トークンで算出し、レポートに必ず概算を記載
- macro-F1 / クラス別 P/R/F1、混同行列
- κ: gold 部分集合における人×VLM の一致度（監査モデルの較正指標）

## 8. 設計判断を決める分析（decision matrix）

設計パラメータは事前に決め打ちしない。以下の各判断は、対応する分析の結果と判断基準によって決定し、reports/ に**意思決定ログ**（基準値・実測値・採否）として記録する。運用は二段構えとする: まず Yelp データで分析の方法論と基準を確立し（各M）、次に**同一の分析スクリプトを実データ（業務環境の予測ログ・ラベル）で再実行して本番の設計判断を下す**。この「方法論は公開データで実証済み、判断は自データの数値で行う」構図そのものが、アーキテクチャ提案の説得の中核になる。

| ID | 設計判断 | 決める分析 | 判断基準の型 | Yelp側 | 実データ側 |
|---|---|---|---|---|---|
| D1 | カスケード採否（vs 単独強化 / 全量VLM） | margin分布の偏り、risk–coverage、コスト分岐点 | 委譲率≤X%で目標精度、全量VLM比コスト<Y% | M1–M3 | 予測ログに同一スクリプト |
| D2 | しきい値の粒度（大域/クラス別/ペア別） | 粒度別の risk–coverage 比較 | 同カバレッジでの誤り削減幅 > 保守コスト | M2 | 同左 |
| D3 | 較正の要否 | reliability diagram、ECE、高確信帯の誤り率 | ECE>基準、または高確信誤りが残存誤りの過半 | M2 | 同左 |
| D4 | Stage3構成（単発/自己一致/2モデル一致、確率注入の有無） | 委譲スライスでの一致率×正解率×コスト、プロンプト4条件アブレーション | 精度とコストのパレート比較。確率注入は精度向上時のみ採用（一致率のみ上昇なら不採用） | M3 | 昇格ログ |
| D5 | irreducible境界（三値フラグ閾値） | VLM合議一致度と人間κ（gold二重判定）の突合 | 不一致帯の人間κ<基準なら分布保持が妥当 | M4 | gold二重判定 |
| D6 | 既存ラベルの再利用可否 | cleanlabスコア分布、来歴別疑義率、スポットチェック的中率 | 来歴別疑義率<基準の部分集合のみ再利用 | M4（Yelpラベルを来歴混在の代役に） | ラベル来歴検証と併走 |
| D7 | 評価セット規模 | Clopper–Pearson CI幅、ブートストラップ分散 | クラス別±CI目標を満たす最小n | M0で設計、M1で実測検証 | 同一計算 |
| D8 | ルール変更ゲート | 版間フリップ率、ペア別影響 | フリップ率>基準で人手レビュー必須 | M5 | rulebook運用 |
| D9 | rulebook自動最適化の採否 | 同一不一致集合を入力にした自動改訂 vs 人手改訂の対照比較、PR頻度・却下率・レビュー所要時間の計測 | 人手比の改善幅/コスト、holdout rotation間の安定性、承認ゲート通過率。却下率高止まり・レビュー負担>改善価値なら撤退（手動M5へ復帰） | M7 | 監査ループに接続 |
| D10 | 委譲規則のconformal化の採否 | 予測集合ベース委譲 vs しきい値委譲の比較、層別被覆検証 | 同一被覆での委譲率減、全層で被覆充足 | M2 | 同左 |

**移植性要件**: 分析コードは `analysis/` に判断ID付きで置く。SQLはDuckDB/BigQueryの両方で動く書き方に限定し（方言依存の関数を避ける）、接続とテーブル名はconfig注入とする。これにより実データ側では接続先の差し替えだけで同一分析が走る。

**自己改善ループの統制（D9 / M7）**: 自動最適化は次の4つの失敗モードを前提に統制する。(a) frozen evalへの過適合 — holdout rotationを必須とし、分割間で改善が再現しない改訂は棄却。(b) Goodhart化 — 指標だけ上がる無意味な規則を防ぐため、rulebook改訂は必ず人の承認ゲートを通し、意味的妥当性をレビューする。(c) 再現性 — 最適化の軌跡・シード・使用モデル版をrunsに記録する。(d) 証拠の偏り — ワークフロー自動最適化の公表成果は推論・コーディング課題中心で知覚分類での証拠は薄いため、採否は本リポジトリでの実測（D9）でのみ判断する。なお、パイプラインのコード自体をエージェントが書き換える自動ML工学系（AIDE等）は、統制・監査可能性と衝突するため検討のうえ不採用とする。

## 9. 非スコープ

リアルタイム推論、Web UI、Yelp データの再配布・画像表示、特定企業事例への言及。README の最終整備（英語版含む）は M5 完了後に別途判断する。
