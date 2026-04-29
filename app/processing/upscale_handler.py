"""Upscaling: Real-ESRGAN (if available) with FFmpeg lanczos fallback.

Real-ESRGAN is a frame-based upscaler: it takes images in and produces images
out. For video we extract frames, upscale each, then reassemble with FFmpeg.
This module exposes a single public helper -- `upscale_video` -- that chooses
the best available backend automatically.

Because Real-ESRGAN is *very* compute-heavy for long clips, the default
"FFmpeg fallback" pathway is also wired through `ffmpeg_handler.process_video`
via the encode options (the UI sets `target_height` directly). This module is
primarily used when the user explicitly chooses the Real-ESRGAN backend.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .ffmpeg_handler import FFmpegError, ffmpeg_available, probe_video


REALESRGAN_BINARY_CANDIDATES = (
    "realesrgan-ncnn-vulkan",
    "realesrgan-ncnn-vulkan.exe",
    "Real-ESRGAN-ncnn-vulkan.exe",
)


def realesrgan_binary() -> Optional[str]:
    """Return the path to a realesrgan binary on PATH, or None."""
    for name in REALESRGAN_BINARY_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def realesrgan_available() -> bool:
    return realesrgan_binary() is not None


@dataclass
class UpscaleOptions:
    """Settings for the upscaling stage."""
    enabled: bool = False
    target_height: int = 2160          # e.g. 2160 = 4K
    model: str = "realesr-animevideov3"  # built-in model name
    scale: int = 2                       # 2, 3, or 4 for Real-ESRGAN
    backend: str = "auto"                # "auto" | "realesrgan" | "ffmpeg"
    tile_size: int = 0                   # 0 = auto


ProgressCallback = Callable[[float], None]


def upscale_video(
    input_path: str,
    output_path: str,
    options: UpscaleOptions,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
) -> str:
    """Upscale a video, returning the path to the produced file.

    Picks a backend according to `options.backend`:
        - "realesrgan": require Real-ESRGAN; raise if missing.
        - "ffmpeg":    use FFmpeg lanczos scaling (fast, good enough baseline).
        - "auto":      use Real-ESRGAN if available, otherwise FFmpeg.
    """
    if not options.enabled:
        # Nothing to do -- caller should skip upscaling.
        if os.path.abspath(input_path) != os.path.abspath(output_path):
            shutil.copyfile(input_path, output_path)
        return output_path

    backend = options.backend
    if backend == "auto":
        backend = "realesrgan" if realesrgan_available() else "ffmpeg"

    if backend == "realesrgan":
        if not realesrgan_available():
            raise FFmpegError(
                "Real-ESRGAN binary not found. Install `realesrgan-ncnn-vulkan` "
                "and ensure it is on PATH, or switch to the FFmpeg backend."
            )
        return _upscale_with_realesrgan(
            input_path, output_path, options, progress_cb, cancel_flag
        )

    # ffmpeg fallback
    return _upscale_with_ffmpeg(
        input_path, output_path, options, progress_cb, cancel_flag
    )


# ---------------------------------------------------------------------------
# FFmpeg fallback
# ---------------------------------------------------------------------------

def _upscale_with_ffmpeg(
    input_path: str,
    output_path: str,
    options: UpscaleOptions,
    progress_cb: Optional[ProgressCallback],
    cancel_flag: Optional[Callable[[], bool]],
) -> str:
    if not ffmpeg_available():
        raise FFmpegError("ffmpeg not found on PATH.")

    info = probe_video(input_path)
    target_h = options.target_height
    if target_h <= 0:
        target_h = info.height * max(1, options.scale)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats",
        "-i", input_path,
        "-vf", f"scale=-2:{target_h}:flags=lanczos",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    total = max(info.duration, 0.001)
    try:
        assert proc.stdout is not None
        import re
        time_re = re.compile(r"out_time_ms=(\d+)")
        for line in proc.stdout:
            if cancel_flag and cancel_flag():
                proc.terminate()
                raise FFmpegError("Upscale cancelled by user.")
            m = time_re.search(line)
            if m and progress_cb:
                progress_cb(min(1.0, (int(m.group(1)) / 1_000_000.0) / total))
    finally:
        if proc.stdout:
            proc.stdout.close()
        rc = proc.wait()
        err = proc.stderr.read() if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        if rc != 0:
            raise FFmpegError(f"FFmpeg upscale failed (rc={rc}): {err.strip()}")
    return output_path


# ---------------------------------------------------------------------------
# Real-ESRGAN (frame-by-frame)
# ---------------------------------------------------------------------------

def _upscale_with_realesrgan(
    input_path: str,
    output_path: str,
    options: UpscaleOptions,
    progress_cb: Optional[ProgressCallback],
    cancel_flag: Optional[Callable[[], bool]],
) -> str:
    if not ffmpeg_available():
        raise FFmpegError("ffmpeg is required for Real-ESRGAN frame assembly.")
    binary = realesrgan_binary()
    if not binary:
        raise FFmpegError("Real-ESRGAN binary not found on PATH.")

    info = probe_video(input_path)
    fps = info.fps or 30.0

    with tempfile.TemporaryDirectory(prefix="vbp_esrgan_") as tmp:
        frames_in = os.path.join(tmp, "in")
        frames_out = os.path.join(tmp, "out")
        os.makedirs(frames_in, exist_ok=True)
        os.makedirs(frames_out, exist_ok=True)

        # 1. Extract frames.
        if progress_cb:
            progress_cb(0.02)
        extract_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", input_path,
            os.path.join(frames_in, "frame_%08d.png"),
        ]
        rc = subprocess.call(extract_cmd)
        if rc != 0:
            raise FFmpegError("Failed to extract frames for Real-ESRGAN upscaling.")
        if cancel_flag and cancel_flag():
            raise FFmpegError("Upscale cancelled by user.")

        # 2. Upscale frames via Real-ESRGAN ncnn.
        if progress_cb:
            progress_cb(0.15)
        esrgan_cmd = [
            binary,
            "-i", frames_in,
            "-o", frames_out,
            "-n", options.model,
            "-s", str(options.scale),
        ]
        if options.tile_size:
            esrgan_cmd += ["-t", str(options.tile_size)]
        rc = subprocess.call(esrgan_cmd)
        if rc != 0:
            raise FFmpegError("Real-ESRGAN upscaling failed.")
        if cancel_flag and cancel_flag():
            raise FFmpegError("Upscale cancelled by user.")

        # 3. Re-assemble video + copy original audio.
        if progress_cb:
            progress_cb(0.75)
        assemble_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", f"{fps:.6f}",
            "-i", os.path.join(frames_out, "frame_%08d.png"),
            "-i", input_path,
            "-map", "0:v:0", "-map", "1:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ]
        rc = subprocess.call(assemble_cmd)
        if rc != 0:
            raise FFmpegError("Failed to reassemble upscaled frames into a video.")

    if progress_cb:
        progress_cb(1.0)
    return output_path
