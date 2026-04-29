"""Bundle processed videos into a ZIP archive."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Optional


ProgressCallback = Callable[[float], None]


def export_zip(
    files: Iterable[str],
    zip_path: str,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
) -> str:
    """Create a ZIP archive at `zip_path` containing the given files.

    Each file is added under its basename (no directory structure).
    `progress_cb(0.0-1.0)` is called after each file is written.
    Returns the absolute path to the ZIP.
    """
    files = [f for f in files if f and os.path.isfile(f)]
    if not files:
        raise ValueError("No files to export.")

    os.makedirs(os.path.dirname(os.path.abspath(zip_path)) or ".", exist_ok=True)
    total = len(files)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, path in enumerate(files, start=1):
            if cancel_flag and cancel_flag():
                raise RuntimeError("ZIP export cancelled by user.")
            zf.write(path, arcname=Path(path).name)
            if progress_cb:
                progress_cb(i / total)

    return os.path.abspath(zip_path)
