"""FFmpeg wrapper: probing metadata, building watermark/overlay filter graphs, encoding."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional


class FFmpegError(RuntimeError):
    """Raised when an FFmpeg invocation fails or FFmpeg is unavailable."""


def _which(tool: str) -> Optional[str]:
    """Return the absolute path to `tool` on PATH, or None if missing."""
    return shutil.which(tool)


def ffmpeg_available() -> bool:
    return _which("ffmpeg") is not None


def ffprobe_available() -> bool:
    return _which("ffprobe") is not None


@dataclass
class VideoInfo:
    path: str
    duration: float  # seconds
    width: int
    height: int
    fps: float
    codec: str

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def duration_hms(self) -> str:
        total = int(self.duration)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


def probe_video(path: str) -> VideoInfo:
    """Probe a video file with ffprobe and return structured metadata.

    Raises FFmpegError if ffprobe is missing or the file is invalid.
    """
    if not ffprobe_available():
        raise FFmpegError("ffprobe not found on PATH. Install FFmpeg and retry.")
    if not os.path.isfile(path):
        raise FFmpegError(f"File not found: {path}")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=30
        )
    except subprocess.CalledProcessError as exc:
        raise FFmpegError(f"ffprobe failed for {path}: {exc.stderr.strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"ffprobe timed out for {path}") from exc

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"Could not parse ffprobe output for {path}") from exc

    video_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not video_streams:
        raise FFmpegError(f"No video stream found in {path}")

    v = video_streams[0]
    width = int(v.get("width", 0))
    height = int(v.get("height", 0))
    codec = v.get("codec_name", "unknown")

    fps = 0.0
    r_rate = v.get("r_frame_rate") or v.get("avg_frame_rate") or "0/1"
    if "/" in r_rate:
        num, den = r_rate.split("/", 1)
        try:
            n, d = float(num), float(den)
            fps = n / d if d else 0.0
        except ValueError:
            fps = 0.0

    duration = 0.0
    if "duration" in v:
        try:
            duration = float(v["duration"])
        except (TypeError, ValueError):
            duration = 0.0
    if not duration and "format" in data:
        try:
            duration = float(data["format"].get("duration", 0.0))
        except (TypeError, ValueError):
            duration = 0.0

    return VideoInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        codec=codec,
    )


# ---------------------------------------------------------------------------
# Filter graph construction
# ---------------------------------------------------------------------------

POSITION_EXPRS = {
    # (x_expr, y_expr) relative to (main W/H, overlay w/h) with 12px padding.
    "top-left":     ("12",            "12"),
    "top-right":    ("W-w-12",        "12"),
    "center":       ("(W-w)/2",       "(H-h)/2"),
    "bottom-left":  ("12",            "H-h-12"),
    "bottom-right": ("W-w-12",        "H-h-12"),
    "bottom":       ("(W-w)/2",       "H-h-12"),
    "top":          ("(W-w)/2",       "12"),
}


@dataclass
class WatermarkSettings:
    """User-controlled overlay settings. Either text or image_path must be set."""
    enabled: bool = False
    mode: str = "text"  # "text" or "image"
    text: str = ""
    image_path: str = ""
    position: str = "bottom-right"  # or "custom"
    custom_x: int = 0
    custom_y: int = 0
    opacity: float = 0.8            # 0.0 - 1.0
    scale: float = 1.0              # relative scale multiplier
    font_size: int = 36
    font_color: str = "white"
    font_file: str = ""             # path to a .ttf / .otf file (optional)
    shadow: bool = True             # drop shadow behind text
    bounce: bool = False            # DVD-screensaver style animation
    bounce_speed: str = "slow"      # "slow" | "medium" | "fast"
    bounce_start: str = "bottom-right"  # corner the bounce starts from
    # one of: top-left | top-right | bottom-left | bottom-right | center | random


def _escape_drawtext(text: str) -> str:
    """Escape a string for use inside an ffmpeg drawtext `text=` parameter."""
    # Order matters: escape backslashes first.
    text = text.replace("\\", "\\\\")
    text = text.replace(":", r"\:")
    text = text.replace("'", r"\'")
    text = text.replace("%", r"\%")
    return text


def _pos_exprs(settings: WatermarkSettings) -> tuple[str, str]:
    if settings.position == "custom":
        return (str(int(settings.custom_x)), str(int(settings.custom_y)))
    return POSITION_EXPRS.get(settings.position, POSITION_EXPRS["bottom-right"])


# DVD-screensaver pixels-per-second for each speed preset. We use slightly
# different X/Y velocities so the overlay doesn't move along a perfect 45°
# line — looks more natural.
BOUNCE_SPEEDS: dict[str, tuple[int, int]] = {
    "slow":   (40, 28),
    "medium": (90, 63),
    "fast":   (160, 112),
}


def _bounce_phase(start: str) -> tuple[str, str]:
    """Return (x_phase_expr, y_phase_expr) so the bounce animation begins at
    the requested corner.

    The triangle-wave `abs(mod(t*v + phase, 2*(span)) - span)` evaluates to:
      - phase=0          -> position = span (right/bottom edge)
      - phase=span       -> position = 0    (left/top edge)
      - phase=span/2     -> position = span/2 (centre, moving outward)

    `start="random"` returns deterministic-but-unique phases per render so
    the watermark spawns at a different location each time without the
    same-corner-every-clip syndrome.
    """
    s = (start or "bottom-right").lower()
    # X span uses the natural ffmpeg variables `w` (text bounce) or `W-w`
    # (overlay bounce); we let the caller substitute the correct names. Here
    # we build *expression strings* that reference `XSPAN` / `YSPAN` which
    # the caller replaces with the real expressions.
    presets = {
        "top-left":     ("XSPAN",          "YSPAN"),
        "top-right":    ("0",              "YSPAN"),
        "bottom-left":  ("XSPAN",          "0"),
        "bottom-right": ("0",              "0"),
        "center":       ("(XSPAN)/2",      "(YSPAN)/2"),
        "random":       (
            # `random(0)*XSPAN` is evaluated once per frame which would jitter,
            # so we use a deterministic-per-process offset baked into the
            # filter graph at build time.
            f"{_RANDOM_X_PHASE:.4f}*(XSPAN)",
            f"{_RANDOM_Y_PHASE:.4f}*(YSPAN)",
        ),
    }
    return presets.get(s, presets["bottom-right"])


import random as _random
_RANDOM_X_PHASE = _random.random()
_RANDOM_Y_PHASE = _random.random()


def _bounce_exprs_text(speed: str, start: str) -> tuple[str, str]:
    """Return ffmpeg drawtext x/y expressions for a DVD-style bounce.

    Uses a triangle wave: `abs(mod(t*v + phase, 2*(w-text_w)) - (w-text_w))`
    oscillates between 0 and `(w-text_w)`. `text_w` / `text_h` are
    drawtext-specific variables that resolve to the rendered glyph box.
    The `phase` term shifts the starting position so the user can pick which
    corner the watermark spawns from.
    """
    vx, vy = BOUNCE_SPEEDS.get(speed, BOUNCE_SPEEDS["slow"])
    px, py = _bounce_phase(start)
    # Substitute the abstract span tokens with drawtext's actual variables.
    px = px.replace("XSPAN", "(w-text_w)").replace("YSPAN", "(h-text_h)")
    py = py.replace("XSPAN", "(w-text_w)").replace("YSPAN", "(h-text_h)")
    x = f"abs(mod(t*{vx}+{px}\\,2*(w-text_w))-(w-text_w))"
    y = f"abs(mod(t*{vy}+{py}\\,2*(h-text_h))-(h-text_h))"
    return x, y


def _bounce_exprs_overlay(speed: str, start: str) -> tuple[str, str]:
    """Return ffmpeg overlay x/y expressions for a DVD-style image bounce.

    For the overlay filter, main video dimensions are `W`/`H` and the overlay
    is `w`/`h`. Note: unlike drawtext, overlay expressions use `:` as the arg
    separator so commas inside `mod()` MUST be escaped with `\\,`.
    """
    vx, vy = BOUNCE_SPEEDS.get(speed, BOUNCE_SPEEDS["slow"])
    px, py = _bounce_phase(start)
    px = px.replace("XSPAN", "(W-w)").replace("YSPAN", "(H-h)")
    py = py.replace("XSPAN", "(W-w)").replace("YSPAN", "(H-h)")
    x = f"abs(mod(t*{vx}+{px}\\,2*(W-w))-(W-w))"
    y = f"abs(mod(t*{vy}+{py}\\,2*(H-h))-(H-h))"
    return x, y


def _escape_fontfile(path: str) -> str:
    """Escape a font file path for FFmpeg's drawtext `fontfile=` parameter."""
    path = path.replace("\\", "/")
    path = path.replace(":", r"\:")
    path = path.replace("'", r"\'")
    return path


