"""License activation dialog.

Shown from Help → License or via the "Activate" pill in the title bar.
Supports paste-key activation, status read-out, release-device, and a
direct link to the dashboard for buying / managing keys.
"""

from __future__ import annotations

import time
from datetime import datetime

from PyQt5 import QtCore, QtGui, QtWidgets

from ..licensing import client as license_client


def _format_expiry(ts: float) -> str:
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(ts).strftime("%d %b %Y, %H:%M")
    except (OSError, OverflowError, ValueError):
        return "—"


class LicenseDialog(QtWidgets.QDialog):
    """Dialog for activating, viewing, or releasing a license."""

    state_changed = QtCore.pyqtSignal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Digitalinos — License")
        self.setMinimumWidth(560)
        self.setModal(True)

        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = QtWidgets.QLabel("License")
        title.setObjectName("h1")
        f = title.font()
        f.setPointSize(18)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        # Status grid
        grid = QtWidgets.QFormLayout()
        grid.setLabelAlignment(QtCore.Qt.AlignRight)
        grid.setHorizontalSpacing(16)
        self.plan_value = QtWidgets.QLabel("—")
        self.expiry_value = QtWidgets.QLabel("—")
        self.last_verify_value = QtWidgets.QLabel("—")
        self.device_value = QtWidgets.QLabel("—")
        grid.addRow("<b>Plan</b>", self.plan_value)
        grid.addRow("<b>Expires</b>", self.expiry_value)
        grid.addRow("<b>Last verified</b>", self.last_verify_value)
        grid.addRow("<b>Device ID</b>", self.device_value)
        root.addLayout(grid)

        # Trial badge
        self.trial_label = QtWidgets.QLabel()
        self.trial_label.setStyleSheet(
            "padding: 6px 10px; background: rgba(27,181,196,0.12); "
            "border-radius: 6px; color: #1BB5C4;"
        )
        root.addWidget(self.trial_label)

        # Key input
        key_label = QtWidgets.QLabel("License key")
        key_label.setStyleSheet("margin-top: 6px;")
        root.addWidget(key_label)

        key_row = QtWidgets.QHBoxLayout()
        self.key_input = QtWidgets.QLineEdit()
        self.key_input.setPlaceholderText("DGIT-XXXX-XXXX-XXXX-XXXX")
        self.key_input.textChanged.connect(self._format_input)
        font = self.key_input.font()
        font.setFamily("Consolas, monospace")
        font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.0)
        self.key_input.setFont(font)
        key_row.addWidget(self.key_input, stretch=1)

        self.activate_btn = QtWidgets.QPushButton("Activate")
        self.activate_btn.setDefault(True)
        self.activate_btn.clicked.connect(self._on_activate)
        key_row.addWidget(self.activate_btn)
        root.addLayout(key_row)

        # Buy / manage / release links
        link_row = QtWidgets.QHBoxLayout()
        buy = QtWidgets.QLabel(
            '<a style="color:#1BB5C4" href="https://digitalinos-web.vercel.app/pricing">Buy a license</a> &nbsp; • &nbsp; '
            '<a style="color:#1BB5C4" href="https://digitalinos-web.vercel.app/dashboard">My dashboard</a>'
        )
        buy.setOpenExternalLinks(True)
        link_row.addWidget(buy)
        link_row.addStretch(1)
        self.release_btn = QtWidgets.QPushButton("Release this device")
        self.release_btn.clicked.connect(self._on_release)
        link_row.addWidget(self.release_btn)
        root.addLayout(link_row)

        # Close
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------
    def _format_input(self, text: str) -> None:
        """Auto-uppercase + auto-insert dashes after every 4 hex chars."""
        cleaned = "".join(c for c in text.upper() if c.isalnum())
        # Re-prefix the hard "DGIT-" if user typed anything else first.
        if cleaned and not cleaned.startswith("DGIT"):
            cleaned = "DGIT" + cleaned
        groups = [cleaned[:4]]
        rest = cleaned[4:]
        while rest:
            groups.append(rest[:4])
            rest = rest[4:]
        formatted = "-".join(groups)
        if formatted != text:
            cursor = self.key_input.cursorPosition()
            self.key_input.blockSignals(True)
            self.key_input.setText(formatted)
            self.key_input.setCursorPosition(min(len(formatted), cursor + 1))
            self.key_input.blockSignals(False)

    def _refresh(self) -> None:
        state = license_client.load_state()
        if state.has_license and state.is_active():
            self.status_label.setText(
                f"<span style='color:#22c55e;'>● License active</span> — "
                f"unlimited renders on this device."
            )
            self.activate_btn.setText("Re-activate")
            self.release_btn.setEnabled(True)
        elif state.has_license:
            self.status_label.setText(
                "<span style='color:#ef4444;'>● License expired</span> — "
                "renew it from the dashboard, then re-activate."
            )
            self.activate_btn.setText("Re-activate")
            self.release_btn.setEnabled(True)
        else:
            self.status_label.setText(
                "No license active — you're on the free trial."
            )
            self.activate_btn.setText("Activate")
            self.release_btn.setEnabled(False)

        self.plan_value.setText(state.plan or "—")
        self.expiry_value.setText(_format_expiry(state.expires_at))
        self.last_verify_value.setText(_format_expiry(state.last_verified_at))
        self.device_value.setText(state.device_id or "—")

        if state.has_license:
            self.trial_label.setVisible(False)
        else:
            remaining = state.trial_remaining
            self.trial_label.setVisible(True)
            if remaining > 0:
                self.trial_label.setText(
                    f"Free trial: {remaining} of {license_client.TRIAL_LIMIT} videos remaining"
                )
            else:
                self.trial_label.setText(
                    "Free trial used up — paste a key above to keep going."
                )
                self.trial_label.setStyleSheet(
                    "padding: 6px 10px; background: rgba(239,68,68,0.12); "
                    "border-radius: 6px; color: #ef4444;"
                )

        self.key_input.setText(state.license_key or "")

    def _on_activate(self) -> None:
        key = self.key_input.text().strip()
        if not key:
            QtWidgets.QMessageBox.information(
                self, "License", "Paste your license key first."
            )
            return
        self.activate_btn.setEnabled(False)
        self.activate_btn.setText("Activating…")
        QtWidgets.QApplication.processEvents()
        try:
            license_client.activate(key)
        except license_client.LicenseError as exc:
            QtWidgets.QMessageBox.critical(self, "Activation failed", str(exc))
        else:
            QtWidgets.QMessageBox.information(
                self, "License", "Activation successful — unlimited renders unlocked."
            )
            self.state_changed.emit()
        finally:
            self.activate_btn.setEnabled(True)
            self._refresh()

    def _on_release(self) -> None:
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Release device",
            "Release this device from your license?\n\n"
            "You'll be back on the free trial here. You can re-activate "
            "the same key on another device afterwards.",
        )
        if confirm != QtWidgets.QMessageBox.Yes:
            return
        try:
            license_client.release()
        except license_client.LicenseError as exc:
            QtWidgets.QMessageBox.critical(self, "Release failed", str(exc))
        finally:
            self._refresh()
            self.state_changed.emit()
