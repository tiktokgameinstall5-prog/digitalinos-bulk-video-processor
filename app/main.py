"""Entry point for Video Batch Pro."""

from __future__ import annotations

import sys

from PyQt5 import QtWidgets


def main() -> int:
    # Importing lazily keeps the module importable in headless test environments.
    from .ui.main_window import MainWindow

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Video Batch Pro")
    app.setOrganizationName("Video Batch Pro")

    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