def build_overlay_filtergraph(
    settings: WatermarkSettings,
    base_width: int,
    base_height: int,
) -> tuple[list[str], list[str]]:
    """Return (extra_input_args, filter_complex_parts) for an overlay.

    The caller is responsible for prepending the primary `-i input.mp4` and
    joining `filter_complex_parts` with `;`. Image inputs are loaded with
    `-loop 1` so a still image stays stable across the whole duration of the
    main video (this is what fixes the "bouncing" issue).
    """
    if not settings.enabled:
        return [], []

    x_expr, y_expr = _pos_exprs(settings)

    if settings.mode == "image":
        if not settings.image_path or not os.path.isfile(settings.image_path):
            raise FFmpegError(f"Watermark image not found: {settings.image_path!r}")
        if settings.bounce:
            x_expr, y_expr = _bounce_exprs_overlay(
                settings.bounce_speed, settings.bounce_start
            )
        # Scale overlay relative to main width (10% default * scale multiplier).
        target_w = max(1, int(base_width * 0.1 * settings.scale))
        # -loop 1 keeps the still image alive for the whole video duration;
        # without it, FFmpeg would emit a single frame and the overlay would
        # flash/disappear (the "bouncing" the user reported).
        extra_inputs = ["-loop", "1", "-i", settings.image_path]
        fc = [
            # Normalize SAR so overlay doesn't stretch or jitter on mixed aspect
            # sources, and force rgba so alpha blending is consistent.
            f"[1:v]scale={target_w}:-1:flags=lanczos,setsar=1,format=rgba,"
            f"colorchannelmixer=aa={settings.opacity:.3f}[wm]",
            f"[0:v][wm]overlay={x_expr}:{y_expr}:shortest=1[vout]",
        ]
        return extra_inputs, fc

    # text mode
    text = _escape_drawtext(settings.text or "")
    if not text:
        return [], []
    font_size = max(8, int(settings.font_size * settings.scale))
    alpha = max(0.0, min(1.0, settings.opacity))
    font_clause = ""
    if settings.font_file and os.path.isfile(settings.font_file):
        font_clause = f"fontfile='{_escape_fontfile(settings.font_file)}':"

    if settings.bounce:
        x_expr, y_expr = _bounce_exprs_text(
            settings.bounce_speed, settings.bounce_start
        )

    # Build drawtext parameters. We pair a semi-transparent box with a
    # drop-shadow for a professional CapCut-style overlay.
    parts = [
        f"drawtext={font_clause}text='{text}'",
        f"fontcolor={settings.font_color}@{alpha:.3f}",
        f"fontsize={font_size}",
        f"x={x_expr}",
        f"y={y_expr}",
        f"box=1",
        f"boxcolor=black@{max(0.0, alpha - 0.3):.3f}",
        f"boxborderw=8",
    ]
    if settings.shadow:
        parts.extend([
            f"shadowcolor=black@{max(0.3, alpha * 0.8):.3f}",
            "shadowx=2",
            "shadowy=2",
        ])
    drawtext = ":".join(parts)
    return [], [f"[0:v]{drawtext}[vout]"]


