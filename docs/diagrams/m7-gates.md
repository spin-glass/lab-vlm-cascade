# M7 自動改訂ループのゲート設計

対象読者: 実装者。M7 は design.md 上「（任意）」であり、M0–M5 完了後に着手する。

## 1. 自動改訂案は、3つのゲートを通らなければ PR にすらならない

```mermaid
flowchart TD
    classDef proc fill:#c6dcff,stroke:#305bab
    classDef cond fill:#fff6b6,stroke:#af7e02
    classDef stop fill:#e7e7e7,stroke:#595959
    classDef go fill:#adf0c7,stroke:#087429

    W["週次監査バッチ"]:::proc
    G1{"不一致の蓄積 > 発火閾値?"}:::cond
    GEN["改訂案を自動生成"]:::proc
    EVAL["holdout rotation で評価"]:::proc
    G2{"改善幅の CI 下限 > 0?"}:::cond
    G3{"未処理の自動PR なし?"}:::cond
    PR(["PR を作成"]):::go
    SKIP(["見送り・runs にログのみ"]):::stop

    W --> G1
    G1 -->|"No"| SKIP
    G1 -->|"Yes"| GEN
    GEN --> EVAL
    EVAL --> G2
    G2 -->|"No"| SKIP
    G2 -->|"Yes"| G3
    G3 -->|"No"| SKIP
    G3 -->|"Yes"| PR
```

**黄 = ゲート ／ 灰 = ここで止まる ／ 緑 = 通過**

3つのゲートはいずれも「PR を作らせない」ために置く。①**発火制御**: 生成は週次バッチに従属させ、イベント駆動にしない。②**改善ゲート**: holdout rotation（評価分割を回転させ過適合を検出）で最小改善幅を満たさない候補は PR 化せず自動棄却する。③**レート上限**: 1サイクル最大1PR、かつ `max_open_prs=1`。

頻度は成り行きではなく発火閾値で制御するガバナンス変数とし、PR発生頻度・却下率・レビュー所要時間を D9 の計測項目に含める。

## 2. 承認プリミティブは PR のマージであり、diff の見える場所にしか承認を置かない

```mermaid
flowchart TD
    classDef proc fill:#c6dcff,stroke:#305bab
    classDef human fill:#ffd8f4,stroke:#af3fb9
    classDef go fill:#adf0c7,stroke:#087429

    PR["ブランチ rulebook/vX.Y-rc<br/>本文 = 影響差分レポート"]:::proc
    CI["CI: 再生成 → 再評価<br/>status check 化"]:::proc
    REV["CODEOWNERS の承認レビュー"]:::human
    MG(["マージ = 承認確定"]):::go
    ENV["高コスト処理は<br/>environments で二段目の承認"]:::human
    AP(["M5 再評価パイプラインへ"]):::go

    PR --> CI
    CI -->|"基準未達はマージ不能"| REV
    REV --> MG
    MG --> ENV
    ENV --> AP
```

**桃 = 人の承認 ／ 青 = 自動処理 ／ 緑 = 到達点**

`rulebook.md` に CODEOWNERS と required approving review を設定し、**マージそのものを承認確定とする**。蒸留・一括再ラベルなど高コストな下流処理には Actions environments の required reviewers で二段目のゲートを設ける。

Slack は通知・催促・議論に限定し、**承認行為は置かない**。diff の見えない場所での承認は、監査痕跡を分散させるため。

**変更の3層ポリシー**: Tier 1＝境界事例集への追記（最頻・最軽、週次PRに同梱）／ Tier 2＝ルールの新規追加（minor bump、同梱可）／ Tier 3＝既存ルールの修正・優先順位変更（既存判定のフリップ発生源。単独PR＋フリップ表必須、フリップ率が基準超なら目視サンプルレビュー。D8 と接続）。

**撤退条件**（フローではなく運用ポリシー）: 却下率が高止まりする、またはレビュー負担が改善価値を上回ると計測された場合はループを停止し、手動 M5 運用（人が起点、機械は影響分析のみ）へ戻す。この条件を D9 の採否基準に含める。

正本: [design.md](../design.md) §6 M7, §8 D9
