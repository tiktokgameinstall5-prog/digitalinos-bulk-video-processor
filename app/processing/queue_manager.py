"""Background worker thread that processes a queue of videos."""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass, field
from typing import Optional

from PyQt5 import QtCore

from .ffmpeg_handler import (
    EncodeOptions,
    FFmpegError,
    VideoInfo,
    process_video,
    probe_video,
)
from .upscale_handler import UpscaleOptions
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
                info = item.info or probe_video(item.input_path)
                item.info = info

                options = EncodeOptions(
                    watermark=self._job.encode_options.watermark,
                    target_height=(
                        self._job.upscale_options.target_height
                        if self._job.upscale_options.enabled
                        else self._job.encode_options.target_height
                    ),
                    video_codec=self._job.encode_options.video_codec,
                    crf=self._job.encode_options.crf,
                    preset=self._job.encode_options.preset,
                    audio_codec=self._job.encode_options.audio_codec,
                    audio_bitrate=self._job.encode_options.audio_bitrate,
                    sharpen=self._job.encode_options.sharpen,
                    sharpen_amount=self._job.encode_options.sharpen_amount,
                    pixel_format=self._job.encode_options.pixel_format,
                )

                def _pcb(pct: float, _idx: int = idx, _total: int = total) -> None:
                    self.item_progress.emit(_idx, pct)
                    self.overall_progress.emit((_idx + pct) / _total)

                process_video(
                    input_path=item.input_path,
                    output_path=item.output_path,
                    info=info,
                    options=options,
                    progress_cb=_pcb,
                    cancel_flag=self._cancelled,
                )

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
