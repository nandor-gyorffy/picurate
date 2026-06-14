#!/usr/bin/env python3
"""Picurate entry point."""
import sys
import os
import traceback
from pathlib import Path

# High-DPI scaling — must be set before QApplication is created.
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from core.db.catalog import integrity_check, open_catalog, restore_latest_backup
from core.logger import get_logger
from core.paths import catalog_path

# Resolve the app's base directory whether running from source or PyInstaller bundle
_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))

log = get_logger("picurate")


def _show_crash_dialog(exc: Exception) -> None:
    msg = QMessageBox()
    msg.setWindowTitle("Picurate — Startup Error")
    msg.setIcon(QMessageBox.Icon.Critical)
    msg.setText("Picurate encountered an error during startup.")
    msg.setDetailedText(traceback.format_exc())
    msg.exec()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Picurate")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("Picurate")

    _icon = _BASE / "assets" / "icon" / "picurate.png"
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))

    try:
        cp = catalog_path()
        if cp.exists() and not integrity_check(cp):
            log.warning("Catalog corrupt on startup — restoring backup")
            restore_latest_backup(cp)
        open_catalog(cp)
    except Exception as exc:
        log.error("Catalog setup failed: %s", exc)
        _show_crash_dialog(exc)
        return 1

    # Restore saved font size before opening the window
    try:
        from core import settings as _s
        saved_font_size = _s.get("font_size", cp)
        if saved_font_size:
            from PySide6.QtGui import QFont
            f = app.font()
            f.setPointSize(int(saved_font_size))
            app.setFont(f)
    except Exception:
        pass

    try:
        from ui.mainwindow import MainWindow
        win = MainWindow(cp)
        win.show()
    except Exception as exc:
        log.error("UI startup failed: %s", exc)
        _show_crash_dialog(exc)
        return 1

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
