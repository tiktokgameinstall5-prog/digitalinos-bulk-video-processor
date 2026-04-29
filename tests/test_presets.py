"""Tests for utils.presets."""

from __future__ import annotations

from pathlib import Path

from app.processing.ffmpeg_handler import WatermarkSettings
from app.processing.upscale_handler import UpscaleOptions
from app.utils.presets import (
    delete_preset,
    list_presets,
    load_preset,
    save_preset,
)


def test_save_load_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIDEO_BATCH_PRO_PRESETS_DIR", str(tmp_path))
    wm = WatermarkSettings(
        enabled=True, mode="text", text="hello", position="top-right",
        opacity=0.7, scale=1.2, font_size=48, font_color="yellow",
    )
    up = UpscaleOptions(enabled=True, target_height=2160, backend="ffmpeg")
    path = save_preset("YouTube watermark", wm, up)
    assert Path(path).exists()

    assert "YouTube watermark" in list_presets()

    wm2, up2 = load_preset("YouTube watermark")
    assert wm2 == wm
    assert up2 == up


def test_delete_preset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VIDEO_BATCH_PRO_PRESETS_DIR", str(tmp_path))
    save_preset("tmp", WatermarkSettings(), UpscaleOptions())
    assert delete_preset("tmp") is True
    assert delete_preset("tmp") is False
