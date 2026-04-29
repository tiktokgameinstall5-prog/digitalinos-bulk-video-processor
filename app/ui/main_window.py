"""Main window for Digitalinos Video Batch Pro."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from ..processing.ffmpeg_handler import (
    EncodeOptions,
    FFmpegError,
    QUALITY_TEMPLATES,
    VideoInfo,
    WatermarkSettings,
    apply_quality_template,
    ffmpeg_available,
    ffmpeg_version,
    probe_video,
)
from ..processing.queue_manager import BatchJob, BatchWorker, QueueItem
from ..processing.upscale_handler import (
    UpscaleOptions,
    realesrgan_available,
)
from ..utils.file_manager import filter_video_paths
from ..utils.presets import (
    default_presets_dir,
    delete_preset,
    list_presets,
    load_preset,
    save_preset,
)
from ..utils.zip_export import export_zip
from ..licensing import client as license_client
from .styles import BRAND_NAME, DARK_QSS, PRODUCT_NAME
from .widgets import Card, DropListWidget, LabeledSlider, VideoListItemWidget
from .license_dialog import LicenseDialog


BOUNCE_START_POSITIONS = [
    "bottom-right",
    "bottom-left",
    "top-right",
    "top-left",
    "center",
    "random",
]


POSITIONS = [
    "top-left",
    "top",
    "top-right",
    "center",
    "bottom-left",
    "bottom",
    "bottom-right",
    "custom",
]


HELP_TEXT = f"""
<h2>{BRAND_NAME} {PRODUCT_NAME} — How to use</h2>

<b>1. Add videos</b>
<ul>
<li>Click <b>Add Videos…</b> or drag &amp; drop files into the left panel.</li>
<li>Supported formats: mp4, mov, mkv, avi, webm, flv, m4v, mpg, mpeg, wmv, ts.</li>
<li>Each row shows the <b>file name</b>, <b>resolution</b>, <b>duration</b> and <b>size</b>.</li>
<li>There is <b>no hard limit</b> on video duration or count — but longer/bigger
jobs will take more disk space and CPU time.</li>
</ul>

<b>2. Configure watermark (optional)</b>
<ul>
<li>Enable the watermark and choose <i>text</i> or <i>image</i> mode.</li>
<li>Pick a preset position, or choose <i>custom</i> and set X/Y in pixels.</li>
<li>For text: adjust opacity, scale, font size, colour, and optionally pick a
<i>font file</i> (any .ttf / .otf — e.g. from <code>C:\\Windows\\Fonts</code>).
A drop shadow is applied automatically for legibility.</li>
<li>For image: PNG with transparency gives the cleanest results. The image is
locked in place for the whole video by default (no unintended flicker).</li>
<li><b>Bounce watermark (DVD-style)</b>: tick this to make the text or logo
drift diagonally and bounce off the edges of the video. Pick
<i>slow / medium / fast</i>. Position / custom X / Y are ignored while
bounce is on.</li>
</ul>

<b>3. Pick a quality template</b>
<ul>
<li>Go to the <b>Output</b> tab and pick a template:</li>
<li><b>Original</b> — keep source resolution, balanced quality.</li>
<li><b>YouTube 1080p Clean</b> — 1080p with mild sharpening, great for social.</li>
<li><b>CapCut Ultra HD (1440p)</b> — 1440p with strong sharpening for crisp,
clean-looking clips (blur reduction).</li>
<li><b>4K Crisp (2160p)</b> — full 4K with Lanczos scale + unsharp for the
cleanest final output (slowest).</li>
<li><b>Fast Preview</b> — 720p draft encode, very fast.</li>
</ul>

<b>4. Output naming</b>
<ul>
<li>Default: <code>&lt;sourcename&gt;_processed.mp4</code>.</li>
<li>Tick <b>Rename outputs sequentially</b> to get <code>1_abc123.mp4</code>,
<code>2_def456.mp4</code>, … — perfect for bulk uploads.</li>
</ul>

<b>5. Presets</b>
<ul>
<li>Save your exact settings (watermark + upscale) on the <b>Presets</b> tab.</li>
<li>Examples: <i>YouTube watermark</i>, <i>4K upscale + logo</i>.</li>
</ul>

<b>6. Run the batch</b>
<ul>
<li>Once videos are loaded, <b>Process All</b> becomes active. Click it.</li>
<li>Watch per-file + overall progress. Use <b>Cancel</b> to stop mid-batch.</li>
<li>After at least one video succeeds, <b>Export All as ZIP</b> unlocks.</li>
</ul>

