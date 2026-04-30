"""Lazy fetcher for AI helper artefacts (Real-ESRGAN binary + Whisper models).

Both Real-ESRGAN and Whisper need bulky native artefacts that we don't want
to ship inside the source zip / Nuitka .exe. Instead they are fetched into a
per-user cache directory the first time the user picks an "AI" mode.

Cache layout (Windows shown):
    %LOCALAPPDATA%/Digitalinos/models/
        realesrgan/
            realesrgan-ncnn-vulkan.exe
            models/
                realesr-animevideov3-x2.bin
                realesr-animevideov3-x2.param
                realesr-animevideov3-x4.bin
                realesr-animevideov3-x4.param
        whisper/
            faster-whisper-tiny/
            faster-whisper-base/
            faster-whisper-small/

Linux + macOS use ``~/.local/share/Digitalinos`` and ``~/Library/Application
Support/Digitalinos`` respectively.

Fetching is incremental — each helper has a ``ensure_*`` function that
returns the on-disk path (downloading first if needed). All public helpers
accept an optional ``progress_cb(fraction: float, label: str)`` so callers
can show download progress in the UI.
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional


ProgressCallback = Callable[[float, str], None]


REALESRGAN_RELEASE = "v0.2.5.0"
REALESRGAN_URLS = {
    "Windows": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        f"{REALESRGAN_RELEASE}/realesrgan-ncnn-vulkan-20220424-windows.zip"
    ),
    "Linux": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        f"{REALESRGAN_RELEASE}/realesrgan-ncnn-vulkan-20220424-ubuntu.zip"
    ),
    "Darwin": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        f"{REALESRGAN_RELEASE}/realesrgan-ncnn-vulkan-20220424-macos.zip"
    ),
}


WHISPER_MODEL_REPOS = {
    # We use Systran's ctranslate2-converted faster-whisper models. Sizes
    # below are approximate — actually downloaded by huggingface_hub which
    # streams files with its own progress accounting.
    "tiny":  "Systran/faster-whisper-tiny",
    "base":  "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
}


# ---------------------------------------------------------------------------
# Cache directory resolution
# ---------------------------------------------------------------------------

def models_root() -> Path:
    """Return the per-user root for cached AI artefacts.

    Honours ``DIGITALINOS_MODELS_DIR`` for tests / advanced users.
    """
    env = os.environ.get("DIGITALINOS_MODELS_DIR")
    if env:
        return Path(env).expanduser().resolve()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Digitalinos" / "models"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Digitalinos" / "models"
    return Path.home() / ".local" / "share" / "Digitalinos" / "models"


# ---------------------------------------------------------------------------
# Real-ESRGAN binary
# ---------------------------------------------------------------------------

def realesrgan_dir() -> Path:
    return models_root() / "realesrgan"


def realesrgan_executable() -> Path:
    name = "realesrgan-ncnn-vulkan.exe" if sys.platform == "win32" else "realesrgan-ncnn-vulkan"
    return realesrgan_dir() / name


def realesrgan_models_dir() -> Path:
    return realesrgan_dir() / "models"


def realesrgan_is_installed() -> bool:
    """Either bundled in the cache, or available on PATH."""
    if realesrgan_executable().exists():
        return True
    return shutil.which("realesrgan-ncnn-vulkan") is not None


def ensure_realesrgan(progress_cb: Optional[ProgressCallback] = None) -> Path:
    """Ensure the Real-ESRGAN executable + bundled models are available.

    Returns the path to the executable. Downloads + extracts on first call.
    If a system-wide binary is on PATH that will be returned instead.
    """
    on_path = shutil.which("realesrgan-ncnn-vulkan")
    if on_path:
        return Path(on_path)

    exe = realesrgan_executable()
    if exe.exists() and realesrgan_models_dir().exists():
        return exe

    system = platform.system()
    url = REALESRGAN_URLS.get(system)
    if not url:
        raise RuntimeError(
            f"Real-ESRGAN binary is not available for {system!r}. "
            "Install `realesrgan-ncnn-vulkan` manually and ensure it is on PATH."
        )

    realesrgan_dir().mkdir(parents=True, exist_ok=True)

    if progress_cb:
        progress_cb(0.0, "Downloading Real-ESRGAN…")

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        _download(url, tmp_path, progress_cb, label="Downloading Real-ESRGAN…")
        if progress_cb:
            progress_cb(0.95, "Extracting Real-ESRGAN…")
        _extract_zip_flat(tmp_path, realesrgan_dir())
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not exe.exists():
        # The release zip extracts with the same filename on every platform,
        # but inside a top-level folder on some platforms. Walk + flatten.
        for found in realesrgan_dir().rglob(exe.name):
            shutil.move(str(found), str(exe))
            break

    if not exe.exists():
        raise RuntimeError(
            f"Real-ESRGAN download finished but {exe.name!r} was not found in the archive."
        )

    if sys.platform != "win32":
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    if progress_cb:
        progress_cb(1.0, "Real-ESRGAN ready.")
    return exe


# ---------------------------------------------------------------------------
# Whisper models
# ---------------------------------------------------------------------------

def whisper_dir() -> Path:
    return models_root() / "whisper"


def whisper_model_dir(size: str) -> Path:
    return whisper_dir() / f"faster-whisper-{size}"


def ensure_whisper_model(
    size: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> Path:
    """Make sure the chosen faster-whisper model is on disk.

    Returns the directory path that ``WhisperModel(model_size_or_path=…)``
    can be pointed at. Downloads via ``huggingface_hub`` on first call.
    """
    size = (size or "tiny").lower()
    if size not in WHISPER_MODEL_REPOS:
        raise ValueError(
            f"Unknown whisper model size {size!r}. "
            f"Expected one of: {sorted(WHISPER_MODEL_REPOS)}"
        )

    target = whisper_model_dir(size)
    sentinel = target / "model.bin"
    if sentinel.exists():
        return target

    target.mkdir(parents=True, exist_ok=True)

    if progress_cb:
        progress_cb(0.0, f"Downloading Whisper {size} model…")

    try:
        from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is required to download Whisper models. "
            "Run `pip install huggingface_hub`."
        ) from exc

    snapshot_download(
        repo_id=WHISPER_MODEL_REPOS[size],
        local_dir=str(target),
        local_dir_use_symlinks=False,
    )

    if progress_cb:
        progress_cb(1.0, f"Whisper {size} ready.")
    return target


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _download(
    url: str,
    dest_path: str,
    progress_cb: Optional[ProgressCallback],
    label: str,
) -> None:
    """Stream a download to ``dest_path`` with optional progress reporting."""
    req = urllib.request.Request(url, headers={"User-Agent": "Digitalinos/0.2"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        read = 0
        chunk = 64 * 1024
        with open(dest_path, "wb") as fh:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                fh.write(buf)
                read += len(buf)
                if progress_cb and total:
                    progress_cb(min(0.95, read / total * 0.95), label)


def _extract_zip_flat(zip_path: str, target: Path) -> None:
    """Extract a zip such that its leaf files end up directly inside ``target``.

    The Real-ESRGAN release zips contain a top-level folder. We strip it so
    callers find the binary at a stable path regardless of release naming.
    """
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        # Detect single common top-level folder.
        prefix = ""
        first = names[0].split("/", 1)[0]
        if all(n.startswith(first + "/") or n == first for n in names):
            prefix = first + "/"
        for name in names:
            if name.endswith("/"):
                continue
            rel = name[len(prefix):] if prefix else name
            if not rel:
                continue
            out_path = target / rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
