# 決められないものを、無理に決めない

```mermaid
flowchart LR
    classDef auto fill:#adf0c7,stroke:#087429
    classDef hold fill:#fff6b6,stroke:#af7e02

    IN["曖昧クラスを含む写真"]:::auto
    A["clear<br/>迷いなく確定"]:::auto
    B["rule_resolved<br/>ルールで確定"]:::auto
    C["irreducible<br/>決着せず保持"]:::hold

    IN --> A
    IN --> B
    IN --> C
```

**緑 = 自動で確定 ／ 黄 = 人手キューへ回し、分布を保持**

多くの分類システムは必ず1クラスを吐く。本設計は「決着しない」を三値フラグの一級市民として持ち、誤って断定する代わりに保持する。運用上、誤ラベルが下流に流れるコストは、保留のコストより高いことが多いため。

この設計の妥当性は主観ではなく測定で裏付ける。不一致帯において人間同士の一致度（κ）が低いことを示せれば、「機械が決められない」のではなく「そもそも人間にも決まらない」ことの証拠になる（[design.md](../design.md) §8 D5、M4 で実測）。各フラグの割合も M2–M3 で確定する。

フラグがどう決まるかは [runtime-decision-path.md](runtime-decision-path.md)。

正本: [design.md](../design.md) §4, §8 D5
