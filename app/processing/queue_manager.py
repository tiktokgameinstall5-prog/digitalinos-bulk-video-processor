"""Background worker thread that processes a queue of videos.

Each item runs through up to three stages — Real-ESRGAN / FFmpeg upscale
(optional), the main encode with watermark + scale + sharpen, and an
auto-caption burn-in (optional). Stages chain via a temporary file so a
failure in one only invalidates that item, not the whole batch.
"""

from __future__ import annotations

import os
import tempfile
import traceback
from dataclasses import dataclass, field
from typing import Optional

from PyQt5 import QtCore

from .captions_handler import CaptionOptions, add_auto_captions
from .ffmpeg_handler import (
    EncodeOptions,
    FFmpegError,
    VideoInfo,
    process_video,
    probe_video,
)
from .upscale_handler import UpscaleOptions, upscale_video
from ..utils.file_manager import (
    build_output_path,
    build_sequential_output_paths,
)


@dataclass
class QueueItem:
    input_path: str
    info: Optional[VideoInfo] = None
    output_path: str = ""
    status: str = "pending"   # "pending" | "running" | "done" | "error" | "cancelled"
    error: str = ""


@dataclass
class BatchJob:
    items: list[QueueItem] = field(default_factory=list)
    output_dir: str = ""
    encode_options: EncodeOptions = None  # type: ignore[assignment]
    upscale_options: UpscaleOptions = field(default_factory=UpscaleOptions)
    caption_options: CaptionOptions = field(default_factory=CaptionOptions)
    sequential_naming: bool = False  # when True: 1_<rand>.mp4, 2_<rand>.mp4, ...


