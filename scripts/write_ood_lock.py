"""全マニフェストから configs/ood_sources.lock（YAML）を生成する。

    uv run python scripts/write_ood_lock.py --out configs/ood_sources.lock

lock は源ごとの {ext_id, sha256, license_short, source_url} の一覧で、コミットする唯一の外部データ記録
（画像は git 管理外。plan v4 記録スキーマ / Phase A3 凍結）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import polars as pl
import typer

from cascade import paths
from cascade.data import fetch_common as fc

app = typer.Typer(add_completion=False)
LOCK_FIELDS = ("ext_id", "sha256", "license_short", "source_url")


def build_lock(
    manifests: dict[str, pl.DataFrame], *, config_path: str = "configs/ood_sources.yaml"
) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for name in sorted(manifests):
        df = manifests[name]
        if df.height == 0:
            continue
        rows = df.select(list(LOCK_FIELDS)).sort("ext_id").to_dicts()
        sources[name] = {
            "n": len(rows),
            "by_type": {
                k: int(v) for k, v in sorted(df.group_by("subject_type_hint").len().iter_rows())
            },
            "images": [{f: ("" if r[f] is None else r[f]) for f in LOCK_FIELDS} for r in rows],
        }
    return {"config": config_path, "generated_at": fc.now_iso(), "sources": sources}


def write_lock(lock: dict[str, Any], out: Path) -> Path:
    import yaml

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        f.write(
            "# 生成物: scripts/write_ood_lock.py。外部候補画像の ID・sha256・ライセンス（画像は git 管理外）\n"
        )
        yaml.safe_dump(lock, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return out


@app.command()
def main(
    out: Annotated[Path, typer.Option("--out")] = Path("configs/ood_sources.lock"),
    ext_root: Annotated[Path | None, typer.Option("--ext-root", help="既定は paths.EXT")] = None,
) -> None:
    manifests = fc.read_all_manifests(ext_root or paths.EXT)
    lock = build_lock(manifests)
    p = write_lock(lock, out)
    n = sum(v["n"] for v in lock["sources"].values())
    typer.echo(f"wrote {p} ({len(lock['sources'])} sources, {n} images)")


if __name__ == "__main__":
    app()
