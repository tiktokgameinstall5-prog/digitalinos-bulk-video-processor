"""Upscaling pipeline with three modes: Fast, Balanced, AI (Real-ESRGAN).

Each mode trades quality for speed:

* ``fast``     — single-pass FFmpeg ``scale=lanczos`` resize. Instant, no
                  detail recovery; just a clean re-sample.
* ``balanced`` — same Lanczos resize plus a mild ``hqdn3d`` denoise (so
                  upscaled grain doesn't pop) and a moderate ``unsharp`` /
                  ``eq`` pass. No AI, but visibly nicer than ``fast`` on
                  low-bitrate input.
* ``ai``       — frame-by-frame Real-ESRGAN super-resolution via the
                  ``realesrgan-ncnn-vulkan`` binary, then re-encoded with
                  FFmpeg. The original audio is muxed back in untouched so
                  sync is byte-accurate.

The ``ai`` mode is the only one that does anything Real about "real
upscaling" — the other two are convenience labels around FFmpeg's own
filter chain. If the AI binary or model files cannot be obtained the AI
pipeline falls back to ``fast`` mode automatically and logs the failure
through ``progress_cb`` (label-aware overload).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import ai_models
from .ffmpeg_handler import FFmpegError, ffmpeg_available, probe_video


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

UPSCALE_MODES = ("fast", "balanced", "ai")


# Real-ESRGAN ships with a few built-in models. We pick a sensible default
# but expose all of them so power users can override via ``UpscaleOptions``.
REALESRGAN_MODELS = (
    "realesr-animevideov3",   # general-purpose 2x/3x/4x; small + fast
    "realesrgan-x4plus",      # photographic 4x; bigger + slower
    "realesrgan-x4plus-anime",
)


@dataclass
class UpscaleOptions:
    """Settings for the upscaling stage.

    ``mode`` is the new public knob (``fast`` / ``balanced`` / ``ai``).
    The legacy ``backend`` field is preserved for serialized presets:
    when present it overrides ``mode`` (``"realesrgan"`` -> ``"ai"``,
    ``"ffmpeg"`` -> ``"fast"``, ``"auto"`` -> ``"fast"``).
    """

    enabled: bool = False
    target_height: int = 1080            # 0 means "respect target_width / scale"
    target_width: int = 0                # used by Portrait HD (1080x1920)
    mode: str = "fast"                   # "fast" | "balanced" | "ai"

    # AI tunables
    model: str = "realesr-animevideov3"
    scale: int = 2                       # 2 / 3 / 4 — Real-ESRGAN frame scale
    tile_size: int = 0                   # 0 = auto (Real-ESRGAN -t flag)

    # Legacy field, kept for backwards-compatible preset loading.
    backend: str = ""

    def resolved_mode(self) -> str:
        """Pick the effective mode, accounting for legacy preset values."""
        if self.mode in UPSCALE_MODES:
            return self.mode
        legacy = (self.backend or "").lower()
        if legacy == "realesrgan":
            return "ai"
        if legacy == "ffmpeg":
            return "fast"
        return "fast"


# Plain-progress callable used by ``upscale_video``.
ProgressCallback = Callable[[float], None]
# Two-arg callable supports a status-message channel; either signature
# is accepted for backwards compatibility.
LabelledProgressCallback = Callable[[float, str], None]


def realesrgan_available() -> bool:
    """True if Real-ESRGAN is already on disk (cache or PATH).

    Does NOT trigger a download. ``upscale_video`` does that lazily.
    """
    return ai_models.realesrgan_is_installed()


def upscale_video(
    input_path: str,
    output_path: str,
    options: UpscaleOptions,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> str:
    """Run the upscale pipeline. Returns the path to the produced file.

    * ``options.enabled == False`` → copy input to output unchanged.
    * ``mode == "ai"`` and Real-ESRGAN unavailable → fall back to ``fast``.
    """
    if not options.enabled:
        if os.path.abspath(input_path) != os.path.abspath(output_path):
            shutil.copyfile(input_path, output_path)
        return output_path

    mode = options.resolved_mode()

    if mode == "ai":
        try:
            return _upscale_ai(
                input_path,
                output_path,
                options,
                progress_cb,
                cancel_flag,
                log_cb,
            )
        except (FFmpegError, RuntimeError, OSError) as exc:
            if log_cb:
                log_cb(f"[upscale] AI mode failed ({exc}); falling back to Fast.")
            return _upscale_ffmpeg(
                input_path,
                output_path,
                options,
                progress_cb,
                cancel_flag,
                balanced=False,
            )

    return _upscale_ffmpeg(
        input_path,
        output_path,
        options,
        progress_cb,
        cancel_flag,
        balanced=(mode == "balanced"),
    )


# ---------------------------------------------------------------------------
# FFmpeg modes (fast / balanced)
# ---------------------------------------------------------------------------

def _resolve_target_size(
    options: UpscaleOptions, info_height: int, info_width: int
) -> tuple[int, int]:
    """Compute the desired output (width, height) for a given input.

    Returns ``(width, height)``; either may be ``-2`` to mean "auto-derive
    from the other while preserving aspect ratio (rounded to even pixels)".
    """
    if options.target_width and options.target_height:
        return options.target_width, options.target_height
    if options.target_height:
        return -2, options.target_height
    if options.target_width:
        return options.target_width, -2
    # No target set — use scale * input height.
    s = max(1, options.scale)
    return -2, info_height * s


def _scale_filter(target_w: int, target_h: int, balanced: bool) -> str:
    """Build the ``scale[+denoise+sharpen+eq]`` filter expression."""
    parts: list[str] = []
    if balanced:
        # Mild denoise *before* the upscale so we don't amplify grain.
        parts.append("hqdn3d=1.5:1.5:6:6")
    parts.append(
        f"scale={target_w}:{target_h}:flags=lanczos+accurate_rnd+full_chroma_int"
    )
    if balanced:
        # Soft unsharp + a tiny contrast/sat lift — basically the
        # CapCut-Ultra-HD recipe minus the AI cost.
        parts.append("unsharp=5:5:0.7:5:5:0.3")
        parts.append("eq=contrast=1.04:saturation=1.08:gamma=0.98")
    return ",".join(parts)


def _upscale_ffmpeg(
    input_path: str,
    output_path: str,
    options: UpscaleOptions,
    progress_cb: Optional[ProgressCallback],
    cancel_flag: Optional[Callable[[], bool]],
    *,
    balanced: bool,
) -> str:
    if not ffmpeg_available():
        raise FFmpegError("ffmpeg not found on PATH.")

    info = probe_video(input_path)
    target_w, target_h = _resolve_target_size(options, info.height, info.width)
    vf = _scale_filter(target_w, target_h, balanced=balanced)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-progress", "pipe:1", "-nostats",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium",
        "-crf", "18" if balanced else "20",
        "-pix_fmt", "yuv420p",
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
# AI mode (Real-ESRGAN frame-by-frame)
# ---------------------------------------------------------------------------

def _upscale_ai(
    input_path: str,
    output_path: str,
    options: UpscaleOptions,
    progress_cb: Optional[ProgressCallback],
    cancel_flag: Optional[Callable[[], bool]],
    log_cb: Optional[Callable[[str], None]],
) -> str:
    if not ffmpeg_available():
        raise FFmpegError("ffmpeg is required for Real-ESRGAN frame assembly.")

    if log_cb:
        log_cb("[upscale] Ensuring Real-ESRGAN binary…")
    # Pull the binary if it isn't already cached. This is the only place we
    # block on a network download, and only on first use of AI mode.
    binary = ai_models.ensure_realesrgan(
        progress_cb=lambda p, msg: log_cb and log_cb(f"[upscale] {msg} ({p*100:.0f}%)")
    )

    info = probe_video(input_path)
    fps = info.fps or 30.0
    target_w, target_h = _resolve_target_size(options, info.height, info.width)

    with tempfile.TemporaryDirectory(prefix="vbp_esrgan_") as tmp:
        frames_in = os.path.join(tmp, "in")
        frames_out = os.path.join(tmp, "out")
        os.makedirs(frames_in, exist_ok=True)
        os.makedirs(frames_out, exist_ok=True)

        # 1. Extract frames as PNGs.
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
            progress_cb(0.10)
        esrgan_cmd = [
            str(binary),
            "-i", frames_in,
            "-o", frames_out,
            "-n", options.model,
            "-s", str(max(2, min(4, options.scale))),
            "-f", "png",
        ]
        if options.tile_size:
            esrgan_cmd += ["-t", str(options.tile_size)]
        rc = subprocess.call(esrgan_cmd)
        if rc != 0:
            raise FFmpegError("Real-ESRGAN upscaling failed.")
        if cancel_flag and cancel_flag():
            raise FFmpegError("Upscale cancelled by user.")

        # 3. Re-assemble video, scale to the requested final resolution
        #    (Real-ESRGAN's 2x/3x/4x rarely matches the user's exact
        #    target precisely), and mux original audio.
        if progress_cb:
            progress_cb(0.80)
        final_filter = (
            f"scale={target_w}:{target_h}:flags=lanczos+accurate_rnd+full_chroma_int"
        )
        assemble_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", f"{fps:.6f}",
            "-i", os.path.join(frames_out, "frame_%08d.png"),
            "-i", input_path,
            "-map", "0:v:0", "-map", "1:a?",
            "-vf", final_filter,
            "-c:v", "libx264", "-preset", "medium", "-crf", "17",
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
