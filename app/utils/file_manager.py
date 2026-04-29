"""Filesystem helpers and output-path management."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Iterable


VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm",
    ".flv", ".m4v", ".mpg", ".mpeg", ".wmv", ".ts",
}


def is_video_file(path: str) -> bool:
    """Return True if `path` has a known video extension and exists on disk."""
    if not os.path.isfile(path):
        return False
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def filter_video_paths(paths: Iterable[str]) -> list[str]:
    """Filter an iterable of paths down to files that look like videos."""
    return [p for p in paths if is_video_file(p)]


_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename(name: str) -> str:
    """Replace any character not in [A-Za-z0-9._-] with `_`."""
    base = Path(name).name
    safe = _SANITIZE_RE.sub("_", base)
    return safe or "output"


def build_output_path(
    input_path: str,
    output_dir: str,
    suffix: str = "_processed",
    ext: str = ".mp4",
) -> str:
    """Return `<output_dir>/<sanitized stem><suffix><ext>`.

    Resolves collisions by appending `_1`, `_2`, ...
    """
    os.makedirs(output_dir, exist_ok=True)
    stem = sanitize_filename(Path(input_path).stem)
    candidate = os.path.join(output_dir, f"{stem}{suffix}{ext}")
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(output_dir, f"{stem}{suffix}_{i}{ext}")
        i += 1
    return candidate


def random_code(length: int = 6) -> str:
    """Return a random lowercase hex code (default 6 chars)."""
    return secrets.token_hex(max(1, (length + 1) // 2))[:length]


def build_sequential_output_paths(
    input_paths: list[str],
    output_dir: str,
    ext: str = ".mp4",
    pad: int | None = None,
) -> list[str]:
    """Return a list of numbered output paths, one per input.

    Naming scheme: `{index}_{random6}{ext}` where `index` starts at 1 and is
    zero-padded to fit the batch size (e.g. batch of 12 -> `01_ab12cd.mp4`).

    - Each file gets its own random code so the same preset run against the
      same inputs twice produces different output names.
    - Index padding width auto-sized to the total count unless `pad` is given.
    """
    os.makedirs(output_dir, exist_ok=True)
    total = len(input_paths)
    if total == 0:
        return []
    if pad is None:
        pad = max(1, len(str(total)))

    used: set[str] = set()
    outputs: list[str] = []
    for i in range(1, total + 1):
        # Avoid collisions within the batch (very unlikely but cheap to guard).
        while True:
            code = random_code(6)
            name = f"{str(i).zfill(pad)}_{code}{ext}"
            candidate = os.path.join(output_dir, name)
            if not os.path.exists(candidate) and candidate not in used:
                used.add(candidate)
                outputs.append(candidate)
                break
    return outputs


def ensure_dir(path: str) -> str:
    """mkdir -p and return the path."""
    os.makedirs(path, exist_ok=True)
    return path


def human_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable string."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"
