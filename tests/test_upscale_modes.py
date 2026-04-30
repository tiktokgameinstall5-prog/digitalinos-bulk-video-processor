"""Behaviour tests for the three upscale modes.

Real-ESRGAN itself is heavy + network-dependent so the AI mode test
monkey-patches ``ai_models.ensure_realesrgan`` to point at a no-op script
that copies its input frames to the output. This still exercises the real
extract → upscale → reassemble FFmpeg pipeline; only the model inference
is stubbed.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from app.processing import ai_models
from app.processing.ffmpeg_handler import probe_video
from app.processing.upscale_handler import (
    UPSCALE_MODES,
    UpscaleOptions,
    upscale_video,
)


def _video_dims(path: str) -> tuple[int, int]:
    info = probe_video(path)
    return info.width, info.height


def test_fast_mode_rescales_with_lanczos(sample_videos, tmp_path):
    src = str(sample_videos[0])
    out = str(tmp_path / "fast.mp4")
    upscale_video(
        src,
        out,
        UpscaleOptions(enabled=True, mode="fast", target_height=480),
    )
    w, h = _video_dims(out)
    assert h == 480
    # Width is auto-derived (-2) so it must be even and proportional.
    assert w % 2 == 0


def test_balanced_mode_runs(sample_videos, tmp_path):
    src = str(sample_videos[0])
    out = str(tmp_path / "balanced.mp4")
    upscale_video(
        src,
        out,
        UpscaleOptions(enabled=True, mode="balanced", target_height=480),
    )
    assert os.path.exists(out)
    assert os.path.getsize(out) > 0


def test_disabled_passes_through(sample_videos, tmp_path):
    src = str(sample_videos[0])
    out = str(tmp_path / "passthrough.mp4")
    upscale_video(src, out, UpscaleOptions(enabled=False))
    assert os.path.exists(out)
    # File contents identical (a copy).
    assert os.path.getsize(out) == os.path.getsize(src)


def test_ai_mode_falls_back_when_binary_missing(sample_videos, tmp_path, monkeypatch):
    src = str(sample_videos[0])
    out = str(tmp_path / "ai_fallback.mp4")

    def _boom(progress_cb=None):
        raise RuntimeError("Real-ESRGAN cannot be downloaded in tests.")

    monkeypatch.setattr(ai_models, "ensure_realesrgan", _boom)

    logged: list[str] = []
    upscale_video(
        src,
        out,
        UpscaleOptions(enabled=True, mode="ai", target_height=480),
        log_cb=logged.append,
    )
    assert os.path.exists(out)
    assert any("falling back to Fast" in m for m in logged), logged


def test_legacy_backend_field_maps_to_mode():
    opts = UpscaleOptions(enabled=True, mode="", backend="realesrgan")
    assert opts.resolved_mode() == "ai"
    opts = UpscaleOptions(enabled=True, mode="", backend="ffmpeg")
    assert opts.resolved_mode() == "fast"
    opts = UpscaleOptions(enabled=True, mode="balanced", backend="realesrgan")
    # Explicit mode wins.
    assert opts.resolved_mode() == "balanced"


def test_modes_constant():
    assert UPSCALE_MODES == ("fast", "balanced", "ai")


@pytest.mark.skipif(
    not shutil.which("ffmpeg"), reason="ffmpeg is required for the AI stub test."
)
def test_ai_mode_with_stub_binary(sample_videos, tmp_path, monkeypatch):
    """End-to-end AI pipeline with a stub binary that just copies frames.

    Validates: extract frames → call binary → reassemble video → mux audio.
    The fake "upscaler" just copies its input directory to the output.
    """
    src = str(sample_videos[0])
    out = str(tmp_path / "ai_stubbed.mp4")

    stub_path = tmp_path / "fake_realesrgan.sh"
    stub_path.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'in_dir=""; out_dir=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -i) in_dir="$2"; shift 2;;\n'
        '    -o) out_dir="$2"; shift 2;;\n'
        '    *) shift;;\n'
        '  esac\n'
        'done\n'
        'mkdir -p "$out_dir"\n'
        'cp -r "$in_dir"/. "$out_dir"/\n'
    )
    stub_path.chmod(stub_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    monkeypatch.setattr(ai_models, "ensure_realesrgan", lambda progress_cb=None: stub_path)

    upscale_video(
        src,
        out,
        UpscaleOptions(enabled=True, mode="ai", target_height=480, scale=2),
    )
    assert os.path.exists(out)
    # Output should be playable (probe must succeed).
    info = probe_video(out)
    assert info.height > 0
    assert info.width > 0
