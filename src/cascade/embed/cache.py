"""埋め込みキャッシュ: npy シャード＋parquet index（計画 Phase B-a「npy シャード＋parquet index、再開可能」）。

配置（``paths.EMB`` 直下）::

    <root>/<model_key>/<dataset_id>/shard-00000.npy
    <root>/<model_key>/<dataset_id>/index.parquet
    <root>/<model_key>/<dataset_id>/manifest.json

- index 列: embedding_id, photo_id, dataset_id, model_key, shard_path, row_idx, sha256, ts
- manifest: model_key / dataset_id / dim / shard_size / n_items と、シャードごとの ``ids_sha1``
  （そのシャードに入る photo_id 列の sha1）。再開時は npy が存在し ids_sha1 が一致するシャードだけ飛ばす
- ``model_key`` 不一致（キャッシュ vs エンコーダ、キャッシュ vs manifest）は必ず raise する
- 画像・生データはここに置かない。npy は数値ベクトルのみ（CLAUDE.md 絶対制約）
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from PIL import Image

from cascade import paths
from cascade.embed.encoder import Encoder

INDEX_COLUMNS = (
    "embedding_id",
    "photo_id",
    "dataset_id",
    "model_key",
    "shard_path",
    "row_idx",
    "sha256",
    "ts",
)
INDEX_SCHEMA = {
    "embedding_id": pl.Utf8,
    "photo_id": pl.Utf8,
    "dataset_id": pl.Utf8,
    "model_key": pl.Utf8,
    "shard_path": pl.Utf8,
    "row_idx": pl.Int64,
    "sha256": pl.Utf8,
    "ts": pl.Utf8,
}


class ModelKeyMismatch(ValueError):
    """キャッシュの model_key とエンコーダ／manifest の model_key が一致しない。"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ids_sha1(ids: Sequence[str]) -> str:
    return hashlib.sha1("\n".join(ids).encode("utf-8")).hexdigest()


def embedding_id(model_key: str, dataset_id: str, photo_id: str) -> str:
    return hashlib.sha1(f"{model_key}|{dataset_id}|{photo_id}".encode()).hexdigest()[:16]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load_image(path: Path) -> Image.Image:
    with Image.open(path) as im:
        return im.convert("RGB")


