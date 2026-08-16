# docs/diagrams/

設計の可視化。**正本は [docs/design.md](../design.md)**、図はその可視化にすぎない。作成・改訂の規約は [docs/diagram-guidelines.md](../diagram-guidelines.md)。

各図は Mermaid フェンス入りの Markdown（GitHub 上でそのまま描画される）。Miro へは同一ソースから生成しており、Miro 上で直接編集しない。

## A群: 説得図（対外説明。この3枚だけで完結する）

| 図 | 主張 |
|---|---|
| [value-cascade.md](value-cascade.md) | 確信できる大多数は安く確定し、曖昧な少数だけに VLM の金を払う |
| [value-irreducible.md](value-irreducible.md) | 決められないものを、無理に決めない |
| [value-rule-agility.md](value-rule-agility.md) | 分類ルールが変わっても、モデルを再学習しない |

数値は `X%` のプレースホルダで、M2–M5 の実測後に確定する。本リポジトリは実測値のみを掲載する方針のため、未実測は空欄のままにしてある。

## B群: 参照図（実装用）

| 図 | 主張 | 抽象レベル |
|---|---|---|
| [runtime-decision-path.md](runtime-decision-path.md) | 1枚の写真は最大3段を通り、必ず三値フラグのいずれかで終端する | 実行時 |
| [data-assets.md](data-assets.md) | 実験記録の正本はローカル parquet であり、外部トラッカーはその投影にすぎない | 永続データ |
| [rulebook-codegen.md](rulebook-codegen.md) | Stage1/2/3 の全分類ロジックは rulebook.md から生成され、手書きの重複定義は存在しない | ビルド時 |
| [m7-gates.md](m7-gates.md) | 自動改訂案は3つのゲートを通らなければ PR にすらならない／承認プリミティブは PR のマージである | 運用（M7、任意） |

マイルストーン一覧は [README](../../README.md) の表、設計判断 D1–D10 は [design.md](../design.md) §8 が正本のため、図にはしない。

## Miro ボード

閲覧用: <https://miro.com/app/board/uXjVHx2mwC0=/>

フレーム構成: `01 主張`（A群）／ `02 実装`（B群）／ `03 ガバナンス`（M7）／ `04 判断基準`（D1–D10 表）。**対外説明時はフレーム 01 だけを見せて完結できる**。
