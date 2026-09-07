"""configs/encoders.yaml のピン留め検証と encoder spec の検査（オフライン。モデルは読まない）。"""

from __future__ import annotations

import copy
import os

import pytest

from cascade import paths
from cascade.config import PinError, load_config, validate_pins
from cascade.embed.encoder import (
    HfSiglipEncoder,
    OpenClipEncoder,
    find_spec,
    load_encoder,
    model_key_for,
    resolve_device,
    validate_spec,
)

CONFIG = paths.REPO_ROOT / "configs" / "encoders.yaml"


def test_encoders_yaml_passes_pin_validation() -> None:
    cfg = load_config(CONFIG)  # validate_pins 込み
    names = [e["name"] for e in cfg["encoders"]]
    assert names == ["e1", "e2"]
    for e in cfg["encoders"]:
        assert len(e["revision"]) == 40 and all(c in "0123456789abcdef" for c in e["revision"])
        assert e["dtype"] == "float32" and e["image_size"] == 224
    assert cfg["taxonomy_version"] == "0.1.0"


def test_mutated_revision_main_rejected() -> None:
    cfg = load_config(CONFIG)
    bad = copy.deepcopy(cfg)
    bad["encoders"][0]["revision"] = "main"
    with pytest.raises(PinError):
        validate_pins(bad)
    with pytest.raises(PinError):
        validate_spec(bad["encoders"][0])
    bad2 = copy.deepcopy(cfg)
    bad2["encoders"][1]["revision"] = "latest"
    with pytest.raises(PinError):
        load_encoder(bad2["encoders"][1])
    bad3 = copy.deepcopy(cfg)
    del bad3["encoders"][0]["revision"]
    with pytest.raises(PinError):
        validate_pins(bad3)


def test_model_key_is_stable_and_sensitive_to_pins() -> None:
    cfg = load_config(CONFIG)
    e1 = cfg["encoders"][0]
    k = model_key_for(e1)
    assert len(k) == 12 and k == model_key_for(dict(e1))
    assert k != model_key_for(dict(e1, revision="0" * 40))
    assert k != model_key_for(dict(e1, image_size=256))
    assert k != model_key_for(cfg["encoders"][1])
    # device は model_key に影響しない（CPU/MPS で同じキャッシュを共有する）
    assert k == model_key_for(dict(e1, device="cpu"))


def test_load_encoder_is_lazy_and_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_config(CONFIG)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    e1 = load_encoder(dict(cfg["encoders"][0], device="cpu"))
    e2 = load_encoder(dict(cfg["encoders"][1], device="cpu"))
    assert isinstance(e1, OpenClipEncoder) and isinstance(e2, HfSiglipEncoder)
    assert e1.device == "cpu" and e1.name == "e1" and e2.name == "e2"
    assert e1._loaded is False and e2._loaded is False  # 構築時にモデルを読まない
    assert find_spec(cfg, "e2")["name"] == "e2"
    with pytest.raises(KeyError):
        find_spec(cfg, "e9")
    with pytest.raises(ValueError):
        load_encoder(dict(cfg["encoders"][0], backend="nope"))
    with pytest.raises(ValueError):
        load_encoder(dict(cfg["encoders"][0], dtype="float16"))


def test_resolve_device_auto() -> None:
    assert resolve_device("cpu") == "cpu"
    assert resolve_device("auto") in ("mps", "cpu")


@pytest.mark.skipif(not os.environ.get("CASCADE_NETWORK_TESTS"), reason="network")
def test_real_encoders_encode(tmp_path) -> None:  # pragma: no cover
    import numpy as np
    from PIL import Image

    cfg = load_config(CONFIG)
    for spec in cfg["encoders"]:
        enc = load_encoder(spec)
        img = Image.fromarray(np.zeros((224, 224, 3), dtype=np.uint8))
        v = enc.encode_images([img, img])
        t = enc.encode_texts(["a photo of food"])
        assert v.shape == (2, enc.dim) and t.shape == (1, enc.dim) and v.dtype == np.float32
        np.testing.assert_allclose(v[0], v[1], atol=1e-5)