class BatchWorker(QtCore.QObject):
    """Processes a batch of videos in a background thread."""

    # Signals -------------------------------------------------------------
    item_started = QtCore.pyqtSignal(int)                      # index
    item_progress = QtCore.pyqtSignal(int, float)              # index, 0..1
    item_finished = QtCore.pyqtSignal(int, str)                # index, output_path
    item_failed = QtCore.pyqtSignal(int, str)                  # index, error message
    overall_progress = QtCore.pyqtSignal(float)                # 0..1
    log_message = QtCore.pyqtSignal(str)                       # informational
    batch_finished = QtCore.pyqtSignal(list)                   # list of output paths

    def __init__(self, job: BatchJob, parent: Optional[QtCore.QObject] = None) -> None:
        super().__init__(parent)
        self._job = job
        self._cancel = False

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        self._cancel = True

    def _cancelled(self) -> bool:
        return self._cancel

    # ------------------------------------------------------------------
    def _plan_output_paths(self) -> list[str]:
        """Decide output paths up front so the UI can show names during processing."""
        if self._job.sequential_naming:
            return build_sequential_output_paths(
                [it.input_path for it in self._job.items],
                self._job.output_dir,
            )
        return [
            it.output_path or build_output_path(it.input_path, self._job.output_dir)
            for it in self._job.items
        ]

    # ------------------------------------------------------------------
    @QtCore.pyqtSlot()
    def run(self) -> None:
        total = len(self._job.items)
        if total == 0:
            self.batch_finished.emit([])
            return

        planned_outputs = self._plan_output_paths()

        outputs: list[str] = []
        for idx, item in enumerate(self._job.items):
            if self._cancel:
                item.status = "cancelled"
                continue

            item.status = "running"
            item.output_path = planned_outputs[idx]
            self.item_started.emit(idx)

            try:
                self._process_one(idx, item, total)
                item.status = "done"
                outputs.append(item.output_path)
                self.item_finished.emit(idx, item.output_path)
                self.overall_progress.emit((idx + 1) / total)

            except FFmpegError as exc:
                item.status = "error"
                item.error = str(exc)
                self.item_failed.emit(idx, str(exc))
                self.log_message.emit(f"[ERROR] {item.input_path}: {exc}")
            except Exception as exc:  # noqa: BLE001
                item.status = "error"
                item.error = f"{exc}\n{traceback.format_exc()}"
                self.item_failed.emit(idx, str(exc))
                self.log_message.emit(f"[ERROR] {item.input_path}: {exc}")

        self.batch_finished.emit(outputs)

    # ------------------------------------------------------------------
    def _process_one(self, idx: int, item: QueueItem, total: int) -> None:
        """Run upscale → encode → captions for a single queue item."""
        info = item.info or probe_video(item.input_path)
        item.info = info

        # Stage weights (sum to 1.0). When a stage is disabled its weight
        # collapses into the next one so the bar still fills smoothly.
        do_up = self._job.upscale_options.enabled
        do_cap = self._job.caption_options.enabled
        w_up = 0.40 if do_up else 0.0
        w_cap = 0.30 if do_cap else 0.0
        w_enc = max(0.10, 1.0 - w_up - w_cap)

        offset = [0.0]

        def _emit(stage_progress: float, stage_weight: float) -> None:
            pct = min(1.0, max(0.0, offset[0] + stage_progress * stage_weight))
            self.item_progress.emit(idx, pct)
            self.overall_progress.emit((idx + pct) / total)

        with tempfile.TemporaryDirectory(prefix="vbp_pipe_") as tmp:
            tmp_dir = tmp
            current_input = item.input_path

            # ----- 1. Upscale ---------------------------------------------
            if do_up:
                up_out = os.path.join(tmp_dir, "upscaled.mp4")
                self.log_message.emit(
                    f"[upscale] mode={self._job.upscale_options.resolved_mode()}"
                )
                upscale_video(
                    input_path=current_input,
                    output_path=up_out,
                    options=self._job.upscale_options,
                    progress_cb=lambda p, w=w_up: _emit(p, w),
                    cancel_flag=self._cancelled,
                    log_cb=self.log_message.emit,
                )
                current_input = up_out
                offset[0] += w_up

            # ----- 2. Main encode (watermark + scale + sharpen + denoise) -
            enc_out = (
                item.output_path
                if not do_cap
                else os.path.join(tmp_dir, "encoded.mp4")
            )
            enc_info = probe_video(current_input) if do_up else info
            options = self._build_encode_options(enc_info)

            process_video(
                input_path=current_input,
                output_path=enc_out,
                info=enc_info,
                options=options,
                progress_cb=lambda p, w=w_enc: _emit(p, w),
                cancel_flag=self._cancelled,
            )
            current_input = enc_out
            offset[0] += w_enc

            # ----- 3. Captions --------------------------------------------
            if do_cap:
                # SRT goes alongside the final output so users can edit /
                # re-burn later without re-transcribing.
                srt_out = os.path.splitext(item.output_path)[0] + ".srt"
                cap_opts = self._job.caption_options
                cap_opts_with_srt = type(cap_opts)(
                    enabled=cap_opts.enabled,
                    model_size=cap_opts.model_size,
                    language=cap_opts.language,
                    style=cap_opts.style,
                    srt_output_path=srt_out,
                )
                add_auto_captions(
                    input_video=current_input,
                    output_video=item.output_path,
                    options=cap_opts_with_srt,
                    progress_cb=lambda p, msg, w=w_cap: _emit(p, w),
                    cancel_flag=self._cancelled,
                    log_cb=self.log_message.emit,
                )
                offset[0] += w_cap

        # Final progress signal — guarantees the bar reaches 100%.
        self.item_progress.emit(idx, 1.0)

    # ------------------------------------------------------------------
    def _build_encode_options(self, info: VideoInfo) -> EncodeOptions:
        """Merge the upscale target into the encode options so the main
        pass also handles fine-tuning the resolution.

        When the upscale stage already produced a frame at the right
        resolution we still pass the target_height through so subsequent
        watermark-overlay rendering knows the canvas size; ``do_scale``
        in ``_chain_post_filters`` is a no-op when source==target.
        """
        base = self._job.encode_options
        target_h = (
            self._job.upscale_options.target_height
            if self._job.upscale_options.enabled
            else base.target_height
        )
        target_w = (
            self._job.upscale_options.target_width
            if self._job.upscale_options.enabled
            else base.target_width
        )
        crop_to_aspect = base.crop_to_aspect or (
            self._job.upscale_options.enabled
            and bool(self._job.upscale_options.target_width)
            and bool(self._job.upscale_options.target_height)
        )
        return EncodeOptions(
            watermark=base.watermark,
            target_height=target_h,
            target_width=target_w,
            video_codec=base.video_codec,
            crf=base.crf,
            preset=base.preset,
            audio_codec=base.audio_codec,
            audio_bitrate=base.audio_bitrate,
            sharpen=base.sharpen,
            sharpen_amount=base.sharpen_amount,
            pixel_format=base.pixel_format,
            denoise=base.denoise,
            denoise_strength=base.denoise_strength,
            color_boost=base.color_boost,
            scale_flags=base.scale_flags,
            profile_high=base.profile_high,
            crop_to_aspect=crop_to_aspect,
        )
