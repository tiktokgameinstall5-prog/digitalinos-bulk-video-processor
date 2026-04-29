"""Smoke tests for the PyQt5 UI: launch the main window offscreen, verify
key widgets + button-state transitions wire up correctly.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt5")

from PyQt5 import QtWidgets  # noqa: E402

from app.processing.queue_manager import QueueItem  # noqa: E402
from app.ui.main_window import HELP_TEXT, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_main_window_launches(qapp) -> None:
    w = MainWindow()
    assert "Digitalinos" in w.windowTitle()
    # Help content should be loaded.
    assert "How to use" in HELP_TEXT
    w.close()


def test_process_button_disabled_without_items(qapp) -> None:
    w = MainWindow()
    # No items loaded yet.
    assert not w.process_btn.isEnabled()
    # Cancel is always disabled when not processing.
    assert not w.cancel_btn.isEnabled()
    # Export ZIP disabled until something completes.
    assert not w.zip_btn.isEnabled()
    w.close()


def test_process_button_enables_with_items(qapp) -> None:
    w = MainWindow()
    # Simulate a loaded item without touching real ffprobe.
    w._items.append(QueueItem(input_path="/tmp/fake.mp4"))
    w._item_widgets.append(object())  # placeholder; only the count matters
    w._refresh_button_states()
    # Process should enable only when ffmpeg is on PATH; guard accordingly.
    from app.processing.ffmpeg_handler import ffmpeg_available
    assert w.process_btn.isEnabled() == bool(ffmpeg_available())
    assert not w.zip_btn.isEnabled()  # no successes yet
    w.close()


def test_export_button_enables_after_completion(qapp) -> None:
    w = MainWindow()
    w._items.append(QueueItem(input_path="/tmp/fake.mp4", status="done",
                              output_path="/tmp/out.mp4"))
    w._item_widgets.append(object())
    w._any_done = True
    w._refresh_button_states()
    assert w.zip_btn.isEnabled()
    w.close()


def test_quality_template_dropdown_populated(qapp) -> None:
    w = MainWindow()
    items = [w.qual_template.itemText(i) for i in range(w.qual_template.count())]
    assert "CapCut Ultra HD (1440p)" in items
    assert "4K Crisp (2160p)" in items
    w.close()


def test_help_tab_present(qapp) -> None:
    w = MainWindow()
    # Find the tab widget under the right panel.
    tabs = w.findChildren(QtWidgets.QTabWidget)
    assert tabs
    tab_titles = [tabs[0].tabText(i) for i in range(tabs[0].count())]
    assert "Help" in tab_titles
    w.close()
