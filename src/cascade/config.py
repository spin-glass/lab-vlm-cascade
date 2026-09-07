"""YAML config の読み込みと、モデル・データ ID のピン留め検証。

CLAUDE.md: モデル ID・taxonomy_version・rulebook_version は config にピン留めし、"latest" 系エイリアスを禁止する。
HF の revision 未指定は "main" (= latest) と同義なので、revision の空文字・"main"・"latest" も拒否する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

FORBIDDEN_ALIASES = frozenset({"latest", "main", "master", "newest", "current", ""})


class PinError(ValueError):
    """ピン留めされていない ID / revision を検出した。"""


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"config のトップレベルは mapping である必要がある: {path}")
    return data


def _check_value(key_path: str, value: Any) -> None:
    if isinstance(value, str) and value.strip().lower() in FORBIDDEN_ALIASES:
        raise PinError(f"{key_path} = {value!r} はピン留めされていない（latest 系エイリアス禁止）")


def validate_pins(
    cfg: dict[str, Any],
    *,
    keys: tuple[str, ...] = (
        "revision",
        "model_id",
        "hf_id",
        "pretrained",
        "dataset_id",
        "taxonomy_version",
        "rulebook_version",
    ),
) -> None:
    """config 木を再帰的に走査し、ピン留め対象キーの値がエイリアスなら PinError を投げる。

    ``revision`` は存在すること自体も要求する（``hf_id`` / ``dataset_id`` を持つ mapping には ``revision`` 必須）。
    """

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if ("hf_id" in node or "dataset_id" in node) and "revision" not in node:
                raise PinError(
                    f"{path}: hf_id / dataset_id には revision（コミットハッシュ）が必須"
                )
            for k, v in node.items():
                kp = f"{path}.{k}" if path else str(k)
                if k in keys:
                    _check_value(kp, v)
                walk(v, kp)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(cfg, "")


def load_config(path: str | Path, *, validate: bool = True) -> dict[str, Any]:
    cfg = load_yaml(path)
    if validate:
        validate_pins(cfg)
    return cfg
