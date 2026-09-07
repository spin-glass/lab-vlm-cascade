"""画像・テキストエンコーダ（design.md §1 Stage1、§6 G0「検出器」の E1 / E2）。

- E1: open_clip ``ViT-B-16`` / ``datacomp_xl_s13b_b90k``（HF Hub ``laion/CLIP-ViT-B-16-DataComp.XL-s13B-b90K``）
- E2: transformers ``google/siglip2-base-patch16-224``

方針:
- **revision ピン留めを実効化する**。open_clip の ``hf-hub:`` スキーマは revision を受け取らない
  （``open_clip.factory._get_hf_config`` / ``download_pretrained_from_hf`` が revision=None で呼ばれる）ため、
  設定ファイルと重みを ``huggingface_hub.hf_hub_download(..., revision=<hash>)`` で自前取得し、
  ``create_model_and_transforms(model_id, pretrained=<ローカル重みパス>)`` で組み立てる。
  transformers 側は ``from_pretrained(..., revision=<hash>)`` をそのまま使う。
- 常に float32。device は ``auto`` → MPS があれば mps、なければ cpu。
- **遅延ロード**: モデルは最初の encode で読む。``model_key`` は spec だけから決まり、ダウンロード不要。
- 出力は L2 正規化**しない**（正規化は呼び出し側。kNN / Mahalanobis++ / MCM で正規化の有無を明示するため）。
- ``model_key = sha1(backend|hf_id|revision|image_size|dtype|preprocess_desc)[:12]``（計画: model_key に前処理と dtype を含む）
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np
from PIL import Image

from cascade.config import PinError, validate_pins

SUPPORTED_BACKENDS = ("open_clip", "hf_siglip")
OPEN_CLIP_WEIGHT_FILES = ("open_clip_model.safetensors", "open_clip_pytorch_model.bin")
OPEN_CLIP_CONFIG_FILE = "open_clip_config.json"
HF_SIGLIP_REQUIRED_FILES = ("config.json", "model.safetensors", "preprocessor_config.json")


@runtime_checkable
class Encoder(Protocol):
    """他レーンが依存するエンコーダ契約。"""

    name: str
    model_key: str
    device: str

    @property
    def dim(self) -> int: ...

    def encode_images(self, images: Sequence[Image.Image]) -> np.ndarray: ...

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray: ...


def resolve_device(device: str | None) -> str:
    """``auto`` → mps（利用可能時）/ cpu。明示指定はそのまま返す。"""
    if device in (None, "", "auto"):
        import torch

        return "mps" if torch.backends.mps.is_available() else "cpu"
    return str(device)


def preprocess_desc(spec: dict[str, Any]) -> str:
    """前処理の決定的な記述文字列（model_key の材料）。ダウンロード不要で spec から決まる。"""
    backend = spec["backend"]
    size = int(spec.get("image_size", 224))
    if backend == "open_clip":
        # open_clip 既定: 短辺 resize + center crop、bicubic、pretrained tag の mean/std（OpenAI 値）
        return f"open_clip/{spec['model_id']}/{spec.get('pretrained', '')}/resize_shortest-centercrop-bicubic-{size}"
    if backend == "hf_siglip":
        # SiglipImageProcessor: 正方形 resize（bilinear）、mean=std=0.5
        return f"hf_siglip/resize_square-bilinear-{size}-mean0.5-std0.5"
    raise ValueError(f"未対応 backend: {backend!r}（対応: {SUPPORTED_BACKENDS}）")


def model_key_for(spec: dict[str, Any]) -> str:
    validate_spec(spec)
    dtype = spec.get("dtype", "float32")
    raw = f"{spec['backend']}|{spec['hf_id']}|{spec['revision']}|{int(spec.get('image_size', 224))}|{dtype}|{preprocess_desc(spec)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def validate_spec(spec: dict[str, Any]) -> None:
    """必須キーとピン留め（cascade.config.validate_pins）を検査する。"""
    for k in ("name", "backend", "hf_id", "revision"):
        if k not in spec:
            raise PinError(f"encoder spec に {k!r} がない: {spec}")
    if spec["backend"] not in SUPPORTED_BACKENDS:
        raise ValueError(f"未対応 backend: {spec['backend']!r}（対応: {SUPPORTED_BACKENDS}）")
    if spec["backend"] == "open_clip" and "model_id" not in spec:
        raise PinError("open_clip backend には model_id（アーキテクチャ名）が必須")
    if spec.get("dtype", "float32") != "float32":
        raise ValueError("dtype は float32 のみ対応（MPS の fp16 差異を避けるため）")
    validate_pins(spec)


def _batches(seq: Sequence[Any], size: int) -> list[Sequence[Any]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


class _TorchEncoderBase:
    """遅延ロードと no_grad バッチ推論の共通部分。"""

    def __init__(self, spec: dict[str, Any], batch_size: int = 64) -> None:
        validate_spec(spec)
        self.spec = dict(spec)
        self.name: str = spec["name"]
        self.backend: str = spec["backend"]
        self.hf_id: str = spec["hf_id"]
        self.revision: str = spec["revision"]
        self.image_size: int = int(spec.get("image_size", 224))
        self.dtype: str = spec.get("dtype", "float32")
        self.device: str = resolve_device(spec.get("device", "auto"))
        self.model_key: str = model_key_for(spec)
        self.batch_size = batch_size
        self._loaded = False
        self._dim: int | None = None

    # --- 遅延ロード -------------------------------------------------------------
    def _load(self) -> None:  # pragma: no cover - 実モデル
        raise NotImplementedError

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self._load()
            self._loaded = True

    @property
    def dim(self) -> int:
        if self._dim is None:
            self.ensure_loaded()
        assert self._dim is not None
        return self._dim

    # --- 推論 ------------------------------------------------------------------
    def _encode_image_batch(self, images: Sequence[Image.Image]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    def _encode_text_batch(self, texts: Sequence[str]) -> np.ndarray:  # pragma: no cover
        raise NotImplementedError

    def encode_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        self.ensure_loaded()
        if len(images) == 0:
            return np.zeros((0, self.dim), dtype=np.float32)
        outs = [self._encode_image_batch(b) for b in _batches(list(images), self.batch_size)]
        return np.concatenate(outs, axis=0).astype(np.float32, copy=False)

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        self.ensure_loaded()
        if len(texts) == 0:
            return np.zeros((0, self.dim), dtype=np.float32)
        outs = [self._encode_text_batch(b) for b in _batches(list(texts), self.batch_size)]
        return np.concatenate(outs, axis=0).astype(np.float32, copy=False)


class OpenClipEncoder(_TorchEncoderBase):
    """open_clip エンコーダ。重み・設定は revision 固定で HF Hub から自前取得する。"""

    def _download(self, filename: str, *, local_files_only: bool = False) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(
            hf_hub_download(
                repo_id=self.hf_id,
                filename=filename,
                revision=self.revision,
                local_files_only=local_files_only,
            )
        )

    def _weights_path(self) -> Path:
        last: Exception | None = None
        for fn in OPEN_CLIP_WEIGHT_FILES:
            try:
                return self._download(fn)
            except Exception as e:  # noqa: BLE001 - 次候補へ
                last = e
        raise FileNotFoundError(
            f"{self.hf_id}@{self.revision} に {OPEN_CLIP_WEIGHT_FILES} が見つからない: {last}"
        )

    def _load(self) -> None:  # pragma: no cover - 実モデル（ネットワーク／キャッシュ必須）
        import json

        import open_clip
        import torch

        cfg = json.loads(self._download(OPEN_CLIP_CONFIG_FILE).read_text(encoding="utf-8"))
        pre = cfg.get("preprocess_cfg", {})
        weights = self._weights_path()
        model, _, preprocess = open_clip.create_model_and_transforms(
            self.spec["model_id"],
            pretrained=str(weights),
            precision="fp32",
            device=self.device,
            image_mean=tuple(pre["mean"]) if "mean" in pre else None,
            image_std=tuple(pre["std"]) if "std" in pre else None,
            force_image_size=self.image_size,
        )
        model.eval()
        self._model = model
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(self.spec["model_id"])
        self._torch = torch
        self._dim = int(
            cfg.get("model_cfg", {}).get("embed_dim") or model.text_projection.shape[-1]
        )

    def _encode_image_batch(self, images: Sequence[Image.Image]) -> np.ndarray:  # pragma: no cover
        torch = self._torch
        x = torch.stack([self._preprocess(im.convert("RGB")) for im in images]).to(self.device)
        with torch.no_grad():
            f = self._model.encode_image(x)
        return f.float().cpu().numpy()

    def _encode_text_batch(self, texts: Sequence[str]) -> np.ndarray:  # pragma: no cover
        torch = self._torch
        tok = self._tokenizer(list(texts)).to(self.device)
        with torch.no_grad():
            f = self._model.encode_text(tok)
        return f.float().cpu().numpy()


class HfSiglipEncoder(_TorchEncoderBase):
    """transformers SigLIP2（``Siglip2Model.get_image_features / get_text_features``）。"""

    def _load(self) -> None:  # pragma: no cover - 実モデル
        import torch
        from transformers import AutoModel, AutoProcessor

        self._model = AutoModel.from_pretrained(
            self.hf_id, revision=self.revision, dtype=torch.float32
        ).to(self.device)
        self._model.eval()
        self._processor = AutoProcessor.from_pretrained(self.hf_id, revision=self.revision)
        self._torch = torch
        cfg = self._model.config
        self._dim = int(getattr(cfg, "projection_dim", None) or cfg.text_config.hidden_size)

    @staticmethod
    def _pooled(out: Any) -> Any:
        # transformers 5.x は BaseModelOutputWithPooling を返す。旧版は Tensor を返す
        return getattr(out, "pooler_output", out)

    def _encode_image_batch(self, images: Sequence[Image.Image]) -> np.ndarray:  # pragma: no cover
        torch = self._torch
        inputs = self._processor(images=[im.convert("RGB") for im in images], return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            f = self._pooled(self._model.get_image_features(**inputs))
        return f.float().cpu().numpy()

    def _encode_text_batch(self, texts: Sequence[str]) -> np.ndarray:  # pragma: no cover
        torch = self._torch
        inputs = self._processor(
            text=list(texts), padding="max_length", truncation=True, return_tensors="pt"
        )
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            f = self._pooled(self._model.get_text_features(**inputs))
        return f.float().cpu().numpy()


def is_cached_locally(spec: dict[str, Any]) -> bool:
    """ピン留め revision の必要ファイルが HF キャッシュに揃っているか（ネットワーク不要）。"""
    from huggingface_hub import try_to_load_from_cache

    validate_spec(spec)
    repo, rev = spec["hf_id"], spec["revision"]

    def has(fn: str) -> bool:
        return isinstance(try_to_load_from_cache(repo, fn, revision=rev), str)

    if spec["backend"] == "open_clip":
        return has(OPEN_CLIP_CONFIG_FILE) and any(has(fn) for fn in OPEN_CLIP_WEIGHT_FILES)
    return all(has(fn) for fn in HF_SIGLIP_REQUIRED_FILES)


def load_encoder(spec: dict[str, Any], batch_size: int = 64) -> Encoder:
    """spec（configs/encoders.yaml の 1 要素）からエンコーダを作る。モデルはまだ読まない。"""
    validate_spec(spec)
    if spec["backend"] == "open_clip":
        return OpenClipEncoder(spec, batch_size=batch_size)
    return HfSiglipEncoder(spec, batch_size=batch_size)


def find_spec(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    for s in cfg.get("encoders", []):
        if s.get("name") == name:
            return s
    raise KeyError(f"encoders に name={name!r} がない")