<b>7. Tips</b>
<ul>
<li>For CapCut-level crispness: use <b>CapCut Ultra HD</b> + a PNG logo.</li>
<li>For YouTube Shorts / Reels: use <b>YouTube 1080p Clean</b> with bottom-right
watermark at 80% opacity.</li>
<li>If a file fails, hover its row to see the error — usually a corrupt source.</li>
<li>All processing is 100% <b>offline</b>. No videos ever leave your PC.</li>
</ul>
"""


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{BRAND_NAME} {PRODUCT_NAME}")
        self.resize(1320, 860)
        self.setMinimumSize(1080, 700)

        self._items: list[QueueItem] = []
        self._item_widgets: list[VideoListItemWidget] = []
        self._worker: Optional[BatchWorker] = None
        self._worker_thread: Optional[QtCore.QThread] = None
        self._output_dir = os.path.join(str(Path.home()), "Digitalinos", "output")
        os.makedirs(self._output_dir, exist_ok=True)
        self._processing = False
        self._any_done = False

        self.setStyleSheet(DARK_QSS)
        self._build_ui()
        self._check_tool_availability()
        self._refresh_preset_list()
        self._refresh_button_states()
        # Refresh license badge + try a silent re-verify in the background.
        self._refresh_license_pill()
        QtCore.QTimer.singleShot(50, self._silent_reverify)

    def _silent_reverify(self) -> None:
        """Best-effort: refresh JWT in background. Failures are silent."""
        try:
            license_client.reverify(timeout=5.0)
        except Exception:  # noqa: BLE001 - never crash on license refresh
            pass
        self._refresh_license_pill()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addLayout(self._build_header())

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self._build_left_panel(), stretch=3)
        body.addWidget(self._build_right_panel(), stretch=2)
        root.addLayout(body, stretch=1)

        root.addWidget(self._build_bottom_panel())
        self.setCentralWidget(central)

    def _build_header(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()

        brand = QtWidgets.QLabel(BRAND_NAME.upper())
        brand.setObjectName("brand")
        title = QtWidgets.QLabel(PRODUCT_NAME)
        title.setObjectName("h1")
        subtitle = QtWidgets.QLabel("Offline batch watermark, upscale &amp; export")
        subtitle.setObjectName("muted")

        col = QtWidgets.QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(brand)
        col.addWidget(title)
        col.addWidget(subtitle)
        row.addLayout(col)
        row.addStretch(1)

        # License pill (clickable -> opens License dialog)
        self.license_pill = QtWidgets.QPushButton("License")
        self.license_pill.setObjectName("licensePill")
        self.license_pill.setCursor(QtCore.Qt.PointingHandCursor)
        self.license_pill.setStyleSheet(
            "QPushButton#licensePill {"
            "  padding: 6px 14px;"
            "  border-radius: 14px;"
            "  background: rgba(27,181,196,0.12);"
            "  color: #1BB5C4;"
            "  border: 1px solid rgba(27,181,196,0.35);"
            "  font-weight: 600;"
            "}"
            "QPushButton#licensePill:hover { background: rgba(27,181,196,0.20); }"
        )
        self.license_pill.clicked.connect(self._open_license_dialog)
        row.addWidget(self.license_pill)

        self.tool_status = QtWidgets.QLabel("Checking FFmpeg…")
        self.tool_status.setObjectName("muted")
        self.tool_status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        row.addWidget(self.tool_status)
        return row

    # ------------------------------------------------------------------
    # License plumbing
    # ------------------------------------------------------------------
    def _refresh_license_pill(self) -> None:
        state = license_client.load_state()
        if state.has_license and state.is_active():
            self.license_pill.setText(f"● {state.plan or 'Pro'} active")
            self.license_pill.setStyleSheet(
                "QPushButton#licensePill {"
                "  padding: 6px 14px; border-radius: 14px;"
                "  background: rgba(34,197,94,0.14); color: #22c55e;"
                "  border: 1px solid rgba(34,197,94,0.35); font-weight: 600;"
                "} QPushButton#licensePill:hover { background: rgba(34,197,94,0.22); }"
            )
        else:
            remaining = state.trial_remaining
            self.license_pill.setText(
                f"Trial — {remaining}/{license_client.TRIAL_LIMIT} videos"
                if remaining > 0
                else "Trial used — activate"
            )
            color = "#1BB5C4" if remaining > 0 else "#ef4444"
            bg = "rgba(27,181,196,0.12)" if remaining > 0 else "rgba(239,68,68,0.14)"
            self.license_pill.setStyleSheet(
                f"QPushButton#licensePill {{"
                f"  padding: 6px 14px; border-radius: 14px;"
                f"  background: {bg}; color: {color};"
                f"  border: 1px solid {color}55; font-weight: 600;"
                f"}}"
            )

    def _open_license_dialog(self) -> None:
        dlg = LicenseDialog(self)
        dlg.state_changed.connect(self._refresh_license_pill)
        dlg.exec_()
        self._refresh_license_pill()

    # ------------------------------------------------------------------
    # Left panel: upload list
    # ------------------------------------------------------------------
    def _build_left_panel(self) -> QtWidgets.QWidget:
        card = Card()
        layout = card.layout()

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Videos")
        title.setObjectName("h2")
        header.addWidget(title)
        header.addStretch(1)

        self.count_label = QtWidgets.QLabel("0 files")
        self.count_label.setObjectName("muted")
        header.addWidget(self.count_label)
        layout.addLayout(header)

        btn_row = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add Videos…")
        self.add_btn.clicked.connect(self._browse_for_videos)
        self.remove_btn = QtWidgets.QPushButton("Remove Selected")
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.clicked.connect(self._clear_list)
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.remove_btn)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.list_widget = DropListWidget()
        self.list_widget.files_dropped.connect(self._add_files)
        layout.addWidget(self.list_widget, stretch=1)

        hint = QtWidgets.QLabel("Drag &amp; drop video files here, or click 'Add Videos…'.")
        hint.setObjectName("muted")
        hint.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(hint)
        return card

    # ------------------------------------------------------------------
    # Right panel: settings tabs
    # ------------------------------------------------------------------
    def _build_right_panel(self) -> QtWidgets.QWidget:
        card = Card()
        layout = card.layout()

        title = QtWidgets.QLabel("Settings")
        title.setObjectName("h2")
        layout.addWidget(title)

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_watermark_tab(), "Watermark")
        tabs.addTab(self._build_upscale_tab(), "Upscale")
        tabs.addTab(self._build_output_tab(), "Output")
        tabs.addTab(self._build_preset_tab(), "Presets")
        tabs.addTab(self._build_help_tab(), "Help")
        layout.addWidget(tabs, stretch=1)
        return card

    def _build_watermark_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w)
        l.setContentsMargins(4, 4, 4, 4)
        l.setSpacing(8)

        self.wm_enable = QtWidgets.QCheckBox("Enable watermark / overlay")
        l.addWidget(self.wm_enable)

        # Mode
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Type:"))
        self.wm_mode = QtWidgets.QComboBox()
        self.wm_mode.addItems(["text", "image"])
        mode_row.addWidget(self.wm_mode)
        mode_row.addStretch(1)
        l.addLayout(mode_row)

        # Text input
        self.wm_text = QtWidgets.QLineEdit()
        self.wm_text.setPlaceholderText("Watermark text (e.g. @YourChannel)")
        l.addWidget(self.wm_text)

        # Font file picker (text mode only)
        font_row = QtWidgets.QHBoxLayout()
        font_row.addWidget(QtWidgets.QLabel("Font file:"))
        self.wm_font_path = QtWidgets.QLineEdit()
        self.wm_font_path.setPlaceholderText("Optional .ttf / .otf — defaults to system font")
        browse_font = QtWidgets.QPushButton("Browse…")
        browse_font.clicked.connect(self._browse_font_file)
        clear_font = QtWidgets.QPushButton("Clear")
        clear_font.clicked.connect(lambda: self.wm_font_path.clear())
        font_row.addWidget(self.wm_font_path, stretch=1)
        font_row.addWidget(browse_font)
        font_row.addWidget(clear_font)
        l.addLayout(font_row)

        # Shadow toggle
        self.wm_shadow = QtWidgets.QCheckBox("Drop shadow behind text (recommended)")
        self.wm_shadow.setChecked(True)
        l.addWidget(self.wm_shadow)

        # Image chooser
        img_row = QtWidgets.QHBoxLayout()
        img_row.addWidget(QtWidgets.QLabel("Logo:"))
        self.wm_image_path = QtWidgets.QLineEdit()
        self.wm_image_path.setPlaceholderText("Logo image (PNG with alpha recommended)")
        browse_img = QtWidgets.QPushButton("Browse…")
        browse_img.clicked.connect(self._browse_watermark_image)
        img_row.addWidget(self.wm_image_path, stretch=1)
        img_row.addWidget(browse_img)
        l.addLayout(img_row)

        # Position
        pos_row = QtWidgets.QHBoxLayout()
        pos_row.addWidget(QtWidgets.QLabel("Position:"))
        self.wm_position = QtWidgets.QComboBox()
        self.wm_position.addItems(POSITIONS)
        self.wm_position.setCurrentText("bottom-right")
        pos_row.addWidget(self.wm_position)
        pos_row.addStretch(1)
        l.addLayout(pos_row)

        # Custom X/Y (enabled only when position == custom)
        xy_row = QtWidgets.QHBoxLayout()
        xy_row.addWidget(QtWidgets.QLabel("Custom X:"))
        self.wm_x = QtWidgets.QSpinBox()
        self.wm_x.setRange(0, 10000)
        xy_row.addWidget(self.wm_x)
        xy_row.addWidget(QtWidgets.QLabel("Y:"))
        self.wm_y = QtWidgets.QSpinBox()
        self.wm_y.setRange(0, 10000)
        xy_row.addWidget(self.wm_y)
        xy_row.addStretch(1)
        l.addLayout(xy_row)

        # Bounce (DVD-screensaver style animation)
        bounce_row = QtWidgets.QHBoxLayout()
        self.wm_bounce = QtWidgets.QCheckBox("Bounce watermark (DVD-style)")
        self.wm_bounce.setToolTip(
            "Make the watermark drift diagonally and bounce off the edges "
            "of the video. Works for both text and image logos."
        )
        bounce_row.addWidget(self.wm_bounce)
        bounce_row.addWidget(QtWidgets.QLabel("speed:"))
        self.wm_bounce_speed = QtWidgets.QComboBox()
        self.wm_bounce_speed.addItems(["slow", "medium", "fast"])
        self.wm_bounce_speed.setCurrentText("slow")
        bounce_row.addWidget(self.wm_bounce_speed)
        bounce_row.addStretch(1)
        l.addLayout(bounce_row)

        # Bounce start position — lets the user choose which corner the
        # watermark spawns from when bounce is enabled.
        start_row = QtWidgets.QHBoxLayout()
        start_row.addWidget(QtWidgets.QLabel("Bounce starts from:"))
        self.wm_bounce_start = QtWidgets.QComboBox()
        self.wm_bounce_start.addItems(BOUNCE_START_POSITIONS)
        self.wm_bounce_start.setCurrentText("bottom-right")
        self.wm_bounce_start.setToolTip(
            "Pick the corner where the watermark first appears when bouncing. "
            "Use 'random' to spawn from a different spot every render."
        )
        start_row.addWidget(self.wm_bounce_start)
        start_row.addStretch(1)
        l.addLayout(start_row)

        # Opacity + scale + font size
        self.wm_opacity = LabeledSlider("Opacity", 0, 100, 80, suffix="%")
        l.addWidget(self.wm_opacity)
        self.wm_scale = LabeledSlider("Scale", 10, 400, 100, suffix="%")
        l.addWidget(self.wm_scale)
        self.wm_fontsize = LabeledSlider("Font size", 8, 200, 36, suffix="px")
        l.addWidget(self.wm_fontsize)

        # Font color
        color_row = QtWidgets.QHBoxLayout()
        color_row.addWidget(QtWidgets.QLabel("Font color:"))
        self.wm_fontcolor = QtWidgets.QComboBox()
        self.wm_fontcolor.addItems(["white", "black", "red", "yellow", "cyan", "magenta"])
        color_row.addWidget(self.wm_fontcolor)
        color_row.addStretch(1)
        l.addLayout(color_row)

        # React to mode changes
        def _update_mode(_: str = "") -> None:
            is_text = self.wm_mode.currentText() == "text"
            for widget in (self.wm_text, self.wm_font_path, self.wm_shadow,
                           self.wm_fontsize, self.wm_fontcolor):
                widget.setEnabled(is_text)
            self.wm_image_path.setEnabled(not is_text)

        self.wm_mode.currentTextChanged.connect(_update_mode)
        _update_mode()

        def _update_custom_xy(_: str = "") -> None:
            bouncing = self.wm_bounce.isChecked()
            is_custom = self.wm_position.currentText() == "custom"
            # Bounce overrides manual positioning — disable all placement
            # controls so the user isn't confused by dead knobs.
            self.wm_position.setEnabled(not bouncing)
            self.wm_x.setEnabled(is_custom and not bouncing)
            self.wm_y.setEnabled(is_custom and not bouncing)
            self.wm_bounce_speed.setEnabled(bouncing)
            self.wm_bounce_start.setEnabled(bouncing)

        self.wm_position.currentTextChanged.connect(_update_custom_xy)
        self.wm_bounce.stateChanged.connect(lambda _=0: _update_custom_xy())
        _update_custom_xy()

        l.addStretch(1)
        return w

    def _build_upscale_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w)
        l.setContentsMargins(4, 4, 4, 4)
        l.setSpacing(8)

        self.up_enable = QtWidgets.QCheckBox("Enable upscaling")
        l.addWidget(self.up_enable)

        tgt_row = QtWidgets.QHBoxLayout()
        tgt_row.addWidget(QtWidgets.QLabel("Target resolution:"))
        self.up_target = QtWidgets.QComboBox()
        self.up_target.addItems(["1080p (1920×1080)", "1440p (2560×1440)", "4K (3840×2160)"])
        self.up_target.setCurrentIndex(2)
        tgt_row.addWidget(self.up_target)
        tgt_row.addStretch(1)
        l.addLayout(tgt_row)

        backend_row = QtWidgets.QHBoxLayout()
        backend_row.addWidget(QtWidgets.QLabel("Backend:"))
        self.up_backend = QtWidgets.QComboBox()
        self.up_backend.addItems(["auto", "ffmpeg", "realesrgan"])
        backend_row.addWidget(self.up_backend)
        backend_row.addStretch(1)
        l.addLayout(backend_row)

        info_text = QtWidgets.QLabel(
            "<b>Real-ESRGAN</b> does frame-by-frame AI upscale (best quality, slow). "
            "<b>FFmpeg Lanczos</b> is instant and still gives CapCut-style clean "
            "output when combined with the quality template sharpening in the "
            "<i>Output</i> tab."
        )
        info_text.setObjectName("muted")
        info_text.setWordWrap(True)
        l.addWidget(info_text)

        l.addStretch(1)
        return w

    def _build_output_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w)
        l.setContentsMargins(4, 4, 4, 4)
        l.setSpacing(8)

        # Output folder
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Output folder:"))
        self.out_dir_edit = QtWidgets.QLineEdit(self._output_dir)
        row.addWidget(self.out_dir_edit, stretch=1)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse_output_dir)
        row.addWidget(browse)
        l.addLayout(row)

        # Quality template
        tpl_row = QtWidgets.QHBoxLayout()
        tpl_row.addWidget(QtWidgets.QLabel("Quality template:"))
        self.qual_template = QtWidgets.QComboBox()
        self.qual_template.addItems(list(QUALITY_TEMPLATES.keys()))
        # Default to a quality template that ACTUALLY improves the output —
        # "Original (no changes)" used to be the default and silently disabled
        # all of the sharpening / denoise machinery, leaving users with bigger
        # files but no visible quality boost.
        self.qual_template.setCurrentText("YouTube 1080p Clean")
        self.qual_template.setToolTip(
            "Quick presets for quality. CapCut Ultra HD and 4K Crisp apply "
            "Lanczos scaling + sharpening for a clean, non-blurry look."
        )
        tpl_row.addWidget(self.qual_template, stretch=1)
        l.addLayout(tpl_row)

        # Encoder preset + CRF (manual override)
        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("Encoder preset:"))
        self.enc_preset = QtWidgets.QComboBox()
        self.enc_preset.addItems(["ultrafast", "fast", "medium", "slow"])
        self.enc_preset.setCurrentText("medium")
        row2.addWidget(self.enc_preset)
        row2.addWidget(QtWidgets.QLabel("CRF:"))
        self.enc_crf = QtWidgets.QSpinBox()
        self.enc_crf.setRange(0, 51)
        self.enc_crf.setValue(20)
        row2.addWidget(self.enc_crf)
        row2.addStretch(1)
        l.addLayout(row2)

        # Sharpen (manual)
        sharp_row = QtWidgets.QHBoxLayout()
        self.sharpen_enable = QtWidgets.QCheckBox("Sharpen (extra crisp)")
        sharp_row.addWidget(self.sharpen_enable)
        self.sharpen_amount = QtWidgets.QDoubleSpinBox()
        self.sharpen_amount.setRange(0.0, 1.5)
        self.sharpen_amount.setSingleStep(0.1)
        self.sharpen_amount.setValue(0.6)
        sharp_row.addWidget(QtWidgets.QLabel("amount:"))
        sharp_row.addWidget(self.sharpen_amount)
        sharp_row.addStretch(1)
        l.addLayout(sharp_row)

        # Sequential naming
        self.rename_sequential = QtWidgets.QCheckBox(
            "Rename outputs sequentially (1_abc123.mp4, 2_def456.mp4, …)"
        )
        self.rename_sequential.setToolTip(
            "Useful for bulk uploads. Each output gets a 1-based index + a "
            "random 6-char code appended, e.g. '03_ab12cd.mp4'."
        )
        l.addWidget(self.rename_sequential)

        note = QtWidgets.QLabel(
            "The quality template sets encoder preset / CRF / sharpening "
            "automatically — pick one first, then fine-tune."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        l.addWidget(note)

        # When a template is picked, sync the fields so the user sees the effect.
        self.qual_template.currentTextChanged.connect(self._apply_quality_template_to_ui)

        l.addStretch(1)
        return w

    def _build_preset_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w)
        l.setContentsMargins(4, 4, 4, 4)
        l.setSpacing(8)

        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("Name:"))
        self.preset_name = QtWidgets.QLineEdit()
        self.preset_name.setPlaceholderText("e.g. YouTube watermark, 4K upscale + logo")
        name_row.addWidget(self.preset_name, stretch=1)
        l.addLayout(name_row)

        btn_row = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton("Save Preset")
        save_btn.clicked.connect(self._save_preset)
        load_btn = QtWidgets.QPushButton("Load")
        load_btn.clicked.connect(self._load_selected_preset)
        del_btn = QtWidgets.QPushButton("Delete")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(self._delete_selected_preset)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(load_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch(1)
        l.addLayout(btn_row)

        self.preset_list = QtWidgets.QListWidget()
        self.preset_list.itemDoubleClicked.connect(lambda _=None: self._load_selected_preset())
        l.addWidget(self.preset_list, stretch=1)

        path_label = QtWidgets.QLabel(f"Stored in: {default_presets_dir()}")
        path_label.setObjectName("muted")
        path_label.setWordWrap(True)
        l.addWidget(path_label)
        return w

    def _build_help_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(w)
        l.setContentsMargins(4, 4, 4, 4)
        viewer = QtWidgets.QTextBrowser()
        viewer.setOpenExternalLinks(True)
        viewer.setHtml(HELP_TEXT)
        l.addWidget(viewer, stretch=1)
        return w

    # ------------------------------------------------------------------
    # Bottom panel: progress + actions
    # ------------------------------------------------------------------
    def _build_bottom_panel(self) -> QtWidgets.QWidget:
        card = Card()
        layout = card.layout()

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Overall:"))
        self.overall_bar = QtWidgets.QProgressBar()
        self.overall_bar.setRange(0, 100)
        row.addWidget(self.overall_bar, stretch=1)
        layout.addLayout(row)

        btns = QtWidgets.QHBoxLayout()
        self.process_btn = QtWidgets.QPushButton("Process All")
        self.process_btn.setObjectName("primary")
        self.process_btn.clicked.connect(self._on_process_clicked)

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)

        self.zip_btn = QtWidgets.QPushButton("Export All as ZIP")
        self.zip_btn.setObjectName("success")
        self.zip_btn.clicked.connect(self._export_zip)

        self.open_output_btn = QtWidgets.QPushButton("Open Output Folder")
        self.open_output_btn.clicked.connect(self._open_output_dir)

        btns.addWidget(self.process_btn)
        btns.addWidget(self.cancel_btn)
        btns.addStretch(1)
        btns.addWidget(self.open_output_btn)
        btns.addWidget(self.zip_btn)
        layout.addLayout(btns)

        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setObjectName("muted")
        layout.addWidget(self.status_label)
        return card

    # ------------------------------------------------------------------
    # Button state logic
    # ------------------------------------------------------------------
    def _refresh_button_states(self) -> None:
        """Enable/disable action buttons based on queue + processing state."""
        has_items = len(self._items) > 0
        ffmpeg_ok = ffmpeg_available()

        # Process All: needs items, FFmpeg, and no running job.
        self.process_btn.setEnabled(has_items and ffmpeg_ok and not self._processing)
        # Cancel: only while processing.
        self.cancel_btn.setEnabled(self._processing)
        # Export ZIP: requires at least one completed item.
        self.zip_btn.setEnabled(self._any_done and not self._processing)
        # Add / Remove / Clear: disabled while processing.
        self.add_btn.setEnabled(not self._processing)
        self.remove_btn.setEnabled(not self._processing and has_items)
        self.clear_btn.setEnabled(not self._processing and has_items)

    # ------------------------------------------------------------------
    # Tool availability banner
    # ------------------------------------------------------------------
    def _check_tool_availability(self) -> None:
        parts = []
        if ffmpeg_available():
            v = ffmpeg_version()
            version_str = v.split(" ")[2] if v and len(v.split(" ")) > 2 else "ok"
            parts.append(f"<span style='color:#1BB5C4;font-weight:600'>FFmpeg ✓</span> ({version_str})")
        else:
            parts.append("<span style='color:#EF4444;font-weight:600'>FFmpeg ✗</span>")
        if realesrgan_available():
            parts.append("<span style='color:#1BB5C4;font-weight:600'>Real-ESRGAN ✓</span>")
        else:
            parts.append("<span style='color:#F59E0B;font-weight:600'>Real-ESRGAN ✗</span> (FFmpeg fallback)")
        self.tool_status.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts))
        self.tool_status.setTextFormat(QtCore.Qt.RichText)
        if not ffmpeg_available():
            QtWidgets.QMessageBox.warning(
                self,
                "FFmpeg not found",
                "FFmpeg was not found on your PATH. Install it before processing videos.\n\n"
                "See the README for platform-specific setup instructions.",
            )

    # ------------------------------------------------------------------
    # File management
    # ------------------------------------------------------------------
    def _browse_for_videos(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select videos",
            str(Path.home()),
            "Video files (*.mp4 *.mov *.mkv *.avi *.webm *.flv *.m4v *.mpg *.mpeg *.wmv *.ts);;All files (*.*)",
        )
        files = filter_video_paths(paths)
        if files:
            self._add_files(files)

    def _browse_watermark_image(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select watermark image",
            str(Path.home()),
            "Image files (*.png *.jpg *.jpeg *.bmp *.webp);;All files (*.*)",
        )
        if path:
            self.wm_image_path.setText(path)

    def _browse_font_file(self) -> None:
        start = str(Path.home())
        if os.name == "nt":
            fonts = r"C:\Windows\Fonts"
            if os.path.isdir(fonts):
                start = fonts
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select a font file",
            start,
            "Font files (*.ttf *.otf *.ttc);;All files (*.*)",
        )
        if path:
            self.wm_font_path.setText(path)

    def _browse_output_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select output folder", self.out_dir_edit.text() or str(Path.home())
        )
        if path:
            self.out_dir_edit.setText(path)
            self._output_dir = path

    def _add_files(self, paths: list[str]) -> None:
        added = 0
        for p in paths:
            if any(item.input_path == p for item in self._items):
                continue
            info: VideoInfo | None = None
            try:
                info = probe_video(p)
            except FFmpegError as exc:
                QtWidgets.QMessageBox.warning(
                    self, "Cannot read video", f"Skipping {p}:\n{exc}"
                )
                continue

            item = QueueItem(input_path=p, info=info)
            self._items.append(item)
            self._append_list_row(item)
            added += 1

        self._update_count_label()
        if added:
            self.status_label.setText(f"Added {added} file(s).")
        self._refresh_button_states()

    def _append_list_row(self, item: QueueItem) -> None:
        row_widget = VideoListItemWidget(item.input_path, item.info, self.list_widget)
        list_item = QtWidgets.QListWidgetItem()
        list_item.setSizeHint(row_widget.sizeHint())
        self.list_widget.addItem(list_item)
        self.list_widget.setItemWidget(list_item, row_widget)
        self._item_widgets.append(row_widget)

    def _remove_selected(self) -> None:
        rows = sorted({self.list_widget.row(it) for it in self.list_widget.selectedItems()}, reverse=True)
        for row in rows:
            self.list_widget.takeItem(row)
            del self._items[row]
            del self._item_widgets[row]
        self._update_count_label()
        self._refresh_button_states()

    def _clear_list(self) -> None:
        self.list_widget.clear()
        self._items.clear()
        self._item_widgets.clear()
        self._any_done = False
        self._update_count_label()
        self._refresh_button_states()

    def _update_count_label(self) -> None:
        n = len(self._items)
        self.count_label.setText(f"{n} file{'s' if n != 1 else ''}")

    # ------------------------------------------------------------------
    # Quality template -> UI sync
    # ------------------------------------------------------------------
    def _apply_quality_template_to_ui(self, _: str = "") -> None:
        name = self.qual_template.currentText()
        tpl = QUALITY_TEMPLATES.get(name)
        if tpl is None:
            return
        self.enc_preset.setCurrentText(tpl.preset)
        self.enc_crf.setValue(int(tpl.crf))
        self.sharpen_enable.setChecked(bool(tpl.sharpen))
        self.sharpen_amount.setValue(float(tpl.sharpen_amount))
        if tpl.target_height is not None:
            self.up_enable.setChecked(True)
            heights = [1080, 1440, 2160]
            if tpl.target_height in heights:
                self.up_target.setCurrentIndex(heights.index(tpl.target_height))

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------
    def _refresh_preset_list(self) -> None:
        self.preset_list.clear()
        self.preset_list.addItems(list_presets())

    def _collect_watermark(self) -> WatermarkSettings:
        return WatermarkSettings(
            enabled=self.wm_enable.isChecked(),
            mode=self.wm_mode.currentText(),
            text=self.wm_text.text(),
            image_path=self.wm_image_path.text(),
            position=self.wm_position.currentText(),
            custom_x=int(self.wm_x.value()),
            custom_y=int(self.wm_y.value()),
            opacity=self.wm_opacity.value() / 100.0,
            scale=self.wm_scale.value() / 100.0,
            font_size=self.wm_fontsize.value(),
            font_color=self.wm_fontcolor.currentText(),
            font_file=self.wm_font_path.text().strip(),
            shadow=self.wm_shadow.isChecked(),
            bounce=self.wm_bounce.isChecked(),
            bounce_speed=self.wm_bounce_speed.currentText(),
            bounce_start=self.wm_bounce_start.currentText(),
        )

    def _collect_upscale(self) -> UpscaleOptions:
        heights = [1080, 1440, 2160]
        target = heights[self.up_target.currentIndex()]
        return UpscaleOptions(
            enabled=self.up_enable.isChecked(),
            target_height=target,
            backend=self.up_backend.currentText(),
        )

    def _apply_watermark(self, wm: WatermarkSettings) -> None:
        self.wm_enable.setChecked(wm.enabled)
        self.wm_mode.setCurrentText(wm.mode)
        self.wm_text.setText(wm.text)
        self.wm_image_path.setText(wm.image_path)
        self.wm_font_path.setText(getattr(wm, "font_file", "") or "")
        self.wm_shadow.setChecked(bool(getattr(wm, "shadow", True)))
        self.wm_bounce.setChecked(bool(getattr(wm, "bounce", False)))
        bs = str(getattr(wm, "bounce_speed", "slow") or "slow")
        idx = self.wm_bounce_speed.findText(bs)
        if idx >= 0:
            self.wm_bounce_speed.setCurrentIndex(idx)
        bstart = str(getattr(wm, "bounce_start", "bottom-right") or "bottom-right")
        idx = self.wm_bounce_start.findText(bstart)
        if idx >= 0:
            self.wm_bounce_start.setCurrentIndex(idx)
        if wm.position in POSITIONS:
            self.wm_position.setCurrentText(wm.position)
        self.wm_x.setValue(int(wm.custom_x))
        self.wm_y.setValue(int(wm.custom_y))
        self.wm_opacity.setValue(int(round(wm.opacity * 100)))
        self.wm_scale.setValue(int(round(wm.scale * 100)))
        self.wm_fontsize.setValue(int(wm.font_size))
        if wm.font_color:
            idx = self.wm_fontcolor.findText(wm.font_color)
            if idx >= 0:
                self.wm_fontcolor.setCurrentIndex(idx)

    def _apply_upscale(self, up: UpscaleOptions) -> None:
        self.up_enable.setChecked(up.enabled)
        heights = [1080, 1440, 2160]
        try:
            self.up_target.setCurrentIndex(heights.index(up.target_height))
        except ValueError:
            self.up_target.setCurrentIndex(2)
        idx = self.up_backend.findText(up.backend)
        self.up_backend.setCurrentIndex(idx if idx >= 0 else 0)

    def _save_preset(self) -> None:
        name = self.preset_name.text().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, "Preset", "Please enter a preset name.")
            return
        try:
            save_preset(name, self._collect_watermark(), self._collect_upscale())
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Preset", f"Failed to save:\n{exc}")
            return
        self._refresh_preset_list()
        self.status_label.setText(f"Saved preset '{name}'.")

    def _load_selected_preset(self) -> None:
        items = self.preset_list.selectedItems()
        name = items[0].text() if items else self.preset_name.text().strip()
        if not name:
            return
        try:
            wm, up = load_preset(name)
        except (OSError, ValueError) as exc:
            QtWidgets.QMessageBox.critical(self, "Preset", f"Failed to load:\n{exc}")
            return
        self._apply_watermark(wm)
        self._apply_upscale(up)
        self.preset_name.setText(name)
        self.status_label.setText(f"Loaded preset '{name}'.")

    def _delete_selected_preset(self) -> None:
        items = self.preset_list.selectedItems()
        if not items:
            return
        name = items[0].text()
        if QtWidgets.QMessageBox.question(
            self, "Delete preset", f"Delete preset '{name}'?"
        ) == QtWidgets.QMessageBox.Yes:
            delete_preset(name)
            self._refresh_preset_list()

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    def _build_encode_options(self, wm: WatermarkSettings) -> EncodeOptions:
        base = EncodeOptions(
            watermark=wm,
            target_height=None,
            preset=self.enc_preset.currentText(),
            crf=int(self.enc_crf.value()),
            sharpen=self.sharpen_enable.isChecked(),
            sharpen_amount=float(self.sharpen_amount.value()),
        )
        return apply_quality_template(base, self.qual_template.currentText())

    def _on_process_clicked(self) -> None:
        if not self._items:
            QtWidgets.QMessageBox.information(
                self, "No videos", "Add at least one video before processing."
            )
            return
        if not ffmpeg_available():
            QtWidgets.QMessageBox.critical(
                self, "FFmpeg missing",
                "FFmpeg is not installed or not on PATH."
            )
            return

        # License / trial gate.
        allowed, reason = license_client.can_render()
        if not allowed:
            if reason == "trial_exhausted":
                msg = (
                    "Your free trial of {limit} videos is used up on this device.\n\n"
                    "Activate a license to keep processing — paste your "
                    "DGIT-XXXX-XXXX-XXXX-XXXX key in the License dialog."
                ).format(limit=license_client.TRIAL_LIMIT)
            else:
                msg = (
                    "Your license has expired. Renew it from the dashboard at "
                    "https://digitalinos-web.vercel.app/dashboard, then "
                    "re-activate."
                )
            ret = QtWidgets.QMessageBox.warning(
                self, "License required", msg,
                QtWidgets.QMessageBox.Ok | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Ok,
            )
            if ret == QtWidgets.QMessageBox.Ok:
                self._open_license_dialog()
            return

        # Heads-up if the trial is about to end.
        state = license_client.load_state()
        if not state.has_license and state.trial_remaining <= 3:
            self.status_label.setText(
                f"Trial: {state.trial_remaining}/{license_client.TRIAL_LIMIT} videos left."
            )

        self._output_dir = self.out_dir_edit.text().strip() or self._output_dir
        os.makedirs(self._output_dir, exist_ok=True)

        wm = self._collect_watermark()
        enc = self._build_encode_options(wm)
        up = self._collect_upscale()

        for widget in self._item_widgets:
            widget.set_status("pending")
            widget.set_progress(0.0)
        self.overall_bar.setValue(0)
        self._any_done = False

        job = BatchJob(
            items=list(self._items),
            output_dir=self._output_dir,
            encode_options=enc,
            upscale_options=up,
            sequential_naming=self.rename_sequential.isChecked(),
        )

        self._worker_thread = QtCore.QThread(self)
        self._worker = BatchWorker(job)
        self._worker.moveToThread(self._worker_thread)

        self._worker.item_started.connect(self._on_item_started)
        self._worker.item_progress.connect(self._on_item_progress)
        self._worker.item_finished.connect(self._on_item_finished)
        self._worker.item_failed.connect(self._on_item_failed)
        self._worker.overall_progress.connect(self._on_overall_progress)
        self._worker.batch_finished.connect(self._on_batch_finished)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.batch_finished.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)

        self._processing = True
        self.status_label.setText("Processing…")
        self._refresh_button_states()
        self._worker_thread.start()

    def _on_cancel_clicked(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.status_label.setText("Cancelling…")

    # Worker signal handlers -----------------------------------------------
    def _on_item_started(self, idx: int) -> None:
        if 0 <= idx < len(self._item_widgets):
            self._item_widgets[idx].set_status("running")

    def _on_item_progress(self, idx: int, pct: float) -> None:
        if 0 <= idx < len(self._item_widgets):
            self._item_widgets[idx].set_progress(pct)

    def _on_item_finished(self, idx: int, output_path: str) -> None:
        if 0 <= idx < len(self._item_widgets):
            self._item_widgets[idx].set_status("done")
            self._item_widgets[idx].set_progress(1.0)
        self._any_done = True
        # Count trial usage AFTER a successful render so failed encodes don't
        # eat the user's trial allowance.
        license_client.increment_trial()
        self._refresh_license_pill()
        self.status_label.setText(f"Finished: {os.path.basename(output_path)}")
        self._refresh_button_states()

    def _on_item_failed(self, idx: int, message: str) -> None:
        if 0 <= idx < len(self._item_widgets):
            self._item_widgets[idx].set_status("error")
        self.status_label.setText(f"Error on item {idx + 1}: {message}")

    def _on_overall_progress(self, pct: float) -> None:
        self.overall_bar.setValue(int(pct * 100))

    def _on_batch_finished(self, outputs: list) -> None:
        self._processing = False
        ok = sum(1 for it in self._items if it.status == "done")
        failed = sum(1 for it in self._items if it.status == "error")
        self._any_done = ok > 0
        self.status_label.setText(
            f"Batch finished. {ok} succeeded, {failed} failed. Output: {self._output_dir}"
        )
        self._refresh_button_states()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _export_zip(self) -> None:
        outputs = [it.output_path for it in self._items if it.status == "done" and it.output_path]
        if not outputs:
            if os.path.isdir(self._output_dir):
                outputs = [
                    os.path.join(self._output_dir, f)
                    for f in os.listdir(self._output_dir)
                    if os.path.isfile(os.path.join(self._output_dir, f))
                ]
        if not outputs:
            QtWidgets.QMessageBox.information(
                self, "Export ZIP", "No processed videos to export yet."
            )
            return

        default_zip = os.path.join(self._output_dir, f"{BRAND_NAME.lower()}_export.zip")
        zip_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export as ZIP", default_zip, "ZIP archive (*.zip)"
        )
        if not zip_path:
            return

        try:
            export_zip(outputs, zip_path)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Export ZIP", f"Failed:\n{exc}")
            return
        self.status_label.setText(f"Exported ZIP: {zip_path}")
        QtWidgets.QMessageBox.information(self, "Export ZIP", f"Exported:\n{zip_path}")

    def _open_output_dir(self) -> None:
        path = self.out_dir_edit.text().strip() or self._output_dir
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