# ---------------------------------------------------------------------------
# Encode with optional overlay + optional scale + optional sharpen
# ---------------------------------------------------------------------------

@dataclass
class EncodeOptions:
    watermark: WatermarkSettings
    target_height: Optional[int] = None   # e.g. 2160 for 4K; None = keep
    target_width: Optional[int] = None    # e.g. 1080 for Portrait HD; None = derive
    video_codec: str = "libx264"
    crf: int = 20
    preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    sharpen: bool = False                 # apply `unsharp` filter for a crisp
                                          # CapCut-style ultra-HD look
    sharpen_amount: float = 0.8           # 0 (none) - 1.5 (very sharp)
    pixel_format: str = "yuv420p"         # broadly compatible output pix fmt
    denoise: bool = False                 # apply hqdn3d before scale to clean
                                          # noisy / low-bitrate sources
    denoise_strength: str = "soft"        # "soft" | "medium" | "strong"
    color_boost: bool = False             # mild contrast / saturation boost
                                          # for a punchy CapCut-style look
    scale_flags: str = "lanczos+accurate_rnd+full_chroma_int"
    profile_high: bool = True             # H.264 High profile (better quality
                                          # at the same bitrate)
    # When set, the scale stage uses ``crop`` to centre-fit the frame to the
    # target aspect ratio before resizing — required for Portrait HD on
    # landscape input so we don't end up with letterboxes.
    crop_to_aspect: bool = False


