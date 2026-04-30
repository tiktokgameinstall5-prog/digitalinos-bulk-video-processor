"""Auto-captions: speech-to-text with Whisper, then burn into video.

Two stages, kept independent so each can be tested + reused:

1. ``transcribe_to_srt(audio_path, model_size, …)``
   Uses ``faster-whisper`` (CTranslate2) to produce a list of timed
   segments and writes a standard ``.srt`` file. CPU-only by default;
   the user picks ``tiny`` / ``base`` / ``small`` per job.

2. ``burn_subtitles(input_video, srt_path, output_video, style)``
   Re-encodes the input through FFmpeg's ``subtitles=`` filter using
   libass — colour, font, size, outline are forwarded as a
   ``force_style=…`` string so the user's choice in the UI is honoured
   without us having to hand-roll an ASS file.

Public entry point: ``add_auto_captions(input_video, output_video,
options, …)`` — does both stages back-to-back and cleans up its temp
files. The original audio stream is preserved untouched (we re-encode
video only).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import ai_models
from .ffmpeg_handler import FFmpegError, ffmpeg_available, probe_video


WHISPER_SIZES = ("tiny", "base", "small")


# ---------------------------------------------------------------------------
# Caption style settings
# ---------------------------------------------------------------------------

DEFAULT_STYLE_PRESETS: dict[str, dict[str, object]] = {
    # libass / ASS field names — forwarded verbatim into ``force_style``.
    "Clean (white)": {
        "FontName": "DejaVu Sans",
        "FontSize": 28,
        "PrimaryColour": "&H00FFFFFF",   # white
        "OutlineColour": "&H00000000",   # black
        "BorderStyle": 1,                # outline + drop shadow
        "Outline": 2,
        "Shadow": 1,
        "Bold": 0,
        "Alignment": 2,                  # bottom-centre
        "MarginV": 60,
    },
    "Bold (yellow)": {
        "FontName": "DejaVu Sans",
        "FontSize": 32,
        "PrimaryColour": "&H0000FFFF",   # yellow (BGR in ASS)
        "OutlineColour": "&H00000000",
        "BorderStyle": 1,
        "Outline": 3,
        "Shadow": 1,
        "Bold": 1,
        "Alignment": 2,
        "MarginV": 70,
    },
    "TikTok (white box)": {
        "FontName": "DejaVu Sans",
        "FontSize": 30,
        "PrimaryColour": "&H00FFFFFF",
        "OutlineColour": "&H00000000",
        "BackColour": "&H80000000",      # 50% black background
        "BorderStyle": 4,                # opaque box
        "Outline": 0,
        "Shadow": 0,
        "Bold": 1,
        "Alignment": 2,
        "MarginV": 80,
    },
}


@dataclass
class CaptionStyle:
    """User-selected caption styling.

    For the *default* mode the UI just picks one of ``DEFAULT_STYLE_PRESETS``
    and leaves the custom overrides empty. For the *custom* mode we forward
    the user's font name / size / colour / stroke straight into the libass
    ``force_style`` string.
    """

    mode: str = "default"                # "default" or "custom"
    preset: str = "Clean (white)"        # one of DEFAULT_STYLE_PRESETS

    # Custom mode overrides (only used when mode == "custom").
    font_name: str = "DejaVu Sans"
    font_size: int = 28
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: int = 2               # "stroke" in the user's terms
    bold: bool = False
    alignment: int = 2                   # 1=BL, 2=BC, 3=BR, 5/6/7=TL/TC/TR (libass)
    margin_v: int = 60

    def to_force_style(self) -> str:
        """Build the libass ``force_style=…`` payload."""
        if self.mode != "custom":
            base = DEFAULT_STYLE_PRESETS.get(
                self.preset, DEFAULT_STYLE_PRESETS["Clean (white)"]
            )
            return _format_style_dict(base)

        return _format_style_dict({
            "FontName": self.font_name or "DejaVu Sans",
            "FontSize": int(self.font_size or 28),
            "PrimaryColour": _hex_to_ass_color(self.primary_color),
            "OutlineColour": _hex_to_ass_color(self.outline_color),
            "BorderStyle": 1,
            "Outline": int(max(0, self.outline_width)),
            "Shadow": 1,
            "Bold": -1 if self.bold else 0,
            "Alignment": int(self.alignment or 2),
            "MarginV": int(self.margin_v or 60),
        })


@dataclass
class CaptionOptions:
    """Top-level caption job settings."""

    enabled: bool = False
    model_size: str = "tiny"             # "tiny" | "base" | "small"
    language: str = ""                   # "" = auto-detect
    style: CaptionStyle = field(default_factory=CaptionStyle)
    # The .srt is written next to the output video by default. If the user
    # wants a different location they can set this.
    srt_output_path: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[float, str], None]


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401  (cheap import-only probe)
    except ImportError:
        return False
    return True


def add_auto_captions(
    input_video: str,
    output_video: str,
    options: CaptionOptions,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> str:
    """Transcribe ``input_video`` and burn the captions into ``output_video``.

    Returns the path to the written SRT (useful for debugging / re-burns).
    Raises :class:`FFmpegError` if FFmpeg is unavailable or any step fails.
    """
    if not options.enabled:
        if os.path.abspath(input_video) != os.path.abspath(output_video):
            shutil.copyfile(input_video, output_video)
        return ""

    if not ffmpeg_available():
        raise FFmpegError("ffmpeg not found on PATH.")
    if not whisper_available():
        raise FFmpegError(
            "faster-whisper is not installed. Run "
            "`pip install faster-whisper` then retry."
        )

    if log_cb:
        log_cb(f"[captions] Loading Whisper model '{options.model_size}'…")
    if progress_cb:
        progress_cb(0.0, "Loading Whisper model…")

    srt_path = options.srt_output_path or _default_srt_path(output_video)

    transcribe_to_srt(
        input_video,
        srt_path,
        model_size=options.model_size,
        language=options.language,
        progress_cb=progress_cb,
        cancel_flag=cancel_flag,
        log_cb=log_cb,
    )
    if cancel_flag and cancel_flag():
        raise FFmpegError("Captions cancelled by user.")

    if progress_cb:
        progress_cb(0.85, "Burning captions into video…")

    burn_subtitles(
        input_video,
        srt_path,
        output_video,
        options.style,
        cancel_flag=cancel_flag,
    )

    if progress_cb:
        progress_cb(1.0, "Captions done.")
    if log_cb:
        log_cb(f"[captions] Subtitle file: {srt_path}")
    return srt_path


# ---------------------------------------------------------------------------
# Stage 1: transcription
# ---------------------------------------------------------------------------

def transcribe_to_srt(
    media_path: str,
    srt_path: str,
    *,
    model_size: str = "tiny",
    language: str = "",
    progress_cb: Optional[ProgressCallback] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> str:
    """Run faster-whisper on ``media_path`` and write SRT to ``srt_path``."""
    from faster_whisper import WhisperModel  # type: ignore[import-not-found]

    model_dir = ai_models.ensure_whisper_model(
        model_size,
        progress_cb=lambda p, msg: progress_cb and progress_cb(p * 0.15, msg),
    )

    if log_cb:
        log_cb(f"[captions] Transcribing with model {model_size!r}…")

    model = WhisperModel(
        str(model_dir),
        device="cpu",
        compute_type="int8",
    )

    info = probe_video(media_path)
    total = max(info.duration, 0.001)

    transcribe_kwargs: dict[str, object] = {
        "vad_filter": True,
        "beam_size": 1,
    }
    if language:
        transcribe_kwargs["language"] = language

    segments_iter, _info = model.transcribe(media_path, **transcribe_kwargs)

    written: list[tuple[int, float, float, str]] = []
    for i, seg in enumerate(segments_iter, start=1):
        if cancel_flag and cancel_flag():
            raise FFmpegError("Captions cancelled by user.")
        text = (seg.text or "").strip()
        if not text:
            continue
        written.append((i, float(seg.start), float(seg.end), text))
        if progress_cb:
            # Whisper emits segments roughly sequentially in time, so the
            # end timestamp is a decent proxy for overall progress.
            progress_cb(0.15 + min(0.65, (seg.end / total) * 0.65), "Transcribing…")

    _write_srt(srt_path, written)
    if log_cb:
        log_cb(f"[captions] Wrote {len(written)} segments to {srt_path}")
    return srt_path


# ---------------------------------------------------------------------------
# Stage 2: burn
# ---------------------------------------------------------------------------

def burn_subtitles(
    input_video: str,
    srt_path: str,
    output_video: str,
    style: CaptionStyle,
    cancel_flag: Optional[Callable[[], bool]] = None,
) -> str:
    """Re-encode ``input_video`` with the SRT burned in via libass."""
    if not ffmpeg_available():
        raise FFmpegError("ffmpeg not found on PATH.")

    force_style = style.to_force_style()
    # FFmpeg's filtergraph parser uses ``,`` and ``:`` as separators, so any
    # such chars in the path or style payload need to be escaped.
    srt_arg = _ffmpeg_subtitle_arg(srt_path)
    vf = f"subtitles={srt_arg}:force_style='{force_style}'"

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_video,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_video,
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(
            f"FFmpeg subtitle burn failed (rc={proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return output_video


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_srt_path(output_video: str) -> str:
    base = os.path.splitext(output_video)[0]
    return base + ".srt"


def _format_style_dict(style: dict[str, object]) -> str:
    return ",".join(f"{k}={v}" for k, v in style.items())


_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _hex_to_ass_color(hex_color: str) -> str:
    """Convert ``#RRGGBB`` to libass's ``&H00BBGGRR`` (alpha first, BGR)."""
    m = _HEX_RE.match((hex_color or "").strip())
    if not m:
        return "&H00FFFFFF"
    rr = m.group(1)[0:2]
    gg = m.group(1)[2:4]
    bb = m.group(1)[4:6]
    return f"&H00{bb.upper()}{gg.upper()}{rr.upper()}"


def _format_srt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(path: str, segments: Sequence[tuple[int, float, float, str]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for idx, (n, start, end, text) in enumerate(segments, start=1):
            fh.write(f"{idx}\n")
            fh.write(f"{_format_srt_time(start)} --> {_format_srt_time(end)}\n")
            fh.write(text.strip() + "\n\n")


def _ffmpeg_subtitle_arg(path: str) -> str:
    """Escape a path for use inside FFmpeg's ``subtitles=`` filter argument.

    Backslash, colon, single quote, and comma all have special meanings
    inside the filter graph parser. We additionally normalise Windows
    paths so libass can find the file (``C:`` is interpreted as a filter
    option separator unless escaped).
    """
    p = path.replace("\\", "/")
    p = p.replace(":", r"\:")
    p = p.replace("'", r"\'")
    p = p.replace(",", r"\,")
    return p
