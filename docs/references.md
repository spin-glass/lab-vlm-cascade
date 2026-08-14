# references.md — カスケード設計の根拠文献

design.md および reports/ で数値・設計主張をする際の引用元。各項目に「支える主張」を付す。

## 1. 系譜・原型

- **[VJ01]** Viola, P. & Jones, M. "Rapid Object Detection using a Boosted Cascade of Simple Features." CVPR 2001. DOI: 10.1109/CVPR.2001.990517 / PDF: https://www.merl.com/publications/docs/TR2004-043.pdf
  — 主張: カスケードの原型。単純な分類器を複雑さの昇順に直列化し、大多数（背景）を初段で早期棄却して計算を難例に集中する構成。

## 2. コスト効率の実証

- **[Fru23]** Chen, Zaharia & Zou. "FrugalGPT." arXiv:2305.05176. https://arxiv.org/abs/2305.05176
  — 主張: カスケードで最良単一モデル同等性能を最大98%コスト減、または同コストで+4%精度。
- **[ABC24]** Kolawole et al. "Agreement-Based Cascading for Efficient Inference." TMLR 2024. https://arxiv.org/abs/2407.02348
  — 主張: 複数モデルの合意を委譲信号に使う変種（Stage3の2モデル一致確定の親戚）。
- **[Route24]** Ong et al. "RouteLLM." arXiv:2406.18665. https://arxiv.org/abs/2406.18665
  — 主張: ルーティング（入口で1回の並列分岐）との対比。カスケードは前段出力を委譲判断に使う直列構成。

## 3. 委譲規則と理論（selective prediction）

- **[Chow70]** Chow, C.K. "On Optimum Recognition Error and Reject Tradeoff." IEEE Trans. Information Theory, 16:41–46, 1970.（1957年論文が原典）
  — 主張: reject option（棄権つき分類）の原典。誤り率と棄却率のトレードオフ。
- **[EW10]** El-Yaniv & Wiener. "On the Foundations of Noise-free Selective Classification." JMLR 11:1605–1641, 2010.
  — 主張: selective classification の基礎理論（risk–coverage の枠組み）。
- **[GE17]** Geifman & El-Yaniv. "Selective Classification for Deep Neural Networks." NeurIPS 2017. https://arxiv.org/abs/1705.08500
  — 主張: 学習済みNNに目標リスクを保証する棄却規則を後付けできる。例: ImageNet top-5 誤り2%保証をカバレッジ約60%で達成。M2のしきい値設計の理論枠。
- **[Jit23]** Jitkrittum et al. "When Does Confidence-Based Cascade Deferral Suffice?" NeurIPS 2023. https://proceedings.neurips.cc/paper_files/paper/2023/file/1f09e1ee5035a4c3fe38a5681cae5815-Paper-Conference.pdf
  — 主張: 確信度ベース委譲（最大確率・エントロピー）が十分な条件と、後段の得意領域がずれる場合の限界。ペア別しきい値の根拠。
- **[CP-Intro23]** Angelopoulos & Bates. "Conformal Prediction: A Gentle Introduction." Foundations and Trends in Machine Learning 16(4):494–591, 2023.
  — 主張: 分布仮定なし・有限サンプルの被覆保証を任意の学習済み分類器に後付けできる枠組みの入門。委譲規則の保証付き化（D10）の基礎。
- **[RAPS21]** Angelopoulos, Bates, Malik & Jordan. "Uncertainty Sets for Image Classifiers using Conformal Prediction." ICLR 2021.
  — 主張: 画像分類向けの予測集合構成（RAPS）。集合サイズ=1で確定・≥2で委譲＋secondary付与という保証付き委譲規則の道具。注意: 保証は周辺被覆であり、曖昧・困難な入力の層では被覆が崩れる報告があるため層別検証が必須。

## 4. 較正（前段の過信対策）

- **[Guo17]** Guo et al. "On Calibration of Modern Neural Networks." ICML 2017. https://arxiv.org/abs/1706.04599
  — 主張: 現代NNは過信傾向で較正が崩れており、temperature scaling（1パラメータ後処理）が大半のデータセットで有効。「高確信の誤りが委譲されず素通りする」失敗モードの根拠。
- **[Gate25]** Rabanser et al. "Gatekeeper: Improving Model Cascades Through Confidence Tuning." arXiv:2502.19335. https://arxiv.org/abs/2502.19335
  — 主張: 前段を「誤答時に自信が下がる」よう調整して委譲性能を改善（委譲特化の較正）。

## 5. 人間終端への拡張（learning to defer）

- **[Mad18]** Madras, Pitassi & Zemel. "Predict Responsibly: Improving Fairness and Accuracy by Learning to Defer." NeurIPS 2018. https://arxiv.org/abs/1711.06664
  — 主張: 人間へ委譲する学習枠組み（L2D）の導入。
- **[MS20]** Mozannar & Sontag. "Consistent Estimators for Learning to Defer to an Expert." ICML 2020. https://arxiv.org/abs/2006.01862
  — 主張: 分類器＋rejector の2関数構成に一貫サロゲート損失を与えた理論的基礎。irreducible→人手キューの定式化に対応。
- **[CasHAI25]** "Cascaded Language Models for Cost-effective Human-AI Decision-Making." arXiv:2506.11887. https://arxiv.org/abs/2506.11887
  — 主張: モデルカスケードの終端に人間を置く構成のコスト効率。「既存の閾値＋人手レビュー運用＝1段カスケード＋人間終端」という再解釈の裏付け。

## 6. 失敗モードと監査