ProgressCallback = Callable[[float], None]
# progress: 0.0 - 1.0


_TIME_RE = re.compile(r"out_time_ms=(\d+)")


_DENOISE_STRENGTHS: dict[str, tuple[float, float, float, float]] = {
    # hqdn3d=luma_spatial:chroma_spatial:luma_tmp:chroma_tmp
    "soft":   (1.5, 1.0, 4.0, 3.0),
    "medium": (3.0, 2.0, 6.0, 4.5),
    "strong": (5.0, 3.5, 7.5, 5.5),
}


def _chain_post_filters(
    filter_parts: list[str],
    options: EncodeOptions,
    info: VideoInfo,
) -> list[str]:
    """Append optional denoise + scale + sharpen + color filters after the
    overlay step.

    Filter order matters for visible quality:
      1. denoise BEFORE scale (cleaner upscale, no amplified grain)
      2. scale  (lanczos+accurate_rnd+full_chroma_int — better than naive lanczos)
      3. unsharp (sharpens AFTER upscale to compensate for any softening)
      4. eq      (final contrast/saturation pop)
    """
    target_w = options.target_width or 0
    target_h = options.target_height or 0
    do_scale = (
        (target_w and target_w > 0 and target_w != info.width)
        or (target_h and target_h > 0 and target_h != info.height)
    )
    do_sharpen = options.sharpen and options.sharpen_amount > 0
    do_denoise = options.denoise
    do_color = options.color_boost

    if not (do_scale or do_sharpen or do_denoise or do_color):
        return filter_parts

    last = filter_parts[-1]
    last_no_sink = last.rsplit("[vout]", 1)[0]
    chain = [f"{last_no_sink}[pre_post]"]

    stages: list[str] = []
    if do_denoise:
        ls, cs, lt, ct = _DENOISE_STRENGTHS.get(
            options.denoise_strength, _DENOISE_STRENGTHS["soft"]
        )
        stages.append(f"hqdn3d={ls:.2f}:{cs:.2f}:{lt:.2f}:{ct:.2f}")
    if do_scale:
        if options.crop_to_aspect and target_w and target_h:
            # Centre-crop to the target aspect ratio first, then scale.
            # This is what makes Portrait HD work on landscape input
            # without pillarboxing.
            stages.append(
                f"crop='min(iw,ih*{target_w}/{target_h})':"
                f"'min(ih,iw*{target_h}/{target_w})'"
            )
        if target_w and target_h:
            stages.append(
                f"scale={int(target_w)}:{int(target_h)}:flags={options.scale_flags}"
            )
        elif target_h:
            stages.append(
                f"scale=-2:{int(target_h)}:flags={options.scale_flags}"
            )
        else:
            stages.append(
                f"scale={int(target_w)}:-2:flags={options.scale_flags}"
            )
    if do_sharpen:
        # unsharp: luma_msize_x:luma_msize_y:luma_amount:chroma_msize_x:chroma_msize_y:chroma_amount
        # Boost chroma sharpening at higher amounts for that crisp CapCut look.
        a = max(0.0, min(1.5, float(options.sharpen_amount)))
        chroma_a = max(0.0, min(0.6, a * 0.45))
        stages.append(f"unsharp=5:5:{a:.2f}:5:5:{chroma_a:.2f}")
    if do_color:
        # Subtle: contrast +5%, saturation +12%, gamma 0.97 (brighter midtones).
        stages.append("eq=contrast=1.05:saturation=1.12:gamma=0.97")

    chain.append(f"[pre_post]{','.join(stages)}[vout]")
    return filter_parts[:-1] + [";".join(chain)]


