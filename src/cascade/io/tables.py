"""三層 parquet テーブルのスキーマ定義と入出力（design.md §5、計画 v4「記録スキーマ」）。

- 正本はローカル parquet（``paths.LAYERS[layer]/<name>.parquet``）。BigQuery 互換型のみを使う
  （string / int64 / float64 / bool / timestamp[us, UTC] / list<string>）ので、同じ論理スキーマを
  そのまま BigQuery 三層（ds_raw / ds_core / ds_marts）へ移せる（design.md §3）
- 書き込み時にスキーマ検証を行う。欠損列・余分な列はともに ``SchemaError``
- 分析 SQL は DuckDB / BigQuery 両対応の方言中立な書き方に限定し、テーブル名は config から
  注入する（CLAUDE.md 開発規約）。``duckdb_connect`` が論理名→物理名の写像でビューを張り、
  ``run_sql`` が ``{{table_name}}`` プレースホルダを置換する
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from cascade import paths

# ---------------------------------------------------------------------------
# スキーマ（design.md §5）
# ---------------------------------------------------------------------------

_STR = pa.string()
_I64 = pa.int64()
_F64 = pa.float64()
_BOOL = pa.bool_()
_TS = pa.timestamp("us", tz="UTC")
_STR_LIST = pa.list_(pa.string())


class SchemaError(ValueError):
    """テーブル名・列・型がスキーマと一致しない。"""


SCHEMAS: dict[str, pa.Schema] = {
    # label は第2階層クラス。gold は gold_annotations.content_label を射影して label_source='gold'
    "labels": pa.schema(
        [
            ("photo_id", _STR),
            ("label_source", _STR),
            ("label", _STR),
            ("provenance", _STR),
            ("ts", _TS),
        ]
    ),
    # 第1階層（food / non-food）は持たない（導出のみ）
    "gold_annotations": pa.schema(
        [
            ("photo_id", _STR),
            ("content_label", _STR),
            ("quality", _STR),
            ("boundary_flag", _BOOL),
            ("food_visible", _BOOL),
            ("secondary", _STR),
            ("annotator", _STR),
            ("guideline_version", _STR),
            ("ts", _TS),
        ]
    ),
    # gold_annotations の拡張（G0）。ライセンス列は外部源のみ（Yelp は null）
    "scope_annotations": pa.schema(
        [
            ("photo_id", _STR),
            ("source", _STR),
            ("content", _STR),
            ("operational_truth", _STR),
            ("subject_type", _STR),
            ("boundary_flag", _BOOL),
            ("quality", _STR),
            ("dup_group_id", _STR),
            ("role", _STR),
            ("sampling_prob", _F64),
            ("license_short", _STR),
            ("attribution", _STR),
            ("source_url", _STR),
            ("sha256", _STR),
            ("annotator", _STR),
            ("guideline_version", _STR),
            ("ts", _TS),
        ]
    ),
    # probs_json は第2階層の確率（葉スコアを含めてよい）、branch_margin = S_food − S_non
    "predictions": pa.schema(
        [
            ("run_id", _STR),
            ("photo_id", _STR),
            ("stage", _STR),
            ("probs_json", _STR),
            ("margin", _F64),
            ("branch_margin", _F64),
            ("primary", _STR),
            ("secondary", _STR),
            ("flag", _STR),
            ("model_id", _STR),
            ("cost_tokens", _I64),
            ("ts", _TS),
        ]
    ),
    "runs": pa.schema(
        [
            ("run_id", _STR),
            ("git_sha", _STR),
            ("taxonomy_version", _STR),
            ("rulebook_version", _STR),
            ("eval_set_id", _STR),
            ("model_id", _STR),
            ("config_json", _STR),
            ("metrics_json", _STR),
            ("wandb_url", _STR),
            ("ts", _TS),
        ]
    ),
    # role ∈ {tune, work, eval}。photo_ids_hash で凍結
    "eval_sets": pa.schema(
        [
            ("eval_set_id", _STR),
            ("role", _STR),
            ("photo_ids_hash", _STR),
            ("definition", _STR),
            ("created_at", _TS),
        ]
    ),
    "ood_sets": pa.schema(
        [
            ("ood_set_id", _STR),
            ("source", _STR),
            ("subject_type", _STR),
            ("role", _STR),
            ("n", _I64),
            ("ids_sha256", _STR),
            ("lock_path", _STR),
            ("created_at", _TS),
        ]
    ),
    # score の向きは大＝対象内。mode ∈ {P1, P2}
    "ood_scores": pa.schema(
        [
            ("run_id", _STR),
            ("photo_id", _STR),
            ("source", _STR),
            ("mode", _STR),
            ("method_id", _STR),
            ("encoder_id", _STR),
            ("score", _F64),
            ("role", _STR),
            ("fold_axis", _STR),
            ("fold_id", _STR),
            ("ts", _TS),
        ]
    ),
    "gate_decisions": pa.schema(
        [
            ("run_id", _STR),
            ("photo_id", _STR),
            ("mode", _STR),
            ("method_id", _STR),
            ("threshold_id", _STR),
            ("decision", _STR),
            ("stage1_pred", _STR),
            ("final_label", _STR),
            ("ts", _TS),
        ]
    ),
}

# 推移閉包用（design.md §5 末尾）。ancestors は list<string>
SCHEMAS["taxonomy_nodes"] = pa.schema(
    [
        ("node_id", _STR),
        ("level", _I64),
        ("parent_id", _STR),
        ("ancestors", _STR_LIST),
    ]
)

# 論理名を解決するときの優先順（marts > core > raw）
_LAYER_PRIORITY = ("marts", "core", "raw")


# ---------------------------------------------------------------------------
# パス
# ---------------------------------------------------------------------------


def _schema_of(name: str) -> pa.Schema:
    try:
        return SCHEMAS[name]
    except KeyError as e:
        raise SchemaError(f"未知のテーブル名: {name!r}（既知: {sorted(SCHEMAS)}）") from e


def _layer_dir(layer: str) -> Path:
    try:
        return paths.LAYERS[layer]
    except KeyError as e:
        raise SchemaError(f"未知の層: {layer!r}（既知: {sorted(paths.LAYERS)}）") from e


def table_path(name: str, layer: str) -> Path:
    """テーブルの parquet パス（存在は問わない）。"""
    _schema_of(name)
    return _layer_dir(layer) / f"{name}.parquet"


# ---------------------------------------------------------------------------
# 検証・変換
# ---------------------------------------------------------------------------


def _validate_and_cast(name: str, df: pl.DataFrame) -> pa.Table:
    """polars → arrow。列集合の一致を検証し、スキーマへ cast する（型不一致は SchemaError）。"""
    schema = _schema_of(name)
    expected = list(schema.names)
    got = list(df.columns)
    missing = [c for c in expected if c not in got]
    extra = [c for c in got if c not in expected]
    if missing or extra:
        raise SchemaError(f"table {name!r}: 欠損列={missing} 余分な列={extra}")
    table = df.select(expected).to_arrow()
    try:
        return table.cast(schema, safe=True)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError, pa.ArrowTypeError) as e:
        raise SchemaError(f"table {name!r}: 型変換に失敗: {e}") from e


def _empty(name: str) -> pl.DataFrame:
    return pl.from_arrow(_schema_of(name).empty_table())  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def write_table(name: str, df: pl.DataFrame, layer: str) -> Path:
    """スキーマ検証のうえ parquet へ上書き保存し、パスを返す。"""
    table = _validate_and_cast(name, df)
    path = table_path(name, layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    return path


def read_table(name: str, layer: str) -> pl.DataFrame:
    """parquet を読み、スキーマ型で返す。ファイルが無ければ空テーブル。"""
    path = table_path(name, layer)
    if not path.exists():
        return _empty(name)
    table = pq.read_table(path)
    return _validate_and_cast_arrow(name, table)


def _validate_and_cast_arrow(name: str, table: pa.Table) -> pl.DataFrame:
    df = pl.from_arrow(table)
    assert isinstance(df, pl.DataFrame)
    return pl.from_arrow(_validate_and_cast(name, df))  # type: ignore[return-value]


def append_rows(name: str, rows: list[dict[str, Any]], layer: str) -> Path:
    """行（dict）を既存 parquet に追記する。

    未知のキーは SchemaError。欠けたキーは null になる（追記の利便性のため。列集合の厳密検証は
    ``write_table`` の責務）。
    """
    schema = _schema_of(name)
    known = set(schema.names)
    for i, row in enumerate(rows):
        unknown = sorted(set(row) - known)
        if unknown:
            raise SchemaError(f"table {name!r}: rows[{i}] に未知のキー {unknown}")
    new = pa.Table.from_pylist(rows, schema=schema)
    existing = read_table(name, layer)
    new_df = pl.from_arrow(new)
    assert isinstance(new_df, pl.DataFrame)
    merged = pl.concat([existing, new_df], how="vertical") if existing.height else new_df
    return write_table(name, merged, layer)


# ---------------------------------------------------------------------------
# DuckDB（分析 SQL は方言中立。BigQuery では同じ論理名で dataset.table に写す）
# ---------------------------------------------------------------------------

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_literal(s: str) -> str:
    """SQL 文字列リテラル（CREATE VIEW は prepared parameter を受け付けないため）。"""
    return "'" + s.replace("'", "''") + "'"


def _quote_ident(name: str) -> str:
    """``schema.table`` 形式を許容し、各要素を検証して引用する。"""
    parts = name.split(".")
    for p in parts:
        if not _IDENT.match(p):
            raise ValueError(f"不正な識別子: {name!r}")
    return ".".join(f'"{p}"' for p in parts)


def duckdb_connect(config: dict[str, Any]) -> duckdb.DuckDBPyConnection:
    """三層下の既存 parquet ごとにビューを張った DuckDB 接続を返す。

    - ``<layer>.<name>``（例 ``marts.runs``）を層ごとに登録
    - 論理名（``config["tables"]`` で物理名へ写像、既定は同名）を marts > core > raw の優先で登録
    - ``config["duckdb"]["path"]`` があればそのファイル DB、無ければ in-memory
    """
    db_path = str(config.get("duckdb", {}).get("path", ":memory:"))
    conn = duckdb.connect(db_path)
    mapping: dict[str, str] = dict(config.get("tables", {}) or {})
    resolved: dict[str, Path] = {}
    for layer in reversed(_LAYER_PRIORITY):  # raw → core → marts の順に上書きし、marts が勝つ
        d = paths.LAYERS[layer]
        if not d.exists():
            continue
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(layer)}")
        for p in sorted(d.glob("*.parquet")):
            logical = p.stem
            if not _IDENT.match(logical):
                continue
            conn.execute(
                f"CREATE OR REPLACE VIEW {_quote_ident(f'{layer}.{logical}')} "
                f"AS SELECT * FROM read_parquet({_quote_literal(str(p))})"
            )
            resolved[logical] = p
    for logical, p in resolved.items():
        physical = mapping.get(logical, logical)
        if "." in physical:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_ident(physical.rsplit('.', 1)[0])}")
        conn.execute(
            f"CREATE OR REPLACE VIEW {_quote_ident(physical)} "
            f"AS SELECT * FROM read_parquet({_quote_literal(str(p))})"
        )
    return conn


_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render_sql(sql: str, params: dict[str, Any]) -> str:
    """``{{name}}`` プレースホルダを置換する（値は識別子またはリテラルとしてそのまま埋める）。"""

    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in params:
            raise KeyError(f"SQL プレースホルダ {{{{{key}}}}} に対応する値が無い")
        return str(params[key])

    return _PLACEHOLDER.sub(sub, sql)


def run_sql(
    conn: duckdb.DuckDBPyConnection, sql_path: str | Path, params: dict[str, Any] | None = None
) -> pl.DataFrame:
    """SQL ファイルを読み、``{{table_name}}`` を置換して実行し polars で返す。"""
    sql = Path(sql_path).read_text(encoding="utf-8")
    rendered = render_sql(sql, params or {})
    return conn.execute(rendered).pl()
