"""End-to-end integration: process multiple files and export ZIP.

This tests the core pipeline without requiring a running Qt event loop.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from app.processing.ffmpeg_handler import (
    EncodeOptions,
    WatermarkSettings,
    probe_video,
    process_video,
)
from app.utils.file_manager import build_output_path
from app.utils.zip_export import export_zip


def test_multi_file_process_and_zip(sample_videos, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    wm = WatermarkSettings(
        enabled=True, mode="text", text="Batch",
        position="bottom-right", opacity=0.8, scale=1.0, font_size=24,
    )
    opts = EncodeOptions(watermark=wm, preset="ultrafast", crf=28)

    outputs = []
    for src in sample_videos:
        info = probe_video(str(src))
        out_path = build_output_path(str(src), str(out_dir))
        process_video(str(src), out_path, info, opts)
        outputs.append(out_path)

    # All outputs exist and are non-empty.
    assert len(outputs) == len(sample_videos) >= 2
    for p in outputs:
        assert os.path.isfile(p)
        assert os.path.getsize(p) > 0

    # Zip export packages all outputs.
    zip_path = tmp_path / "all.zip"
    export_zip(outputs, str(zip_path))
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for p in outputs:
            assert os.path.basename(p) in names
