"""Tests for utils.file_manager."""

from __future__ import annotations

import os
from pathlib import Path

from app.utils.file_manager import (
    build_output_path,
    build_sequential_output_paths,
    filter_video_paths,
    human_size,
    is_video_file,
    random_code,
    sanitize_filename,
)


def test_sanitize_filename_strips_bad_chars() -> None:
    assert sanitize_filename("my  video??.mp4") == "my_video_.mp4"
    assert sanitize_filename("") == "output"
    assert sanitize_filename("clean-name_1.mp4") == "clean-name_1.mp4"


def test_is_video_file(tmp_path: Path) -> None:
    good = tmp_path / "a.mp4"
    good.write_bytes(b"\x00")
    assert is_video_file(str(good))
    bad = tmp_path / "b.txt"
    bad.write_bytes(b"")
    assert not is_video_file(str(bad))
    assert not is_video_file(str(tmp_path / "missing.mp4"))


def test_filter_video_paths(tmp_path: Path) -> None:
    v = tmp_path / "a.mov"
    v.write_bytes(b"\x00")
    t = tmp_path / "b.txt"
    t.write_bytes(b"")
    out = filter_video_paths([str(v), str(t), "/no/such/path.mp4"])
    assert out == [str(v)]


def test_build_output_path_collision(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    src.write_bytes(b"\x00")
    out_dir = tmp_path / "out"
    a = build_output_path(str(src), str(out_dir))
    Path(a).write_bytes(b"x")
    b = build_output_path(str(src), str(out_dir))
    assert a != b
    assert b.endswith("_processed_1.mp4")


def test_random_code_length_and_hex() -> None:
    code = random_code(6)
    assert len(code) == 6
    assert all(c in "0123456789abcdef" for c in code)


def test_build_sequential_output_paths(tmp_path: Path) -> None:
    inputs = [f"/src/vid{i}.mp4" for i in range(1, 6)]
    out_dir = tmp_path / "out"
    paths = build_sequential_output_paths(inputs, str(out_dir))
    assert len(paths) == 5
    names = [os.path.basename(p) for p in paths]
    # 1-based, zero-padded to len(5)=1, so "1_xxxxxx.mp4" .. "5_xxxxxx.mp4"
    for i, name in enumerate(names, start=1):
        assert name.startswith(f"{i}_")
        assert name.endswith(".mp4")
    # All unique
    assert len(set(paths)) == 5


def test_build_sequential_output_paths_pads_for_large_batches(tmp_path: Path) -> None:
    inputs = [f"/src/vid{i}.mp4" for i in range(1, 13)]  # 12 files
    paths = build_sequential_output_paths(inputs, str(tmp_path))
    names = [os.path.basename(p) for p in paths]
    assert names[0].startswith("01_")
    assert names[-1].startswith("12_")


def test_build_sequential_output_paths_empty(tmp_path: Path) -> None:
    assert build_sequential_output_paths([], str(tmp_path)) == []


def test_human_size() -> None:
    assert human_size(0).endswith("B")
    assert "KB" in human_size(2048)
    assert "MB" in human_size(5 * 1024 * 1024)
