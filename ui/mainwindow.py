"""Main application window."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QGridLayout,
)

from core.db.catalog import backup, integrity_check, open_catalog, restore_latest_backup
from core.logger import get_logger
from core.paths import catalog_path as default_catalog_path
from core.scanner import mark_missing, scan_folder
from core.worker import JobWorker

log = get_logger("picurate.ui")


class ScanSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(dict)


class ScanThread(QThread):
    """Runs scan_folder off the main thread."""

    def __init__(self, folder: Path, catalog_path: Path, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.catalog_path = catalog_path
        self.signals = ScanSignals()
        self.stats: dict = {}

    def run(self):
        self.stats = scan_folder(
            self.folder,
            self.catalog_path,
            progress_cb=lambda done, total: self.signals.progress.emit(done, total),
        )
        self.signals.finished.emit(self.stats)


class ThumbnailGrid(QScrollArea):
    """Scrollable grid of photo thumbnails."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._container = QWidget()
        self._layout = QGridLayout(self._container)
        self._layout.setSpacing(4)
        self.setWidget(self._container)
        self.setWidgetResizable(True)
        self._thumb_size = 160

    def load_from_catalog(self, catalog_path: Path) -> None:
        # Clear existing
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        from core.db.catalog import get_connection
        conn = get_connection(catalog_path)
        rows = conn.execute(
            "SELECT thumbnail_path, filename FROM photos WHERE thumbnail_path IS NOT NULL ORDER BY date_taken DESC LIMIT 500"
        ).fetchall()

        cols = max(1, self.width() // (self._thumb_size + 8))
        for idx, row in enumerate(rows):
            label = QLabel()
            label.setFixedSize(self._thumb_size, self._thumb_size)
            label.setAlignment(Qt.AlignCenter)
            label.setToolTip(row["filename"])
            pix = QPixmap(row["thumbnail_path"])
            if not pix.isNull():
                label.setPixmap(pix.scaled(
                    self._thumb_size, self._thumb_size,
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                ))
            else:
                label.setText(row["filename"])
            self._layout.addWidget(label, idx // cols, idx % cols)

    def resizeEvent(self, event):
        super().resizeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, catalog_path: Path | None = None):
        super().__init__()
        self._catalog_path = catalog_path or default_catalog_path()
        self._scan_thread: ScanThread | None = None
        self._worker: JobWorker | None = None
        self._setup_catalog()
        self._build_ui()
        self._start_worker()

    # ------------------------------------------------------------------
    def _setup_catalog(self) -> None:
        if not integrity_check(self._catalog_path):
            log.warning("Catalog integrity check failed — attempting restore")
            restore_latest_backup(self._catalog_path)
        open_catalog(self._catalog_path)

    def _start_worker(self) -> None:
        self._worker = JobWorker(
            self._catalog_path,
            progress_cb=lambda jtype, done, total: self._on_worker_progress(jtype, done, total),
        )
        self._worker.start()

    def _on_worker_progress(self, job_type: str, done: int, total: int) -> None:
        # Called from worker thread — only update status bar text (thread-safe for labels)
        pass

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle("Picurate")
        self.resize(1200, 800)

        # Toolbar
        toolbar = QToolBar("Main")
        self.addToolBar(toolbar)

        open_action = QAction("Open Folder…", self)
        open_action.triggered.connect(self._on_open_folder)
        toolbar.addAction(open_action)

        rescan_action = QAction("Rescan", self)
        rescan_action.triggered.connect(self._on_rescan)
        toolbar.addAction(rescan_action)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        self._grid = ThumbnailGrid()
        layout.addWidget(self._grid)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)

        self._progress = QProgressBar()
        self._progress.setMaximumWidth(200)
        self._progress.setVisible(False)
        self._status.addPermanentWidget(self._progress)

        self._status_label = QLabel("Ready")
        self._status.addWidget(self._status_label)

        # Load any already-indexed photos
        self._refresh_grid()

    # ------------------------------------------------------------------
    def _on_open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Photo Folder")
        if not folder:
            return
        backup(self._catalog_path, "pre_scan")
        self._start_scan(Path(folder))

    def _on_rescan(self) -> None:
        from core import settings
        folders = settings.get_watch_folders(self._catalog_path)
        if not folders:
            self._status_label.setText("No watch folders configured. Use Open Folder first.")
            return
        for f in folders:
            self._start_scan(Path(f))

    def _start_scan(self, folder: Path) -> None:
        if self._scan_thread and self._scan_thread.isRunning():
            self._status_label.setText("Scan already in progress…")
            return
        from core import settings
        settings.add_watch_folder(str(folder), self._catalog_path)

        self._status_label.setText(f"Scanning {folder.name}…")
        self._progress.setValue(0)
        self._progress.setVisible(True)

        self._scan_thread = ScanThread(folder, self._catalog_path)
        self._scan_thread.signals.progress.connect(self._on_scan_progress)
        self._scan_thread.signals.finished.connect(self._on_scan_finished)
        self._scan_thread.start()

    def _on_scan_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setMaximum(total)
            self._progress.setValue(done)

    def _on_scan_finished(self, stats: dict) -> None:
        self._progress.setVisible(False)
        self._status_label.setText(
            f"Scan done — {stats.get('inserted', 0)} new, "
            f"{stats.get('updated', 0)} updated, "
            f"{stats.get('relinked', 0)} relinked, "
            f"{stats.get('errors', 0)} errors"
        )
        self._worker.wake()
        self._refresh_grid()

    def _refresh_grid(self) -> None:
        self._grid.load_from_catalog(self._catalog_path)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self._worker:
            self._worker.stop()
            self._worker.join(timeout=3)
        super().closeEvent(event)
