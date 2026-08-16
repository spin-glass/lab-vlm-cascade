# 確信できる大多数は安く確定し、曖昧な少数だけに VLM の金を払う

```mermaid
flowchart LR
    classDef cheap fill:#adf0c7,stroke:#087429
    classDef expensive fill:#f8d3af,stroke:#9b4a07
    classDef baseline fill:#e7e7e7,stroke:#595959

    IN["写真 100%"]:::cheap
    ENC["エンコーダ"]:::cheap
    DONE["確定"]:::cheap
    VLM["VLM"]:::expensive
    ALL["全量VLM: 100% が高い経路"]:::baseline

    IN --> ENC
    ENC -->|"大多数 (100 - X)%"| DONE
    ENC -->|"曖昧な X%"| VLM
    VLM --> DONE

    linkStyle 1 stroke-width:6px
    linkStyle 2 stroke-width:1px
```

**コスト: 全量VLM比 Y% ／ 精度: 同等圏（macro-F1 差 ≤ Z）**

太い矢印が安い経路、細い矢印が高い経路。この非対称性がカスケードの存在理由で、灰色の「全量VLM」は比較対象（すべてが高い経路を通る世界）。X / Y / Z は M3 で実測して確定する（判断基準は [design.md](../design.md) §8 D1: 委譲率≤X% で目標精度、かつ全量VLM比コスト<Y%）。現時点で数値は入っていない — 本リポジトリは実測値のみを掲載する方針のため。

エンコーダは OpenCLIP / SigLIP のゼロショット分類、VLM は Gemini の構造化出力判定。委譲の判定規則は [runtime-decision-path.md](runtime-decision-path.md)。

正本: [design.md](../design.md) §1, §6 M3
