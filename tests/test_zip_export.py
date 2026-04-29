"""Tests for utils.zip_export."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from app.utils.zip_export import export_zip


def test_export_zip_contains_all_files(tmp_path: Path) -> None:
    files = []
    for i in range(3):
        p = tmp_path / f"f{i}.bin"
        p.write_bytes(os.urandom(64))
        files.append(str(p))

    zip_path = tmp_path / "out.zip"
    progress = []
    result = export_zip(files, str(zip_path), progress_cb=progress.append)

    assert result == str(zip_path.resolve())
    assert zip_path.exists()
    assert progress[-1] == pytest.approx(1.0)

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert names == {"f0.bin", "f1.bin", "f2.bin"}


def test_export_zip_skips_missing(tmp_path: Path) -> None:
    good = tmp_path / "real.mp4"
    good.write_bytes(b"\x00\x01")
    zip_path = tmp_path / "a.zip"
    export_zip([str(good), str(tmp_path / "nope.mp4")], str(zip_path))
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.namelist() == ["real.mp4"]


def test_export_zip_empty_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_zip([], str(tmp_path / "x.zip"))
