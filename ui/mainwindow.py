"""Main application window — three-pane layout with sidebar, grid, and props."""
from __future__ import annotations

import queue
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QToolBar,
    QWidget,
)

from core.db.catalog import backup, integrity_check, open_catalog, restore_latest_backup
from core.logger import get_logger
from core.paths import catalog_path as default_catalog_path
from core.scanner import mark_missing, scan_folder
from core.worker import JobWorker
from ui.sidebar import SidebarWidget
from ui.thumbgrid import ThumbnailGrid
from ui.propspanel import PropertiesPanel

log = get_logger("picurate.ui")


# ── Scan thread ───────────────────────────────────────────────────────────────

class _ScanSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(dict)


class _ScanThread(QThread):
    def __init__(self, folder: Path, catalog_path: Path, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.catalog_path = catalog_path
        self.signals = _ScanSignals()

    def run(self) -> None:
        mark_missing(self.folder, self.catalog_path)
        stats = scan_folder(
            self.folder,
            self.catalog_path,
            progress_cb=lambda done, total: self.signals.progress.emit(done, total),
        )
        self.signals.finished.emit(stats)


# ── Main window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, catalog_path: Path | None = None):
        super().__init__()
        self._catalog_path = catalog_path or default_catalog_path()
        self._scan_thread: _ScanThread | None = None
        self._worker: JobWorker | None = None
        self._result_queue: queue.Queue = queue.Queue()
        self._active_filter: dict = {}
        self._loupe = None  # keep a reference so GC doesn't collect it

        self._setup_catalog()
        self._build_ui()
        self._start_worker()
        self._start_result_poll()

    # ------------------------------------------------------------------
    def _setup_catalog(self) -> None:
        if self._catalog_path.exists() and not integrity_check(self._catalog_path):
            log.warning("Catalog integrity check failed — attempting restore")
            restore_latest_backup(self._catalog_path)
        open_catalog(self._catalog_path)

    def _start_worker(self) -> None:
        self._worker = JobWorker(
            self._catalog_path,
            result_queue=self._result_queue,
        )
        self._worker.start()

    def _start_result_poll(self) -> None:
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(400)
        self._poll_timer.timeout.connect(self._drain_result_queue)
        self._poll_timer.start()

    def _drain_result_queue(self) -> None:
        try:
            while True:
                event = self._result_queue.get_nowait()
                if event[0] == "thumbnail":
                    _, photo_id, thumb_path = event
                    self._grid.update_thumbnail(photo_id, thumb_path)
        except queue.Empty:
            pass

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle("Picurate")
        self.resize(1280, 820)

        # ── Toolbar ──────────────────────────────────────────────────
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_act = QAction("Open Folder…", self)
        open_act.setShortcut("Ctrl+O")
        open_act.triggered.connect(self._on_open_folder)
        toolbar.addAction(open_act)

        rescan_act = QAction("Rescan", self)
        rescan_act.setShortcut("F5")
        rescan_act.triggered.connect(self._on_rescan)
        toolbar.addAction(rescan_act)

        toolbar.addSeparator()

        sidebar_act = QAction("Sidebar", self)
        sidebar_act.setCheckable(True)
        sidebar_act.setChecked(True)
        sidebar_act.triggered.connect(self._toggle_sidebar)
        toolbar.addAction(sidebar_act)
        self._sidebar_action = sidebar_act

        props_act = QAction("Properties", self)
        props_act.setCheckable(True)
        props_act.setChecked(True)
        props_act.triggered.connect(self._toggle_props)
        toolbar.addAction(props_act)
        self._props_action = props_act

        # ── Central three-pane splitter ───────────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(self._splitter)

        self._sidebar = SidebarWidget(self._catalog_path)
        self._sidebar.filter_changed.connect(self._on_filter_changed)
        self._splitter.addWidget(self._sidebar)

        self._grid = ThumbnailGrid(self._catalog_path)
        self._grid.photo_activated.connect(self._on_photo_activated)
        self._grid.photo_selected.connect(self._on_photo_selected)
        self._splitter.addWidget(self._grid)

        self._props = PropertiesPanel()
        self._splitter.addWidget(self._props)

        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        self._splitter.setSizes([200, 860, 220])

        # ── Status bar ───────────────────────────────────────────────
        self._status_label = QLabel("Ready")
        self._progress = QProgressBar()
        self._progress.setMaximumWidth(200)
        self._progress.setVisible(False)

        status_bar = QStatusBar()
        status_bar.addWidget(self._status_label)
        status_bar.addPermanentWidget(self._progress)
        self.setStatusBar(status_bar)

        # Initial load
        self._grid.load_photos({})

    # ------------------------------------------------------------------
    def _toggle_sidebar(self, checked: bool) -> None:
        self._sidebar.setVisible(checked)

    def _toggle_props(self, checked: bool) -> None:
        self._props.setVisible(checked)

    # ------------------------------------------------------------------
    def _on_filter_changed(self, filt: dict) -> None:
        self._active_filter = filt
        self._grid.load_photos(filt)

    def _on_photo_selected(self, photo_id: int) -> None:
        from core.db.catalog import get_connection
        from core.query import get_photo_by_id
        conn = get_connection(self._catalog_path)
        row = get_photo_by_id(conn, photo_id)
        if row:
            self._props.show_photo(row)

    def _on_photo_activated(self, photo_id: int) -> None:
        from ui.loupe import LoupeView
        self._loupe = LoupeView(
            photo_id,
            self._catalog_path,
            filter_ctx=self._active_filter,
            parent=self,
        )
        self._loupe.show()

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
            self._status_label.setText("No watch folders — use Open Folder first.")
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

        self._scan_thread = _ScanThread(folder, self._catalog_path, self)
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
            f"Done — {stats.get('inserted', 0)} new, "
            f"{stats.get('updated', 0)} updated, "
            f"{stats.get('relinked', 0)} relinked, "
            f"{stats.get('errors', 0)} errors"
        )
        self._worker.wake()
        self._sidebar.refresh()
        self._grid.load_photos(self._active_filter)

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        if self._worker:
            self._worker.stop()
            self._worker.join(timeout=3)
        super().closeEvent(event)