class EmbeddingCache:
    def __init__(self, model_key: str, root: Path = paths.EMB) -> None:
        self.model_key = model_key
        self.root = Path(root)

    # --- パス -----------------------------------------------------------------
    def dataset_dir(self, dataset_id: str) -> Path:
        return self.root / self.model_key / dataset_id

    def manifest_path(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "manifest.json"

    def index_path(self, dataset_id: str) -> Path:
        return self.dataset_dir(dataset_id) / "index.parquet"

    @staticmethod
    def shard_name(i: int) -> str:
        return f"shard-{i:05d}.npy"

    def _read_manifest(self, dataset_id: str) -> dict[str, Any] | None:
        p = self.manifest_path(dataset_id)
        if not p.exists():
            return None
        m = json.loads(p.read_text(encoding="utf-8"))
        if m.get("model_key") != self.model_key:
            raise ModelKeyMismatch(
                f"manifest の model_key={m.get('model_key')!r} がキャッシュの {self.model_key!r} と異なる: {p}"
            )
        return m

    def _write_manifest(self, dataset_id: str, m: dict[str, Any]) -> None:
        p = self.manifest_path(dataset_id)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    # --- 書き込み -------------------------------------------------------------
    def encode_dataset(
        self,
        dataset_id: str,
        items: Sequence[tuple[str, Path]],
        encoder: Encoder,
        batch_size: int = 64,
        shard_size: int = 2048,
        resume: bool = True,
    ) -> np.ndarray:
        """items=(photo_id, path) を順に埋め込み、シャードと index を書く。戻り値は全行の [n, dim]。"""
        if encoder.model_key != self.model_key:
            raise ModelKeyMismatch(
                f"encoder.model_key={encoder.model_key!r} がキャッシュの {self.model_key!r} と異なる"
            )
        if shard_size <= 0 or batch_size <= 0:
            raise ValueError("shard_size / batch_size は正の整数")
        ids = [pid for pid, _ in items]
        if len(set(ids)) != len(ids):
            raise ValueError("photo_id が重複している")

        ddir = self.dataset_dir(dataset_id)
        ddir.mkdir(parents=True, exist_ok=True)
        prev = self._read_manifest(dataset_id) if resume else None
        prev_shards: dict[str, dict[str, Any]] = {
            s["path"]: s for s in (prev or {}).get("shards", [])
        }

        manifest: dict[str, Any] = {
            "model_key": self.model_key,
            "dataset_id": dataset_id,
            "encoder_name": getattr(encoder, "name", ""),
            "dim": None,
            "shard_size": shard_size,
            "n_items": len(items),
            "ids_sha1": _ids_sha1(ids),
            "shards": [],
            "complete": False,
            "updated_at": _now(),
        }
        rows: list[dict[str, Any]] = []
        outs: list[np.ndarray] = []
        n_shards = (len(items) + shard_size - 1) // shard_size
        for si in range(n_shards):
            chunk = list(items[si * shard_size : (si + 1) * shard_size])
            chunk_ids = [pid for pid, _ in chunk]
            sname = self.shard_name(si)
            spath = ddir / sname
            ids_sha = _ids_sha1(chunk_ids)
            reused = (
                resume
                and sname in prev_shards
                and prev_shards[sname].get("ids_sha1") == ids_sha
                and spath.exists()
            )
            if reused:
                arr = np.load(spath)
                if arr.shape[0] != len(chunk):
                    reused = False
            if not reused:
                vecs: list[np.ndarray] = []
                for b in range(0, len(chunk), batch_size):
                    ims = [_load_image(Path(p)) for _, p in chunk[b : b + batch_size]]
                    vecs.append(np.asarray(encoder.encode_images(ims), dtype=np.float32))
                arr = np.concatenate(vecs, axis=0).astype(np.float32)
                tmp = spath.with_suffix(".npy.tmp")
                with tmp.open("wb") as f:  # ファイルオブジェクト渡しで ".npy" 付加を避ける
                    np.save(f, arr)
                tmp.replace(spath)
            if manifest["dim"] is None:
                manifest["dim"] = int(arr.shape[1])
            ts = _now()
            for ri, (pid, p) in enumerate(chunk):
                rows.append(
                    {
                        "embedding_id": embedding_id(self.model_key, dataset_id, pid),
                        "photo_id": pid,
                        "dataset_id": dataset_id,
                        "model_key": self.model_key,
                        "shard_path": sname,
                        "row_idx": ri,
                        "sha256": sha256_file(Path(p)),
                        "ts": ts,
                    }
                )
            outs.append(arr)
            manifest["shards"].append(
                {"path": sname, "n": len(chunk), "ids_sha1": ids_sha, "reused": bool(reused)}
            )
            manifest["updated_at"] = _now()
            # シャードごとに index / manifest を書き、途中停止でも再開できるようにする
            self._write_index(dataset_id, rows)
            self._write_manifest(dataset_id, manifest)

        if n_shards == 0:
            manifest["dim"] = int(encoder.dim)
            self._write_index(dataset_id, rows)
        manifest["complete"] = True
        self._write_manifest(dataset_id, manifest)
        if not outs:
            return np.zeros((0, int(manifest["dim"])), dtype=np.float32)
        return np.concatenate(outs, axis=0)

    def _write_index(self, dataset_id: str, rows: list[dict[str, Any]]) -> None:
        df = pl.DataFrame(rows, schema=INDEX_SCHEMA) if rows else pl.DataFrame(schema=INDEX_SCHEMA)
        p = self.index_path(dataset_id)
        tmp = p.with_suffix(".parquet.tmp")
        df.select(list(INDEX_COLUMNS)).write_parquet(tmp)
        tmp.replace(p)

    # --- 読み出し -------------------------------------------------------------
    def exists(self, dataset_id: str) -> bool:
        m = self._read_manifest(dataset_id)
        return bool(m and m.get("complete"))

    def index(self, dataset_id: str) -> pl.DataFrame:
        p = self.index_path(dataset_id)
        if not p.exists():
            raise FileNotFoundError(p)
        df = pl.read_parquet(p)
        bad = df.filter(pl.col("model_key") != self.model_key)
        if bad.height:
            raise ModelKeyMismatch(f"index に model_key 不一致の行がある: {p}")
        return df.sort(["shard_path", "row_idx"])

    def get(self, dataset_id: str) -> tuple[np.ndarray, pl.DataFrame]:
        """index 順にシャードを連結して返す。行 i が index の行 i に対応する。"""
        m = self._read_manifest(dataset_id)
        if m is None:
            raise FileNotFoundError(self.manifest_path(dataset_id))
        if not m.get("complete"):
            raise RuntimeError(
                f"キャッシュが未完了（resume=True で encode_dataset を再実行）: {dataset_id}"
            )
        df = self.index(dataset_id)
        ddir = self.dataset_dir(dataset_id)
        arrs = [np.load(ddir / s["path"]) for s in m["shards"]]
        emb = (
            np.concatenate(arrs, axis=0) if arrs else np.zeros((0, int(m["dim"])), dtype=np.float32)
        )
        if emb.shape[0] != df.height:
            raise RuntimeError(f"シャード行数 {emb.shape[0]} と index 行数 {df.height} が不一致")
        return emb.astype(np.float32, copy=False), df
