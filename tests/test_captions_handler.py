"""Behaviour tests for the auto-captions module.

Whisper itself is too heavy / network-dependent for CI so we monkey-patch
``faster_whisper.WhisperModel`` with a stub that emits a fixed list of
segments. The real ``transcribe_to_srt`` -> SRT writer -> FFmpeg burn-in
flow is exercised end-to-end.
"""

from __future__ import annotations

import os
import shutil
import sys
import types
from pathlib import Path

import pytest

from app.processing import ai_models, captions_handler
from app.processing.captions_handler import (
    CaptionOptions,
    CaptionStyle,
    DEFAULT_STYLE_PRESETS,
    add_auto_captions,
    burn_subtitles,
    transcribe_to_srt,
    _format_srt_time,
    _hex_to_ass_color,
)


def test_format_srt_time_padding():
    assert _format_srt_time(0) == "00:00:00,000"
    assert _format_srt_time(1.234) == "00:00:01,234"
    assert _format_srt_time(3661.5) == "01:01:01,500"


def test_hex_to_ass_color():
    # libass uses BGR with leading "00" alpha byte.
    assert _hex_to_ass_color("#FFFFFF") == "&H00FFFFFF"
    assert _hex_to_ass_color("#FFAA00") == "&H0000AAFF"
    assert _hex_to_ass_color("FFAA00") == "&H0000AAFF"
    assert _hex_to_ass_color("garbage") == "&H00FFFFFF"


def test_caption_style_presets_keys():
    assert "Clean (white)" in DEFAULT_STYLE_PRESETS
    assert "Bold (yellow)" in DEFAULT_STYLE_PRESETS
    for name, payload in DEFAULT_STYLE_PRESETS.items():
        assert "FontName" in payload, name
        assert "FontSize" in payload, name


def test_caption_style_default_force_style():
    s = CaptionStyle(mode="default", preset="Clean (white)")
    text = s.to_force_style()
    assert "FontName=DejaVu Sans" in text
    assert "PrimaryColour=&H00FFFFFF" in text


def test_caption_style_custom_force_style_includes_overrides():
    s = CaptionStyle(
        mode="custom",
        font_name="Arial",
        font_size=42,
        primary_color="#00FF00",
        outline_color="#000000",
        outline_width=4,
        bold=True,
        margin_v=80,
    )
    text = s.to_force_style()
    assert "FontName=Arial" in text
    assert "FontSize=42" in text
    assert "PrimaryColour=&H0000FF00" in text
    assert "Outline=4" in text
    assert "Bold=-1" in text


def test_add_captions_disabled_copies_through(sample_videos, tmp_path):
    src = str(sample_videos[0])
    out = str(tmp_path / "passthrough.mp4")
    srt = add_auto_captions(src, out, CaptionOptions(enabled=False))
    assert srt == ""
    assert os.path.exists(out)


@pytest.fixture
def stub_whisper(monkeypatch, tmp_path):
    """Replace faster-whisper WhisperModel with a tiny stub."""

    fake_dir = tmp_path / "fake-whisper-tiny"
    fake_dir.mkdir()
    (fake_dir / "model.bin").write_bytes(b"")  # sentinel only

    monkeypatch.setattr(
        ai_models, "ensure_whisper_model",
        lambda size, progress_cb=None: fake_dir,
    )

    class _StubSegment:
        def __init__(self, start: float, end: float, text: str) -> None:
            self.start = start
            self.end = end
            self.text = text

    class _StubModel:
        def __init__(self, *args, **kwargs) -> None:  # noqa: D401
            pass

        def transcribe(self, *args, **kwargs):
            segs = [
                _StubSegment(0.0, 1.5, "Hello world"),
                _StubSegment(1.6, 3.0, "This is a captions test"),
            ]
            return iter(segs), types.SimpleNamespace(language="en", duration=3.0)

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = _StubModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return fake_dir


def test_transcribe_to_srt_writes_valid_file(sample_videos, tmp_path, stub_whisper):
    src = str(sample_videos[0])
    srt = str(tmp_path / "out.srt")
    transcribe_to_srt(src, srt, model_size="tiny")
    body = Path(srt).read_text(encoding="utf-8")
    assert "Hello world" in body
    assert "This is a captions test" in body
    # Two segments => indices 1 and 2.
    assert body.startswith("1\n")
    assert "2\n" in body
    # SRT time-stamp format
    assert "00:00:00,000 --> 00:00:01,500" in body


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required.")
def test_burn_subtitles_runs_e2e(sample_videos, tmp_path):
    src = str(sample_videos[0])
    srt = tmp_path / "in.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nBurn me\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nAnd me\n",
        encoding="utf-8",
    )
    out = str(tmp_path / "burned.mp4")
    burn_subtitles(src, str(srt), out, CaptionStyle(mode="default", preset="Clean (white)"))
    assert os.path.exists(out)
    assert os.path.getsize(out) > 0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required.")
def test_add_auto_captions_full_flow(sample_videos, tmp_path, stub_whisper):
    src = str(sample_videos[0])
    out = str(tmp_path / "captioned.mp4")
    srt_path = str(tmp_path / "out.srt")
    written = add_auto_captions(
        src,
        out,
        CaptionOptions(
            enabled=True,
            model_size="tiny",
            srt_output_path=srt_path,
            style=CaptionStyle(mode="default", preset="Bold (yellow)"),
        ),
    )
    assert written == srt_path
    assert os.path.exists(out)
    assert "Hello world" in Path(srt_path).read_text(encoding="utf-8")
