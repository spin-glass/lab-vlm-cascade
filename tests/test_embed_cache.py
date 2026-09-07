"""EmbeddingCache のオフラインテスト（FakeEncoder。ネットワーク・モデル不要）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from PIL import Image

from cascade.embed.cache import INDEX_COLUMNS, EmbeddingCache, ModelKeyMismatch, sha256_file
from cascade.embed.fake import FakeEncoder


def _make_images(tmp: Path, n: int) -> list[tuple[str, Path]]:
    rng = np.random.default_rng(0)
    items = []
    for i in range(n):
        p = tmp / f"img_{i:03d}.png"
        Image.fromarray(rng.integers(0, 256, (16, 16, 3), dtype=np.uint8)).save(p)
        items.append((f"p{i:03d}", p))
    return items


@pytest.fixture
def items(tmp_path: Path) -> list[tuple[str, Path]]:
    d = tmp_path / "imgs"
    d.mkdir()
    return _make_images(d, 10)


def test_round_trip_shards_and_index(tmp_path: Path, items: list[tuple[str, Path]]) -> None:
    enc = FakeEncoder(model_key="abc123abc123")
    cache = EmbeddingCache(model_key=enc.model_key, root=tmp_path / "emb")
    emb = cache.encode_dataset("ds1", items, enc, batch_size=3, shard_size=4)
    assert emb.shape == (10, 8) and emb.dtype == np.float32

    ddir = tmp_path / "emb" / enc.model_key / "ds1"
    shards = sorted(p.name for p in ddir.glob("shard-*.npy"))
    assert shards == ["shard-00000.npy", "shard-00001.npy", "shard-00002.npy"]
    assert not list(ddir.glob("*.tmp"))

    got, idx = cache.get("ds1")
    np.testing.assert_allclose(got, emb)
    assert tuple(idx.columns) == INDEX_COLUMNS
    assert idx["photo_id"].to_list() == [pid for pid, _ in items]
    assert idx["shard_path"].to_list()[:5] == ["shard-00000.npy"] * 4 + ["shard-00001.npy"]
    assert idx["row_idx"].to_list()[:5] == [0, 1, 2, 3, 0]
    assert idx["sha256"][0] == sha256_file(items[0][1])
    assert idx["model_key"].unique().to_list() == [enc.model_key]
    assert idx["dataset_id"].unique().to_list() == ["ds1"]
    assert idx["embedding_id"].n_unique() == 10

    # 埋め込みは決定的で、直接エンコードした値と一致する
    direct = enc.encode_images([Image.open(p) for _, p in items])
    np.testing.assert_allclose(got, direct)

    m = json.loads((ddir / "manifest.json").read_text())
    assert m["complete"] and m["dim"] == 8 and len(m["shards"]) == 3


def test_resume_skips_completed_shards(tmp_path: Path, items: list[tuple[str, Path]]) -> None:
    enc = FakeEncoder(model_key="abc123abc123")
    cache = EmbeddingCache(model_key=enc.model_key, root=tmp_path / "emb")
    first = cache.encode_dataset("ds1", items, enc, batch_size=4, shard_size=4)
    n_first = enc.n_images_encoded
    assert n_first == 10

    # 最後のシャードを消して「途中停止」を模す → 再開で 2 枚だけ再エンコード
    ddir = tmp_path / "emb" / enc.model_key / "ds1"
    (ddir / "shard-00002.npy").unlink()
    second = cache.encode_dataset("ds1", items, enc, batch_size=4, shard_size=4, resume=True)
    assert enc.n_images_encoded - n_first == 2
    np.testing.assert_allclose(first, second)
    m = json.loads((ddir / "manifest.json").read_text())
    assert [s["reused"] for s in m["shards"]] == [True, True, False]

    # 完了済みなら再実行でエンコード 0 枚
    n = enc.n_images_encoded
    cache.encode_dataset("ds1", items, enc, batch_size=4, shard_size=4)
    assert enc.n_images_encoded == n

    # resume=False は全部やり直す
    cache.encode_dataset("ds1", items, enc, batch_size=4, shard_size=4, resume=False)
    assert enc.n_images_encoded == n + 10


def test_resume_reencodes_when_items_change(tmp_path: Path, items: list[tuple[str, Path]]) -> None:
    enc = FakeEncoder(model_key="abc123abc123")
    cache = EmbeddingCache(model_key=enc.model_key, root=tmp_path / "emb")
    cache.encode_dataset("ds1", items, enc, shard_size=4)
    n = enc.n_images_encoded
    # 先頭シャードの並びを変える → そのシャードだけ再エンコード
    changed = [items[1], items[0], *items[2:]]
    out = cache.encode_dataset("ds1", changed, enc, shard_size=4)
    assert enc.n_images_encoded - n == 4
    got, idx = cache.get("ds1")
    assert idx["photo_id"].to_list()[:2] == ["p001", "p000"]
    np.testing.assert_allclose(got, out)


def test_model_key_mismatch_raises(tmp_path: Path, items: list[tuple[str, Path]]) -> None:
    enc = FakeEncoder(model_key="abc123abc123")
    root = tmp_path / "emb"
    cache = EmbeddingCache(model_key=enc.model_key, root=root)
    cache.encode_dataset("ds1", items, enc, shard_size=4)

    other = EmbeddingCache(model_key="ffffffffffff", root=root)
    with pytest.raises(ModelKeyMismatch):
        other.encode_dataset("ds1", items, enc)

    # manifest を改ざん → get で検出
    mp = root / enc.model_key / "ds1" / "manifest.json"
    m = json.loads(mp.read_text())
    m["model_key"] = "deadbeef0000"
    mp.write_text(json.dumps(m))
    with pytest.raises(ModelKeyMismatch):
        cache.get("ds1")


def test_index_model_key_mismatch_raises(tmp_path: Path, items: list[tuple[str, Path]]) -> None:
    enc = FakeEncoder(model_key="abc123abc123")
    root = tmp_path / "emb"
    cache = EmbeddingCache(model_key=enc.model_key, root=root)
    cache.encode_dataset("ds1", items, enc, shard_size=4)
    ip = root / enc.model_key / "ds1" / "index.parquet"
    df = pl.read_parquet(ip).with_columns(pl.lit("deadbeef0000").alias("model_key"))
    df.write_parquet(ip)
    with pytest.raises(ModelKeyMismatch):
        cache.get("ds1")


def test_incomplete_cache_and_missing_raise(tmp_path: Path, items: list[tuple[str, Path]]) -> None:
    enc = FakeEncoder(model_key="abc123abc123")
    root = tmp_path / "emb"
    cache = EmbeddingCache(model_key=enc.model_key, root=root)
    with pytest.raises(FileNotFoundError):
        cache.get("nope")
    cache.encode_dataset("ds1", items, enc, shard_size=4)
    mp = root / enc.model_key / "ds1" / "manifest.json"
    m = json.loads(mp.read_text())
    m["complete"] = False
    mp.write_text(json.dumps(m))
    assert not cache.exists("ds1")
    with pytest.raises(RuntimeError):
        cache.get("ds1")


def test_duplicate_photo_ids_rejected(tmp_path: Path, items: list[tuple[str, Path]]) -> None:
    enc = FakeEncoder(model_key="abc123abc123")
    cache = EmbeddingCache(model_key=enc.model_key, root=tmp_path / "emb")
    with pytest.raises(ValueError):
        cache.encode_dataset("ds1", [items[0], items[0]], enc)


def test_empty_dataset(tmp_path: Path) -> None:
    enc = FakeEncoder(model_key="abc123abc123")
    cache = EmbeddingCache(model_key=enc.model_key, root=tmp_path / "emb")
    emb = cache.encode_dataset("empty", [], enc)
    assert emb.shape == (0, 8)
    got, idx = cache.get("empty")
    assert got.shape == (0, 8) and idx.height == 0
