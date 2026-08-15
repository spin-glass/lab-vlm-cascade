# docs/diagrams/

設計の可視化。**正本は [docs/design.md](../design.md)** であり、本ディレクトリの図はその可視化にすぎない。design.md を改訂したら、対応する .mmd を更新し、Miro ボードへ再生成する。

| ファイル | 図 | 正本の節 |
|---|---|---|
| [architecture.mmd](architecture.mmd) | システムアーキテクチャ（カスケード＋監査ループ＋rulebook単一ソース＋tracker fan-out） | design.md §1, §3 |
| [decision-flow.mmd](decision-flow.mmd) | 1枚の写真の判定フロー（flag三値の分岐） | design.md §1, §4 |
| [milestones.mmd](milestones.mmd) | マイルストーンロードマップ（M0–M7、対応する設計判断ID） | design.md §6, §8 |
| [m7-approval-loop.mmd](m7-approval-loop.mmd) | M7 self-improving loop の PR承認ゲート | design.md §6 M7 |

decision matrix（D1–D10）の表は design.md §8 が正本のため、ここには置かない（Miro ボード上にのみ表として展開）。

## Miro ボード

閲覧用: <https://miro.com/app/board/uXjVHxs_4FQ=/>

- フレーム構成: 01 Overview（アーキテクチャ・ロードマップ）／ 02 Detail（判定フロー・M7承認ゲート）／ 03 Decisions（D1–D10 表）
- 各図は上記 .mmd から Miro MCP（`diagram_create_mermaid`）で生成。Miro 上で直接編集せず、.mmd を更新して再生成する
