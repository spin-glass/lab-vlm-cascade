"""M0 セットアップ CLI（design.md §6 M0、計画 Phase B-a）。

現時点は ``check-env`` のみ実装:
- torch 版、MPS の is_built / is_available、選択 device
- E1 が HF キャッシュにピン留め revision で揃っている（または ``CASCADE_NETWORK_TESTS`` が設定されている）場合のみ
  合成画像 256 枚のマイクロベンチ（img/s）と CPU vs MPS の余弦一致（16 枚、min cosine。期待 ≥ 0.999）
- ``--skip-model`` で torch / MPS の事実だけを出す

出力は JSON 1 個（stdout）。``--out`` で保存もできる。画像・生データは扱わない。

実行: ``uv run python -m cascade.m0_setup check-env [--skip-model] [--encoder e1]``
（計画 Phase B-a の表記 ``--check-env`` も同じ意味で受け付ける: ``python -m cascade.m0_setup --check-env --skip-model``）

TODO(M0, Yelp 到着後 / Phase B-b): 以下は Yelp Open Dataset の手動配置（paths.YELP）に依存するため未実装。
  - ``ingest-yelp``: photos.json の読み込みとチェックサム検証、規約 PDF 確認の記録
  - ``sample``: 層化サンプリングと tune_fit / tune_cal / work / eval の分割（business_id と dup 群の非交差）
  - ``freeze-eval``: eval の photo_id リストのハッシュ凍結と eval_sets への記録、W&B reference artifact
  - ``report``: reports/m0_setup.md の自動生成（フッタ: run_id / git sha / taxonomy_version / rulebook_version=未導入 / eval_set_id / コスト / W&B URL）
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import typer

from cascade import paths
from cascade.config import load_config
from cascade.embed.encoder import find_spec, is_cached_locally, load_encoder, resolve_device

app = typer.Typer(help="M0 セットアップ（環境確認・データ準備）", no_args_is_help=True)

DEFAULT_CONFIG = paths.REPO_ROOT / "configs" / "encoders.yaml"


@app.callback()
def _main() -> None:
    """サブコマンド名（check-env 等）を維持するためのルート callback。"""


def torch_facts() -> dict[str, Any]:
    import torch

    mps_built = bool(torch.backends.mps.is_built())
    mps_avail = bool(torch.backends.mps.is_available())
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "torch": torch.__version__,
        "mps_built": mps_built,
        "mps_available": mps_avail,
        "device": resolve_device("auto"),
        "cpu_threads": torch.get_num_threads(),
    }


def synthetic_images(n: int, size: int, seed: int = 0) -> list[Any]:
    from PIL import Image

    rng = np.random.default_rng(seed)
    return [
        Image.fromarray(rng.integers(0, 256, size=(size, size, 3), dtype=np.uint8), "RGB")
        for _ in range(n)
    ]


def _min_cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)
    b = b / np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
    return float((a * b).sum(axis=1).min())


def benchmark_encoder(
    spec: dict[str, Any], n_bench: int = 256, n_agree: int = 16, batch_size: int = 64
) -> dict[str, Any]:
    """合成画像で img/s と CPU/MPS 余弦一致を測る。モデルはキャッシュ済み前提。"""
    out: dict[str, Any] = {
        "encoder": spec["name"],
        "hf_id": spec["hf_id"],
        "revision": spec["revision"],
    }
    size = int(spec.get("image_size", 224))
    enc = load_encoder(spec, batch_size=batch_size)
    out["model_key"] = enc.model_key
    out["device"] = enc.device
    t0 = time.perf_counter()
    enc.ensure_loaded()  # type: ignore[attr-defined]
    out["load_sec"] = round(time.perf_counter() - t0, 3)
    out["dim"] = enc.dim

    imgs = synthetic_images(n_bench, size)
    enc.encode_images(imgs[:batch_size])  # warm-up（MPS のカーネルコンパイル分を除く）
    t0 = time.perf_counter()
    emb = enc.encode_images(imgs)
    dt = time.perf_counter() - t0
    out["bench_n"] = n_bench
    out["bench_sec"] = round(dt, 3)
    out["img_per_sec"] = round(n_bench / dt, 2) if dt > 0 else None
    out["bench_batch_size"] = batch_size
    out["nan_or_inf"] = bool(~np.isfinite(emb).all())

    if enc.device != "cpu":
        cpu_spec = dict(spec, device="cpu")
        cpu_enc = load_encoder(cpu_spec, batch_size=batch_size)
        sub = imgs[:n_agree]
        a = enc.encode_images(sub)
        b = cpu_enc.encode_images(sub)
        mc = _min_cosine(a, b)
        out["agreement"] = {
            "n": n_agree,
            "devices": [enc.device, "cpu"],
            "min_cosine": round(mc, 6),
            "ok": bool(mc >= 0.999),
            "expected_min": 0.999,
        }
        est = None
        if out["img_per_sec"]:
            t0 = time.perf_counter()
            cpu_enc.encode_images(imgs[:batch_size])
            cpu_dt = time.perf_counter() - t0
            est = round(batch_size / cpu_dt, 2) if cpu_dt > 0 else None
        out["cpu_img_per_sec_estimate"] = est
    else:
        out["agreement"] = {"skipped": "device is cpu; nothing to compare"}
    # 所要見積り（G0 規模の目安。25k work + 数千 ext）
    if out["img_per_sec"]:
        out["eta_sec_per_10k_images"] = round(10_000 / out["img_per_sec"], 1)
    return out


@app.command("check-env")
def check_env(
    config: Annotated[Path, typer.Option(help="configs/encoders.yaml")] = DEFAULT_CONFIG,
    encoder: Annotated[str, typer.Option(help="ベンチ対象のエンコーダ name")] = "e1",
    skip_model: Annotated[
        bool, typer.Option("--skip-model", help="torch / MPS の事実のみ出す")
    ] = False,
    n_bench: Annotated[int, typer.Option(help="マイクロベンチの合成画像枚数")] = 256,
    batch_size: Annotated[int, typer.Option()] = 64,
    out: Annotated[Path | None, typer.Option(help="JSON の保存先（省略時は stdout のみ）")] = None,
) -> None:
    """環境確認: torch / MPS / device、E1 のマイクロベンチと CPU-MPS 余弦一致。"""
    result: dict[str, Any] = {"torch": torch_facts(), "config": str(config)}
    if skip_model:
        result["model"] = {"skipped": "--skip-model"}
    else:
        cfg = load_config(config)  # ピン留め検証込み
        spec = find_spec(cfg, encoder)
        cached = is_cached_locally(spec)
        allow_net = bool(os.environ.get("CASCADE_NETWORK_TESTS"))
        result["model_cached_locally"] = cached
        if not cached and not allow_net:
            result["model"] = {
                "skipped": (
                    f"{spec['hf_id']}@{spec['revision']} が HF キャッシュに無い。"
                    "ダウンロードを許可するには CASCADE_NETWORK_TESTS=1 を設定して再実行"
                )
            }
        else:
            try:
                result["model"] = benchmark_encoder(spec, n_bench=n_bench, batch_size=batch_size)
            except Exception as e:  # noqa: BLE001 - 環境確認コマンドは落とさず記録する
                result["model"] = {"failed": f"{type(e).__name__}: {e}"}
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    typer.echo(text)


def main(argv: list[str] | None = None) -> None:
    """エントリポイント。``--check-env``（計画・CLAUDE.md の表記）をサブコマンド ``check-env`` に写像する。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--check-env" in args:
        args = ["check-env", *(a for a in args if a != "--check-env")]
    app(args=args)


if __name__ == "__main__":
    main()