- **[CL21]** Northcutt, Jiang & Chuang. "Confident Learning." JAIR 70:1373–1411, 2021. 実装: https://docs.cleanlab.ai/
  — 主張: out-of-sample 予測確率からラベル誤りを理論保証つきで推定。高確信帯を含む監査サンプリングの道具。
- **[NAM21]** Northcutt, Athalye & Mueller. "Pervasive Label Errors in Test Sets." NeurIPS 2021 D&B. https://arxiv.org/abs/2103.14749
  — 主張: 主要10ベンチマークのテストセットに平均3.3%以上の誤ラベル。定期監査が標準実務である根拠。

## 7. 分析駆動の設計判断（decision matrix の根拠）

- **[MLTest17]** Breck, Cai, Nielsen, Salib & Sculley. "The ML Test Score: A Rubric for ML Production Readiness and Technical Debt Reduction." IEEE Big Data 2017. https://research.google/pubs/the-ml-test-score-a-rubric-for-ml-production-readiness-and-technical-debt-reduction/
  — 主張: 本番ML実務では28項目のテスト・監視を定義し点数化して判断する。「実装前に測るべき項目を明文化し、測定で判断する」運用の実務標準。
- **[Casc21]** Sambasivan et al. "'Everyone wants to do the model work, not the data work': Data Cascades in High-Stakes AI." CHI 2021. DOI: 10.1145/3411764.3445518
  — 主張: データ品質の軽視が下流に複利で波及する Data Cascades は実務者の92%が経験。ラベル品質分析（D6）をモデル改善より先行させる根拠。
- **[Power20]** Card et al. "With Little Power Comes Great Responsibility." EMNLP 2020. https://arxiv.org/abs/2010.06595
  — 主張: 検定力不足の実験が蔓延しており、小さいテストセットではSOTA比較の大半が検出力不足になる。評価セット規模（D7）を検定力・CI幅から設計する根拠。
- **[CP34]** Clopper & Pearson. "The Use of Confidence or Fiducial Limits Illustrated in the Case of the Binomial." Biometrika 26(4):404–413, 1934.
  — 主張: 二項比率の正確信頼区間。ゴールドセットのクラス別サンプル数とCI幅の対応計算（D7）の原典。
- **[ECE15]** Naeini, Cooper & Hauskrecht. "Obtaining Well Calibrated Probabilities Using Bayesian Binning." AAAI 2015.
  — 主張: Expected Calibration Error（ECE）の定式化。較正要否判断（D3）の指標定義。

## 8. グラフ最適化・自己改善（M7 / D9 の根拠）

- **[Swarm24]** Zhuge et al. "GPTSwarm: Language Agents as Optimizable Graphs." ICML 2024 (Oral). https://proceedings.mlr.press/v235/zhuge24a.html
  — 主張: エージェント＝計算グラフとして統一記述し、ノード（プロンプト）最適化とエッジ（接続構造）最適化を自動化する枠組み。「グラフエンジニアリング」の代表定式化。
- **[ADAS24]** Hu, Lu & Clune. "Automated Design of Agentic Systems." arXiv:2408.08435. https://arxiv.org/abs/2408.08435
  — 主張: メタエージェントがコードとして新しいエージェント設計を発明・改良していく探索枠組み。
- **[AFlow25]** Zhang et al. "AFlow: Automating Agentic Workflow Generation." ICLR 2025 (Oral). https://arxiv.org/abs/2410.10762
  — 主張: ワークフロー最適化を「コード表現されたワークフロー空間の探索問題」として定式化し、MCTSと実行フィードバックで反復改良。Stage3サブワークフローの予算制約付き探索（M7副題・D4自動化）の直接の型。周辺にMaAS（arXiv:2502.04180、supernet探索）等の後続。
- **[ProTeGi23]** Pryzant et al. "Automatic Prompt Optimization with 'Gradient Descent' and Beam Search." EMNLP 2023. https://arxiv.org/abs/2305.03495
  — 主張: 誤り事例のミニバッチから自然言語「勾配」を生成し、その逆方向にプロンプトを編集、ビームサーチ＋バンディットで探索。rulebook自動改訂の基本機構。
- **[GEPA25]** Agrawal et al. "GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning." arXiv:2507.19457. https://arxiv.org/abs/2507.19457
  — 主張: 実行軌跡への自然言語内省で高水準ルールを学習し、GRPO比最大+19pt・ロールアウト最大1/35、MIPROv2を10pt超上回る。DSPy統合済み。M7の第一候補実装。
- **[ACE25]** Zhang et al. "Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models." arXiv:2510.04618. https://arxiv.org/abs/2510.04618
  — 主張: Generator / Reflector / Curator の3役でコンテキストを「進化するプレイブック」として増分更新し、brevity bias と context collapse を構造化差分更新で防ぐ。ラベルなし・実行フィードバックのみで適応。境界事例集の維持規律（丸ごと書き換え禁止）の根拠。
- 注記: これらの公表成果は推論・コーディング課題中心で、知覚分類での証拠は薄い。採否は本リポジトリの実測（D9）で判断する。

## 9. 訓練不要適応（ゼロショットとFTの中間段）

- **[Tip22]** Zhang et al. "Tip-Adapter: Training-free Adaption of CLIP for Few-shot Classification." ECCV 2022. https://arxiv.org/abs/2207.09519
  — 主張: few-shot集合から key-value キャッシュを構築し、特徴検索でCLIPの事前知識を更新する訓練不要の適応。学習必須手法に匹敵。シルバーラベルをキャッシュ化する低コスト中間段（M6前段）の候補。

## 引用規約

reports/ 内で数値主張をする際は上記キー（[VJ01] 等）で引用する。本ファイルにない主張を導入する場合は、一次文献を確認してからここに追記する。
