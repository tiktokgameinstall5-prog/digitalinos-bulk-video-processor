"""Reusable UI widgets: drag-and-drop video list, card frames, labelled sliders."""

from __future__ import annotations

import os
from typing import Iterable

from PyQt5 import QtCore, QtGui, QtWidgets

from ..processing.ffmpeg_handler import VideoInfo
from ..utils.file_manager import filter_video_paths, human_size


class DropListWidget(QtWidgets.QListWidget):
    """QListWidget that accepts drag-and-drop of video files.

    Emits `files_dropped(list[str])` with a list of file paths when the user
    drops files onto the widget.
    """

    files_dropped = QtCore.pyqtSignal(list)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(False)
        self.setUniformItemSizes(False)
        self.setSpacing(2)

    # Drag + drop -------------------------------------------------------
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        md = event.mimeData()
        if md.hasUrls():
            paths = [url.toLocalFile() for url in md.urls() if url.isLocalFile()]
            files = filter_video_paths(paths)
            if files:
                self.files_dropped.emit(files)
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class VideoListItemWidget(QtWidgets.QWidget):
    """Row widget shown inside the drop list: name, resolution, duration, size, status."""

    def __init__(
        self,
        path: str,
        info: VideoInfo | None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.path = path
        self._build_ui(path, info)
        self._status = "pending"

    def _build_ui(self, path: str, info: VideoInfo | None) -> None:
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        # Left: file name + path tooltip.
        name = os.path.basename(path)
        name_label = QtWidgets.QLabel(name)
        name_label.setToolTip(path)
        name_label.setStyleSheet("font-weight: 600;")
        name_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
        )

        # Middle: metadata
        meta_bits: list[str] = []
        if info:
            meta_bits.append(info.resolution)
            meta_bits.append(info.duration_hms)
        try:
            size = os.path.getsize(path)
            meta_bits.append(human_size(size))
        except OSError:
            pass
        meta_label = QtWidgets.QLabel("  •  ".join(meta_bits) if meta_bits else "")
        meta_label.setObjectName("muted")

        # Right: status + per-item progress
        self.status_label = QtWidgets.QLabel("Pending")
        self.status_label.setObjectName("muted")
        self.status_label.setMinimumWidth(80)
        self.status_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(140)
        self.progress.setFixedHeight(6)

        layout.addWidget(name_label, stretch=2)
        layout.addWidget(meta_label, stretch=1)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)

    # ------------------------------------------------------------------
    def set_progress(self, pct: float) -> None:
        self.progress.setValue(int(max(0.0, min(1.0, pct)) * 100))

    def set_status(self, status: str) -> None:
        self._status = status
        label_map = {
            "pending": ("Pending", "#A8A9AD"),
            "running": ("Running…", "#5865F2"),
            "done": ("Done", "#23A559"),
            "error": ("Error", "#B33A3A"),
            "cancelled": ("Cancelled", "#A8A9AD"),
        }
        label, color = label_map.get(status, (status, "#A8A9AD"))
        self.status_label.setText(label)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        if status == "done":
            self.progress.setValue(100)


class Card(QtWidgets.QFrame):
    """A simple rounded container frame with the `card` object name."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 14)
        self._layout.setSpacing(8)

    def layout(self) -> QtWidgets.QVBoxLayout:  # type: ignore[override]
        return self._layout


class LabeledSlider(QtWidgets.QWidget):
    """A slider with a textual label showing the current value."""

    valueChanged = QtCore.pyqtSignal(int)

    def __init__(
        self,
        label: str,
        minimum: int,
        maximum: int,
        default: int,
        suffix: str = "",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._suffix = suffix
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._label = QtWidgets.QLabel(label)
        self._label.setMinimumWidth(80)

        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider.setRange(minimum, maximum)
        self._slider.setValue(default)

        self._value_label = QtWidgets.QLabel(f"{default}{suffix}")
        self._value_label.setMinimumWidth(50)
        self._value_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

        layout.addWidget(self._label)
        layout.addWidget(self._slider, stretch=1)
        layout.addWidget(self._value_label)

        self._slider.valueChanged.connect(self._on_changed)

    def _on_changed(self, v: int) -> None:
        self._value_label.setText(f"{v}{self._suffix}")
        self.valueChanged.emit(v)

    def value(self) -> int:
        return self._slider.value()

    def setValue(self, v: int) -> None:
        self._slider.setValue(v)
