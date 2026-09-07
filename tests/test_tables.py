"""cascade.io.tables のスキーマ検証・往復・DuckDB ビュー登録（design.md §5）。ネットワーク不要。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pyarrow as pa
import pytest

from cascade import paths
from cascade.io import tables
from cascade.io.tables import SCHEMAS, SchemaError

_TS = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    d = {k: tmp_path / k for k in ("raw", "core", "marts")}
    monkeypatch.setattr(paths, "LAYERS", d)
    return d


def _scope_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "photo_id": ["p1", "p2"],
            "source": ["yelp", "commons"],
            "content": ["food", "non_food"],
            "operational_truth": ["food", "out_of_scope"],
            "subject_type": [None, "doll_character_statue"],
            "boundary_flag": [False, True],
            "quality": ["ok", "ok"],
            "dup_group_id": ["g1", "g2"],
            "role": ["eval", "tune"],
            "sampling_prob": [1.0, 0.5],
            "license_short": [None, "CC BY-SA 4.0"],
            "attribution": [None, "someone"],
            "source_url": [None, "https://example.org/x"],
            "sha256": ["a" * 64, "b" * 64],
            "annotator": ["ann-01", "ann-01"],
            "guideline_version": ["0.1", "0.1"],
            "ts": [_TS, _TS],
        }
    )


def test_schemas_use_bigquery_compatible_types_only() -> None:
    allowed = {pa.string(), pa.int64(), pa.float64(), pa.bool_(), pa.timestamp("us", tz="UTC")}
    for name, schema in SCHEMAS.items():
        for field in schema:
            t = field.type
            if pa.types.is_list(t):
                assert t.value_type == pa.string(), (name, field.name)
            else:
                assert t in allowed, (name, field.name, t)


def test_design_md_tables_present() -> None:
    expected = {
        "labels",
        "gold_annotations",
        "scope_annotations",
        "predictions",
        "runs",
        "eval_sets",
        "ood_sets",
        "ood_scores",
        "gate_decisions",
    }
    assert expected <= set(SCHEMAS)
    assert SCHEMAS["runs"].names == [
        "run_id",
        "git_sha",
        "taxonomy_version",
        "rulebook_version",
        "eval_set_id",
        "model_id",
        "config_json",
        "metrics_json",
        "wandb_url",
        "ts",
    ]


def test_missing_column_raises(layers: dict[str, Path]) -> None:
    df = _scope_rows().drop("sha256")
    with pytest.raises(SchemaError, match="sha256"):
        tables.write_table("scope_annotations", df, "core")


def test_extra_column_raises(layers: dict[str, Path]) -> None:
    df = _scope_rows().with_columns(pl.lit("x").alias("business_id"))
    with pytest.raises(SchemaError, match="business_id"):
        tables.write_table("scope_annotations", df, "core")


def test_type_mismatch_raises(layers: dict[str, Path]) -> None:
    df = _scope_rows().with_columns(pl.lit("not-a-number").alias("sampling_prob"))
    with pytest.raises(SchemaError):
        tables.write_table("scope_annotations", df, "core")


def test_unknown_table_or_layer_raises(layers: dict[str, Path]) -> None:
    with pytest.raises(SchemaError):
        tables.write_table("nope", _scope_rows(), "core")
    with pytest.raises(SchemaError):
        tables.write_table("scope_annotations", _scope_rows(), "gold")


def test_scope_annotations_round_trip(layers: dict[str, Path]) -> None:
    df = _scope_rows()
    path = tables.write_table("scope_annotations", df, "core")
    assert path == layers["core"] / "scope_annotations.parquet"
    back = tables.read_table("scope_annotations", "core")
    assert back.columns == SCHEMAS["scope_annotations"].names
    assert back.schema["ts"] == pl.Datetime("us", "UTC")
    assert back.schema["boundary_flag"] == pl.Boolean
    assert back.to_dicts() == df.to_dicts()


def test_runs_round_trip_and_append(layers: dict[str, Path]) -> None:
    row = {
        "run_id": "r1",
        "git_sha": "abc",
        "taxonomy_version": "0.1.0",
        "rulebook_version": "未導入",
        "eval_set_id": "eval-v1",
        "model_id": "ViT-B-16",
        "config_json": "{}",
        "metrics_json": '{"acc": 0.5}',
        "wandb_url": None,
        "ts": _TS,
    }
    tables.append_rows("runs", [row], "marts")
    tables.append_rows("runs", [{**row, "run_id": "r2"}], "marts")
    back = tables.read_table("runs", "marts")
    assert back["run_id"].to_list() == ["r1", "r2"]
    assert back.schema["ts"] == pl.Datetime("us", "UTC")
    # 未知キーは拒否
    with pytest.raises(SchemaError, match="bogus"):
        tables.append_rows("runs", [{**row, "bogus": 1}], "marts")


def test_read_missing_returns_empty_with_schema(layers: dict[str, Path]) -> None:
    df = tables.read_table("labels", "raw")
    assert df.height == 0
    assert df.columns == SCHEMAS["labels"].names


def test_write_overwrites(layers: dict[str, Path]) -> None:
    tables.write_table("scope_annotations", _scope_rows(), "core")
    tables.write_table("scope_annotations", _scope_rows().head(1), "core")
    assert tables.read_table("scope_annotations", "core").height == 1


def test_duckdb_connect_registers_views_and_runs_neutral_sql(
    layers: dict[str, Path], tmp_path: Path
) -> None:
    tables.write_table("scope_annotations", _scope_rows(), "core")
    tables.append_rows(
        "runs",
        [
            {
                "run_id": "r1",
                "git_sha": "abc",
                "metrics_json": "{}",
                "ts": _TS,
            }
        ],
        "marts",
    )
    # 論理名 → 物理名の写像を config から注入（BigQuery では ds_core.scope_annotations 等になる）
    config = {"tables": {"scope_annotations": "scope_annotations", "runs": "runs_v1"}}
    conn = tables.duckdb_connect(config)
    assert conn.execute("SELECT count(*) FROM scope_annotations").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM runs_v1").fetchone()[0] == 1
    # 層付きの名前も参照できる
    assert conn.execute("SELECT count(*) FROM core.scope_annotations").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM marts.runs").fetchone()[0] == 1

    sql_path = tmp_path / "q.sql"
    sql_path.write_text(
        "SELECT source, count(*) AS n FROM {{scope}} "
        "WHERE role = '{{role}}' GROUP BY source ORDER BY source",
        encoding="utf-8",
    )
    out = tables.run_sql(conn, sql_path, {"scope": "scope_annotations", "role": "eval"})
    assert out.to_dicts() == [{"source": "yelp", "n": 1}]
    with pytest.raises(KeyError):
        tables.run_sql(conn, sql_path, {"scope": "scope_annotations"})


def test_duckdb_dotted_physical_name(layers: dict[str, Path]) -> None:
    tables.write_table(
        "labels",
        pl.DataFrame(
            {
                "photo_id": ["p"],
                "label_source": ["yelp"],
                "label": ["food"],
                "provenance": ["photos.json"],
                "ts": [_TS],
            }
        ),
        "raw",
    )
    conn = tables.duckdb_connect({"tables": {"labels": "ds_raw.labels"}})
    assert conn.execute("SELECT label FROM ds_raw.labels").fetchone()[0] == "food"


def test_duckdb_connect_with_no_data_dirs(layers: dict[str, Path]) -> None:
    conn = tables.duckdb_connect({})
    assert conn.execute("SELECT 1").fetchone()[0] == 1
