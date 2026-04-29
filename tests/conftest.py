"""pytest conftest: generate small sample videos with FFmpeg when available."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


@pytest.fixture(scope="session")
def ffmpeg_path() -> str:
    path = _ffmpeg()
    if not path:
        pytest.skip("ffmpeg not available on PATH")
    return path


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("vbp_samples")
    return d


def _generate(ffmpeg: str, out: Path, duration: int = 2, w: int = 320, h: int = 240) -> Path:
    """Create a synthetic test video via ffmpeg lavfi."""
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={w}x{h}:rate=24",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


@pytest.fixture(scope="session")
def sample_videos(ffmpeg_path: str, sample_dir: Path) -> list[Path]:
    videos = []
    for i in range(2):
        v = sample_dir / f"sample_{i}.mp4"
        if not v.exists():
            _generate(ffmpeg_path, v)
        videos.append(v)
    return videos
