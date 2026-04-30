"""Tests for :class:`LicenseDialog` — focused on ``_format_input``.

These exercise the character-by-character typing path that previously
produced phantom characters (e.g. typing ``5709...`` into an empty
field produced ``DGIT-ITDG-5G7I-09TD-...``). The fix anchors the
cursor to the end after every reformat and only auto-prepends
``DGIT`` once the user has typed enough body chars to make their
intent unambiguous.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt5")

from PyQt5 import QtCore, QtTest, QtWidgets  # noqa: E402

from app.ui.license_dialog import LicenseDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def _type(widget: QtWidgets.QLineEdit, text: str) -> None:
    """Type *text* into *widget* one key at a time, letting each
    ``textChanged`` signal run the formatter before the next key."""
    for ch in text:
        QtTest.QTest.keyClicks(widget, ch)


def test_paste_full_key_is_unchanged(qapp) -> None:
    d = LicenseDialog()
    d.key_input.setText("DGIT-5709-2F78-E963-ABCF")
    # Formatter runs on the setText signal too.
    assert d.key_input.text() == "DGIT-5709-2F78-E963-ABCF"
    d.close()


def test_paste_key_without_prefix_gets_prefix(qapp) -> None:
    d = LicenseDialog()
    d.key_input.setText("57092F78E963ABCF")
    assert d.key_input.text() == "DGIT-5709-2F78-E963-ABCF"
    d.close()


def test_paste_lowercase_key_is_uppercased(qapp) -> None:
    d = LicenseDialog()
    d.key_input.setText("dgit-c247-6077-88f6-ab02")
    assert d.key_input.text() == "DGIT-C247-6077-88F6-AB02"
    d.close()


def test_paste_key_with_spaces_strips_them(qapp) -> None:
    d = LicenseDialog()
    d.key_input.setText("  DGIT C247 6077 88F6 AB02  ")
    assert d.key_input.text() == "DGIT-C247-6077-88F6-AB02"
    d.close()


def test_type_full_key_character_by_character(qapp) -> None:
    """Regression: typing ``DGIT-5709-2F78-E963-ABCF`` one key at a time
    previously produced a garbled output. Now it produces the correct
    key."""
    d = LicenseDialog()
    _type(d.key_input, "DGIT-5709-2F78-E963-ABCF")
    assert d.key_input.text() == "DGIT-5709-2F78-E963-ABCF"
    d.close()


def test_type_body_only_gets_prefix_added(qapp) -> None:
    """User forgets the ``DGIT`` prefix and just types the 16-char body.
    Formatter should add the prefix once the input is long enough to
    disambiguate (>= 5 chars)."""
    d = LicenseDialog()
    _type(d.key_input, "57092F78E963ABCF")
    assert d.key_input.text() == "DGIT-5709-2F78-E963-ABCF"
    d.close()


def test_short_typing_does_not_get_phantom_prefix(qapp) -> None:
    """Typing just one or two chars must NOT prepend ``DGIT`` — that
    was the bug that caused ``D`` → ``DGIT-D`` and cascading phantom
    characters on each subsequent keystroke."""
    d = LicenseDialog()
    QtTest.QTest.keyClicks(d.key_input, "D")
    assert d.key_input.text() == "D"
    QtTest.QTest.keyClicks(d.key_input, "G")
    assert d.key_input.text() == "DG"
    QtTest.QTest.keyClicks(d.key_input, "I")
    assert d.key_input.text() == "DGI"
    QtTest.QTest.keyClicks(d.key_input, "T")
    assert d.key_input.text() == "DGIT"
    d.close()


def test_cursor_ends_at_end_after_reformat(qapp) -> None:
    """Cursor must land at the end of the formatted text so the next
    keystroke appends cleanly instead of inserting into the middle of
    a group."""
    d = LicenseDialog()
    d.key_input.setText("DGIT5709")
    assert d.key_input.text() == "DGIT-5709"
    assert d.key_input.cursorPosition() == len("DGIT-5709")
    d.close()


def test_input_caps_at_full_key_length(qapp) -> None:
    """Extra characters past the 20-alnum-char key are discarded."""
    d = LicenseDialog()
    d.key_input.setText("DGIT-5709-2F78-E963-ABCF-EXTRA-JUNK")
    assert d.key_input.text() == "DGIT-5709-2F78-E963-ABCF"
    d.close()


def test_dashes_and_case_are_irrelevant(qapp) -> None:
    """Mixed separators + case still yield a canonical key."""
    d = LicenseDialog()
    d.key_input.setText("dgit_5709.2f78 e963 abcf")
    assert d.key_input.text() == "DGIT-5709-2F78-E963-ABCF"
    d.close()