def process_video(
    input_path: str,
    output_path: str,
    info: VideoInfo,
    options: EncodeOptions,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
) -> None:
    """Run a full encode with overlay + optional upscale + optional sharpen.

    `progress_cb` is invoked with a 0.0-1.0 float as FFmpeg emits progress.
    `cancel_flag` is a callable that returns True when processing should abort.
    """
    if not ffmpeg_available():
        raise FFmpegError("ffmpeg not found on PATH. Install FFmpeg and retry.")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    extra_inputs, filter_parts = build_overlay_filtergraph(
        options.watermark, info.width, info.height
    )

    # If no overlay, we still need a label for scaling.
    if not filter_parts:
        filter_parts = ["[0:v]null[vout]"]

    filter_parts = _chain_post_filters(filter_parts, options, info)
    filter_complex = ";".join(filter_parts)

    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-progress", "pipe:1",
        "-nostats",
        "-i", input_path,
    ]
    cmd.extend(extra_inputs)
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", options.video_codec,
        "-preset", options.preset,
        "-crf", str(options.crf),
        "-pix_fmt", options.pixel_format,
    ])
    if options.profile_high and options.video_codec == "libx264":
        cmd.extend(["-profile:v", "high", "-level", "4.2"])
    cmd.extend([
        "-c:a", options.audio_codec,
        "-b:a", options.audio_bitrate,
        "-movflags", "+faststart",
        output_path,
    ])

    total_duration_s = max(info.duration, 0.001)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel_flag and cancel_flag():
                proc.terminate()
                raise FFmpegError("Processing cancelled by user.")
            line = line.strip()
            m = _TIME_RE.search(line)
            if m and progress_cb:
                out_ms = int(m.group(1))
                elapsed_s = out_ms / 1_000_000.0
                pct = min(1.0, elapsed_s / total_duration_s)
                progress_cb(pct)
            if line == "progress=end" and progress_cb:
                progress_cb(1.0)
    finally:
        proc.stdout.close() if proc.stdout else None
        rc = proc.wait()
        stderr = proc.stderr.read() if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()
        if rc != 0:
            raise FFmpegError(
                f"FFmpeg exited with code {rc} for {input_path}:\n{stderr.strip()}"
            )


def ffmpeg_version() -> str:
    """Return the first line of `ffmpeg -version`, or '' if unavailable."""
    if not ffmpeg_available():
        return ""
    try:
        out = subprocess.check_output(["ffmpeg", "-version"], text=True, timeout=5)
        return out.splitlines()[0] if out else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def build_command_preview(
    input_path: str,
    output_path: str,
    info: VideoInfo,
    options: EncodeOptions,
) -> list[str]:
    """Return the ffmpeg command that would be executed (for tests / UI preview)."""
    extra_inputs, filter_parts = build_overlay_filtergraph(
        options.watermark, info.width, info.height
    )
    if not filter_parts:
        filter_parts = ["[0:v]null[vout]"]
    filter_parts = _chain_post_filters(filter_parts, options, info)
    filter_complex = ";".join(filter_parts)

    cmd: list[str] = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_path,
    ]
    cmd.extend(extra_inputs)
    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "0:a?",
        "-c:v", options.video_codec,
        "-preset", options.preset,
        "-crf", str(options.crf),
        "-pix_fmt", options.pixel_format,
    ])
    if options.profile_high and options.video_codec == "libx264":
        cmd.extend(["-profile:v", "high", "-level", "4.2"])
    cmd.extend([
        "-c:a", options.audio_codec,
        "-b:a", options.audio_bitrate,
        "-movflags", "+faststart",
        output_path,
    ])
    return cmd


