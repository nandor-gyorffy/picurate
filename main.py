#!/usr/bin/env python3
"""Picurate entry point."""
import sys
import os

# High-DPI scaling — set before QApplication is created.
# QT_AUTO_SCREEN_SCALE_FACTOR is the recommended env-var approach for PySide6 >= 6.5.
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

from PySide6.QtWidgets import QApplication

from core.db.catalog import open_catalog, integrity_check, restore_latest_backup
from core.logger import get_logger
from core.paths import catalog_path

log = get_logger("picurate")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Picurate")
    app.setOrganizationName("Picurate")

    # Startup integrity check
    cp = catalog_path()
    if cp.exists() and not integrity_check(cp):
        log.warning("Catalog corrupt on startup — restoring backup")
        restore_latest_backup(cp)

    # Open/migrate catalog
    open_catalog(cp)

    from ui.mainwindow import MainWindow
    win = MainWindow(cp)
    win.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
