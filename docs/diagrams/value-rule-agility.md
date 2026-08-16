# 分類ルールが変わっても、モデルを再学習しない

```mermaid
flowchart LR
    classDef ours fill:#adf0c7,stroke:#087429
    classDef old fill:#e7e7e7,stroke:#595959

    RB["rulebook.md<br/>v1.0 → v1.1"]:::ours
    GEN["プロンプトと判定表を再生成"]:::ours
    EV["再評価 → フリップ表"]:::ours

    OLD["従来: 再ラベル → 再学習 → 再デプロイ"]:::old

    RB -->|"その日のうちに"| GEN
    GEN --> EV
    OLD -.->|"数週間"| EV
```

**緑 = 本設計（Markdown 1ファイルの改版で完結） ／ 灰 = 従来の経路**

分類の意図・優先順位ルールは `rulebook.md` に単一ソースとして置かれ、Stage1 のクラスプロンプト・Stage2 の判定表・Stage3 の VLM プロンプトはすべてそこから生成される。したがってルール変更で触るのは Markdown 1ファイルだけで、モデルの重みは一切変わらない。

変更の影響は自動で可視化する。再評価で「どの写真の判定がどう変わったか」のフリップ表が出るため、変更を承認する側は影響範囲を見てから判断できる（フリップ率が基準を超えたら人手レビュー必須 — [design.md](../design.md) §8 D8）。所要時間の対比は M5 で実測する。

生成の詳細は [rulebook-codegen.md](rulebook-codegen.md)。

正本: [design.md](../design.md) §4, §6 M5
