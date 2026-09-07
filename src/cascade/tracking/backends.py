"""トラッキングのバックエンド実装（design.md §3「Tracker fan-out」）。

各バックエンドは ``start / log_metrics / log_table / finish`` の 4 段を持つ。呼び出し側（adapter）が
例外をすべて捕捉して ``failed:<ExceptionName>`` に落とすため、ここでは素直に例外を投げてよい。
認証情報が無い場合は ``SkipBackend`` を投げて ``skipped:<reason>`` にする。

制約（CLAUDE.md 絶対制約）:
- 画像・生データは送らない。scalar と小テーブルのみ（``max_table_rows`` で上限）
- Vertex AI TensorBoard は有効化しない（``experiment_tensorboard=False`` を必ず渡す）
- 認証は環境変数 ``WANDB_API_KEY`` / ``GOOGLE_CLOUD_PROJECT``（＋ADC）のみ。config へのベタ書き禁止
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Protocol

import polars as pl


class SkipBackend(Exception):
    """バックエンドを使わない（認証情報なし等）。メッセージが skipped:<reason> の reason になる。"""


class Backend(Protocol):
    """fan-out 先の共通契約。"""

    name: str

    def start(self, run_id: str, stamps: dict[str, Any]) -> None: ...

    def log_metrics(self, metrics: dict[str, Any]) -> None: ...

    def log_table(self, name: str, df: pl.DataFrame) -> None: ...

    def finish(self) -> str | None:
        """終了処理。run の URL があれば返す。"""
        ...


def _scalar_metrics(metrics: dict[str, Any]) -> dict[str, float | int]:
    """数値スカラーのみ抜き出す（bool は int、NaN/inf は除外）。"""
    out: dict[str, float | int] = {}
    for k, v in metrics.items():
        if isinstance(v, bool):
            out[k] = int(v)
        elif isinstance(v, (int, float)) and math.isfinite(v):
            out[k] = v
    return out


def _check_small(df: pl.DataFrame, max_rows: int, name: str) -> None:
    if df.height > max_rows:
        raise ValueError(
            f"table {name!r} は {df.height} 行で上限 {max_rows} を超える（小テーブルのみ送出可）"
        )


# ---------------------------------------------------------------------------
# W&B
# ---------------------------------------------------------------------------


class WandbBackend:
    """W&B Free（個人プロジェクト限定）。``WANDB_API_KEY`` 未設定なら skip。"""

    name = "wandb"

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        self.project: str = str(cfg.get("project", "lab-vlm-cascade"))
        self.entity: str | None = cfg.get("entity")
        self.max_table_rows: int = int(cfg.get("max_table_rows", 1000))
        self._run: Any = None

    def start(self, run_id: str, stamps: dict[str, Any]) -> None:
        if not os.environ.get("WANDB_API_KEY"):
            raise SkipBackend("no_api_key")
        import wandb  # 遅延 import（未使用時に SDK を初期化しない）

        self._run = wandb.init(
            project=self.project,
            entity=self.entity,
            id=run_id,
            config=dict(stamps),
            mode=os.environ.get("WANDB_MODE", "online"),
            reinit="create_new",
        )

    def log_metrics(self, metrics: dict[str, Any]) -> None:
        scalars = _scalar_metrics(metrics)
        if scalars:
            self._run.log(scalars)

    def log_table(self, name: str, df: pl.DataFrame) -> None:
        _check_small(df, self.max_table_rows, name)
        import wandb

        table = wandb.Table(columns=list(df.columns), data=[list(r) for r in df.rows()])
        self._run.log({name: table})

    def finish(self) -> str | None:
        url = getattr(self._run, "url", None)
        self._run.finish()
        return url


# ---------------------------------------------------------------------------
# Vertex AI Experiments
# ---------------------------------------------------------------------------

_VERTEX_RUN_RE = re.compile(r"[^a-z0-9-]")


def vertex_run_name(run_id: str) -> str:
    """Vertex の run 名制約（小文字英数とハイフン、先頭は英数、≤128）へ正規化する。"""
    s = _VERTEX_RUN_RE.sub("-", run_id.lower()).strip("-")
    return (s or "run")[:128]


class VertexBackend:
    """Vertex AI Experiments。``GOOGLE_CLOUD_PROJECT`` 未設定なら skip。TensorBoard は使わない。"""

    name = "vertex"

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        self.location: str = str(cfg.get("location", "us-central1"))
        self.experiment: str = str(cfg.get("experiment", "lab-vlm-cascade"))
        self.max_table_rows: int = int(cfg.get("max_table_rows", 1000))
        self._ap: Any = None
        self._started = False

    def start(self, run_id: str, stamps: dict[str, Any]) -> None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise SkipBackend("no_project")
        from google.cloud import aiplatform  # 遅延 import

        self._ap = aiplatform
        aiplatform.init(
            project=project,
            location=self.location,
            experiment=self.experiment,
            experiment_tensorboard=False,  # 課金対象のため有効化しない（design.md §3）
        )
        aiplatform.start_run(run=vertex_run_name(run_id))
        self._started = True
        params = {
            k: (v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v))
            for k, v in stamps.items()
            if v is not None and k != "config_json"  # 長文は param にしない
        }
        params["run_id"] = run_id
        if params:
            aiplatform.log_params(params)

    def log_metrics(self, metrics: dict[str, Any]) -> None:
        scalars = _scalar_metrics(metrics)
        if scalars:
            self._ap.log_metrics(scalars)

    def log_table(self, name: str, df: pl.DataFrame) -> None:
        # Experiments に汎用テーブルの器が無いため、行数のみをパラメータとして残す
        _check_small(df, self.max_table_rows, name)
        self._ap.log_params({f"table_{name}_rows": df.height})

    def finish(self) -> str | None:
        if self._started:
            self._ap.end_run()
        return None


BACKENDS: dict[str, type] = {"wandb": WandbBackend, "vertex": VertexBackend}
