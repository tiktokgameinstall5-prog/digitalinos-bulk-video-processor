"""Tests for ffmpeg_handler: probing, overlay graph construction, full encode."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.processing.ffmpeg_handler import (
    EncodeOptions,
    QUALITY_TEMPLATES,
    WatermarkSettings,
    apply_quality_template,
    build_command_preview,
    build_overlay_filtergraph,
    probe_video,
    process_video,
)


def test_overlay_text_filtergraph_contains_drawtext() -> None:
    wm = WatermarkSettings(enabled=True, mode="text", text="Hello", position="bottom-right")
    extras, parts = build_overlay_filtergraph(wm, 1920, 1080)
    assert extras == []
    assert len(parts) == 1
    assert "drawtext=" in parts[0]
    assert "[0:v]" in parts[0] and "[vout]" in parts[0]


def test_overlay_text_escapes_special_chars() -> None:
    wm = WatermarkSettings(enabled=True, mode="text", text="a:b'c%d")
    _, parts = build_overlay_filtergraph(wm, 1920, 1080)
    assert r"a\:b\'c\%d" in parts[0]


def test_overlay_text_has_drop_shadow_by_default() -> None:
    wm = WatermarkSettings(enabled=True, mode="text", text="Hello")
    _, parts = build_overlay_filtergraph(wm, 1920, 1080)
    assert "shadowcolor" in parts[0]
    assert "shadowx=2" in parts[0]
    assert "shadowy=2" in parts[0]


def test_overlay_text_shadow_can_be_disabled() -> None:
    wm = WatermarkSettings(enabled=True, mode="text", text="Hello", shadow=False)
    _, parts = build_overlay_filtergraph(wm, 1920, 1080)
    assert "shadowcolor" not in parts[0]


def test_overlay_text_with_font_file(tmp_path: Path) -> None:
    font = tmp_path / "fake.ttf"
    font.write_bytes(b"\x00" * 4)
    wm = WatermarkSettings(enabled=True, mode="text", text="Hi", font_file=str(font))
    _, parts = build_overlay_filtergraph(wm, 1920, 1080)
    assert "fontfile=" in parts[0]


def test_overlay_image_uses_loop_and_setsar(tmp_path: Path) -> None:
    img = tmp_path / "logo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # header only; no validation here
    wm = WatermarkSettings(enabled=True, mode="image", image_path=str(img))
    extras, parts = build_overlay_filtergraph(wm, 1920, 1080)
    assert extras == ["-loop", "1", "-i", str(img)]
    assert "setsar=1" in parts[0]
    assert "format=rgba" in parts[0]


def test_quality_templates_registered() -> None:
    assert "Original (no changes)" in QUALITY_TEMPLATES
    assert "YouTube 1080p Clean" in QUALITY_TEMPLATES
    assert "CapCut Ultra HD (1440p)" in QUALITY_TEMPLATES
    assert "4K Crisp (2160p)" in QUALITY_TEMPLATES
    assert "Fast Preview" in QUALITY_TEMPLATES


def test_apply_quality_template_4k() -> None:
    base = EncodeOptions(
        watermark=WatermarkSettings(enabled=False),
        target_height=None,
        preset="medium",
        crf=23,
    )
    out = apply_quality_template(base, "4K Crisp (2160p)")
    assert out.target_height == 2160
    assert out.preset == "slow"
    assert out.crf == 16
    assert out.sharpen is True
    assert out.sharpen_amount > 0
    assert out.denoise is True
    assert out.color_boost is True


def test_apply_quality_template_unknown_returns_input() -> None:
    base = EncodeOptions(watermark=WatermarkSettings(enabled=False), crf=22)
    out = apply_quality_template(base, "does-not-exist")
    assert out is base


def test_command_preview_includes_sharpen_when_enabled() -> None:
    wm = WatermarkSettings(enabled=True, mode="text", text="Hi")
    info_like = type("I", (), {
        "width": 1920, "height": 1080, "duration": 1.0, "fps": 24,
        "codec": "h264", "path": "x.mp4",
        "resolution": "1920x1080", "duration_hms": "00:00:01",
    })
    options = EncodeOptions(
        watermark=wm, target_height=1440, preset="slow", crf=18,
        sharpen=True, sharpen_amount=0.9,
    )
    cmd = build_command_preview("in.mp4", "out.mp4", info_like, options)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "unsharp=" in graph
    assert "scale=-2:1440" in graph


def test_bounce_text_uses_triangle_wave() -> None:
    wm = WatermarkSettings(
        enabled=True, mode="text", text="Hi",
        bounce=True, bounce_speed="slow",
    )
    _, parts = build_overlay_filtergraph(wm, 1920, 1080)
    graph = parts[0]
    assert "abs(mod(t*40" in graph
    assert "text_w" in graph
    assert "text_h" in graph


def test_bounce_text_speed_fast() -> None:
    wm = WatermarkSettings(
        enabled=True, mode="text", text="Hi",
        bounce=True, bounce_speed="fast",
    )
    _, parts = build_overlay_filtergraph(wm, 1920, 1080)
    assert "abs(mod(t*160" in parts[0]


def test_bounce_image_overlay(tmp_path: Path) -> None:
    img = tmp_path / "logo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    wm = WatermarkSettings(
        enabled=True, mode="image", image_path=str(img),
        bounce=True, bounce_speed="medium",
    )
    _, parts = build_overlay_filtergraph(wm, 1920, 1080)
    # Should contain the bounce expressions against main W/H and overlay w/h.
    joined = ";".join(parts)
    assert "W-w" in joined and "H-h" in joined
    assert "abs(mod(t*90" in joined


def test_overlay_disabled_returns_empty() -> None:
    wm = WatermarkSettings(enabled=False)
    extras, parts = build_overlay_filtergraph(wm, 1920, 1080)
    assert extras == []
    assert parts == []


def test_overlay_image_requires_existing_file(tmp_path: Path) -> None:
    wm = WatermarkSettings(enabled=True, mode="image", image_path=str(tmp_path / "nope.png"))
    with pytest.raises(Exception):
        build_overlay_filtergraph(wm, 1920, 1080)


def test_build_command_preview_has_required_flags() -> None:
    wm = WatermarkSettings(enabled=True, mode="text", text="Hi")
    info_like = type("I", (), {"width": 1920, "height": 1080, "duration": 1.0, "fps": 24, "codec": "h264", "path": "x.mp4", "resolution": "1920x1080", "duration_hms": "00:00:01"})
    options = EncodeOptions(watermark=wm, target_height=2160)
    cmd = build_command_preview("in.mp4", "out.mp4", info_like, options)
    assert "ffmpeg" in cmd[0]
    assert "-filter_complex" in cmd
    idx = cmd.index("-filter_complex")
    graph = cmd[idx + 1]
    assert "drawtext=" in graph
    assert "scale=-2:2160" in graph


# -----------------------------------------------------------------------
# Integration tests requiring ffmpeg
# -----------------------------------------------------------------------

def test_probe_video(sample_videos) -> None:
    info = probe_video(str(sample_videos[0]))
    assert info.width == 320
    assert info.height == 240
    assert info.duration > 0
    assert info.codec


def test_process_video_produces_output(sample_videos, tmp_path: Path) -> None:
    src = sample_videos[0]
    out = tmp_path / "processed.mp4"
    info = probe_video(str(src))

    wm = WatermarkSettings(enabled=True, mode="text", text="VBP", position="top-left")
    opts = EncodeOptions(watermark=wm, target_height=480, preset="ultrafast", crf=28)

    progress_values: list[float] = []
    process_video(str(src), str(out), info, opts, progress_cb=progress_values.append)

    assert out.exists()
    assert out.stat().st_size > 0
    # Confirm progress callback was invoked.
    assert len(progress_values) > 0
    assert progress_values[-1] >= 0.9

    # Output should be scaled up to 480p height.
    out_info = probe_video(str(out))
    assert out_info.height == 480


def test_process_video_with_bouncing_text(sample_videos, tmp_path: Path) -> None:
    """End-to-end: real ffmpeg must accept the bounce expression."""
    src = sample_videos[0]
    out = tmp_path / "bounce_text.mp4"
    info = probe_video(str(src))
    wm = WatermarkSettings(
        enabled=True, mode="text", text="BOUNCE",
        bounce=True, bounce_speed="slow",
    )
    opts = EncodeOptions(watermark=wm, preset="ultrafast", crf=28)
    process_video(str(src), str(out), info, opts)
    assert out.exists() and out.stat().st_size > 0


def test_process_video_with_image_overlay(sample_videos, tmp_path: Path) -> None:
    # Create a tiny PNG logo via ffmpeg.
    import subprocess
    logo = tmp_path / "logo.png"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=64x32",
            "-frames:v", "1", str(logo),
        ],
        check=True,
    )

    src = sample_videos[0]
    out = tmp_path / "logo_overlay.mp4"
    info = probe_video(str(src))

    wm = WatermarkSettings(
        enabled=True, mode="image", image_path=str(logo),
        position="bottom-right", opacity=0.9, scale=1.0,
    )
    opts = EncodeOptions(watermark=wm, preset="ultrafast", crf=28)
    process_video(str(src), str(out), info, opts)
    assert out.exists()
    assert out.stat().st_size > 0