# ---------------------------------------------------------------------------
# Built-in quality presets (Digitalinos templates)
# ---------------------------------------------------------------------------

@dataclass
class QualityTemplate:
    """Preset-style knobs for the encoder. Applied on top of user watermark."""
    name: str
    target_height: Optional[int]   # None = keep source height
    preset: str                    # x264 preset
    crf: int                       # lower = better quality
    sharpen: bool
    sharpen_amount: float
    denoise: bool = False
    denoise_strength: str = "soft"
    color_boost: bool = False
    audio_bitrate: str = "192k"


QUALITY_TEMPLATES: dict[str, QualityTemplate] = {
    "Original (no changes)": QualityTemplate(
        name="Original (no changes)",
        target_height=None, preset="medium", crf=20,
        sharpen=False, sharpen_amount=0.0,
    ),
    "YouTube 1080p Clean": QualityTemplate(
        name="YouTube 1080p Clean",
        target_height=1080, preset="slow", crf=18,
        sharpen=True, sharpen_amount=0.9,
        denoise=True, denoise_strength="soft",
        color_boost=True,
    ),
    "CapCut Ultra HD (1440p)": QualityTemplate(
        name="CapCut Ultra HD (1440p)",
        target_height=1440, preset="slow", crf=17,
        sharpen=True, sharpen_amount=1.2,
        denoise=True, denoise_strength="medium",
        color_boost=True,
    ),
    "4K Crisp (2160p)": QualityTemplate(
        name="4K Crisp (2160p)",
        target_height=2160, preset="slow", crf=16,
        sharpen=True, sharpen_amount=1.4,
        denoise=True, denoise_strength="medium",
        color_boost=True,
    ),
    "Fast Preview": QualityTemplate(
        name="Fast Preview",
        target_height=720, preset="ultrafast", crf=26,
        sharpen=False, sharpen_amount=0.0,
        audio_bitrate="128k",
    ),
}


def apply_quality_template(options: EncodeOptions, template_name: str) -> EncodeOptions:
    """Return a new EncodeOptions with the named template applied on top.

    Unknown template names return `options` unchanged.
    """
    tpl = QUALITY_TEMPLATES.get(template_name)
    if not tpl:
        return options
    return EncodeOptions(
        watermark=options.watermark,
        target_height=tpl.target_height if tpl.target_height is not None else options.target_height,
        target_width=options.target_width,
        video_codec=options.video_codec,
        crf=tpl.crf,
        preset=tpl.preset,
        audio_codec=options.audio_codec,
        audio_bitrate=tpl.audio_bitrate,
        sharpen=tpl.sharpen,
        sharpen_amount=tpl.sharpen_amount,
        pixel_format=options.pixel_format,
        denoise=tpl.denoise,
        denoise_strength=tpl.denoise_strength,
        color_boost=tpl.color_boost,
        scale_flags=options.scale_flags,
        profile_high=options.profile_high,
        crop_to_aspect=options.crop_to_aspect,
    )


# ---------------------------------------------------------------------------
# Resolution presets — labelled targets exposed in the UI.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolutionPreset:
    label: str
    width: int
    height: int
    portrait: bool = False  # True = centre-crop landscape input to 9:16


RESOLUTION_PRESETS: dict[str, ResolutionPreset] = {
    "HD (1280×720)":          ResolutionPreset("HD",          1280,  720),
    "Ultra HD (1920×1080)":   ResolutionPreset("Ultra HD",    1920, 1080),
    "Portrait HD (1080×1920)":ResolutionPreset("Portrait HD", 1080, 1920, portrait=True),
    "2K (2560×1440)":         ResolutionPreset("2K",          2560, 1440),
    "4K (3840×2160)":         ResolutionPreset("4K",          3840, 2160),
}


DEFAULT_RESOLUTION_KEY = "Ultra HD (1920×1080)"
