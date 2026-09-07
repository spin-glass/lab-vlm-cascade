import pytest

from cascade.config import PinError, validate_pins


def test_rejects_latest_alias():
    with pytest.raises(PinError):
        validate_pins({"encoders": [{"hf_id": "x/y", "revision": "latest"}]})


def test_rejects_main_and_missing_revision():
    with pytest.raises(PinError):
        validate_pins({"e": {"hf_id": "x/y", "revision": "main"}})
    with pytest.raises(PinError):
        validate_pins({"e": {"hf_id": "x/y"}})


def test_accepts_pinned_hash():
    validate_pins(
        {
            "e": {"hf_id": "x/y", "revision": "d110532e8d4ff91c574ee60a342323f28468b287"},
            "taxonomy_version": "0.1.0",
        }
    )
