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
    QVBoxLayout,
    QWidget,
)

from core.db.catalog import backup, integrity_check, open_catalog, restore_latest_backup
from core.logger import get_logger
from core.paths import catalog_path as default_catalog_path
from core.scanner import mark_missing, scan_folder
from core.worker import JobWorker
from ui.filterbar import FilterBar
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
        self._sidebar_filter: dict = {}
        self._filterbar_filter: dict = {}
        self._loupe = None
        self._cull_view = None
        self._in_cull_mode = False

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
                elif event[0] == "geocode_done":
                    stats = event[1]
                    self._progress.setVisible(False)
                    self._status_label.setText(
                        f"Geocode done — {stats['geocoded']} photos placed, "
                        f"{stats['errors']} errors."
                    )
                    self._sidebar.refresh()
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

        toolbar.addSeparator()

        cull_act = QAction("Cull Mode", self)
        cull_act.setCheckable(True)
        cull_act.setChecked(False)
        cull_act.triggered.connect(self._toggle_cull_mode)
        toolbar.addAction(cull_act)
        self._cull_action = cull_act

        toolbar.addSeparator()

        export_act = QAction("Export…", self)
        export_act.setShortcut("Ctrl+E")
        export_act.triggered.connect(self._on_export)
        toolbar.addAction(export_act)

        import_act = QAction("Import…", self)
        import_act.setShortcut("Ctrl+I")
        import_act.triggered.connect(self._on_import)
        toolbar.addAction(import_act)

        toolbar.addSeparator()

        geocode_act = QAction("Geocode GPS", self)
        geocode_act.setToolTip("Reverse-geocode all GPS-tagged photos (offline)")
        geocode_act.triggered.connect(self._on_geocode)
        toolbar.addAction(geocode_act)

        trips_act = QAction("Group Trips", self)
        trips_act.setToolTip("Auto-group photos into trips by date gap")
        trips_act.triggered.connect(self._on_group_trips)
        toolbar.addAction(trips_act)

        faces_act = QAction("Detect Faces", self)
        faces_act.setToolTip("Enqueue face detection for all photos")
        faces_act.triggered.connect(self._on_detect_faces)
        toolbar.addAction(faces_act)

        cluster_act = QAction("Cluster Faces", self)
        cluster_act.setToolTip("Group similar faces into people")
        cluster_act.triggered.connect(self._on_cluster_faces)
        toolbar.addAction(cluster_act)

        tag_act = QAction("Tag Topics", self)
        tag_act.setToolTip("Enqueue CLIP topic tagging for all photos")
        tag_act.triggered.connect(self._on_tag_topics)
        toolbar.addAction(tag_act)

        quality_act = QAction("Score Quality", self)
        quality_act.setToolTip("Enqueue quality scoring for all photos")
        quality_act.triggered.connect(self._on_score_quality)
        toolbar.addAction(quality_act)

        dupes_act = QAction("Find Near-Dupes", self)
        dupes_act.setToolTip("Detect near-duplicate photos using perceptual hashing")
        dupes_act.triggered.connect(self._on_find_near_dupes)
        toolbar.addAction(dupes_act)

        writeback_act = QAction("Write Metadata", self)
        writeback_act.setToolTip("Mirror ratings/captions/keywords back to file XMP (requires exiftool)")
        writeback_act.triggered.connect(self._on_write_metadata)
        toolbar.addAction(writeback_act)

        # ── Central three-pane splitter ───────────────────────────────
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(self._splitter)

        self._sidebar = SidebarWidget(self._catalog_path)
        self._sidebar.filter_changed.connect(self._on_sidebar_filter_changed)
        self._splitter.addWidget(self._sidebar)

        # Center pane: FilterBar + ThumbnailGrid stacked
        self._center = QWidget()
        center_layout = QVBoxLayout(self._center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self._filterbar = FilterBar()
        self._filterbar.filter_changed.connect(self._on_filterbar_filter_changed)
        center_layout.addWidget(self._filterbar)

        self._grid = ThumbnailGrid(self._catalog_path)
        self._grid.photo_activated.connect(self._on_photo_activated)
        self._grid.photo_selected.connect(self._on_photo_selected)
        center_layout.addWidget(self._grid)

        self._splitter.addWidget(self._center)

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

    def _toggle_cull_mode(self, checked: bool) -> None:
        from ui.cullview import CullView
        if checked:
            self._in_cull_mode = True
            self._filterbar.setVisible(False)
            self._grid.setVisible(False)

            active = self._merged_filter()
            self._cull_view = CullView(self._catalog_path, active, parent=self._center)
            self._center.layout().addWidget(self._cull_view)
            self._cull_view.show()
            self._cull_view.exit_requested.connect(lambda: self._cull_action.setChecked(False))
            self._cull_view.exit_requested.connect(lambda: self._toggle_cull_mode(False))
            self._cull_view.photo_changed.connect(self._on_cull_photo_changed)
            self._cull_view.collection_changed.connect(self._sidebar.refresh)
        else:
            self._in_cull_mode = False
            if self._cull_view is not None:
                self._cull_view.hide()
                self._center.layout().removeWidget(self._cull_view)
                self._cull_view.deleteLater()
                self._cull_view = None
            self._filterbar.setVisible(True)
            self._grid.setVisible(True)

    # ------------------------------------------------------------------
    def _merged_filter(self) -> dict:
        return {**self._sidebar_filter, **self._filterbar_filter}

    def _on_sidebar_filter_changed(self, filt: dict) -> None:
        self._sidebar_filter = filt
        self._apply_filter()

    def _on_filterbar_filter_changed(self, filt: dict) -> None:
        self._filterbar_filter = filt
        self._apply_filter()

    def _apply_filter(self) -> None:
        merged = self._merged_filter()
        if self._in_cull_mode and self._cull_view is not None:
            self._cull_view.set_filter(merged)
        else:
            self._grid.load_photos(merged)

    def _on_cull_photo_changed(self, photo_id: int) -> None:
        from core.db.catalog import get_connection
        from core.query import get_photo_by_id
        conn = get_connection(self._catalog_path)
        row = get_photo_by_id(conn, photo_id)
        if row:
            self._props.show_photo(row)

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
            filter_ctx=self._merged_filter(),
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
        self._grid.load_photos(self._merged_filter())

    def _on_export(self) -> None:
        from ui.exportdialog import ExportDialog
        dlg = ExportDialog(self._catalog_path, parent=self)
        dlg.exec()

    def _on_import(self) -> None:
        from ui.importdialog import ImportDialog
        dlg = ImportDialog(self._catalog_path, parent=self)
        dlg.import_done.connect(self._sidebar.refresh)
        dlg.import_done.connect(lambda: self._grid.load_photos(self._merged_filter()))
        dlg.exec()

    def _on_geocode(self) -> None:
        from core.places import geocode_photos
        self._status_label.setText("Geocoding GPS photos…")
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)

        import threading
        def run():
            stats = geocode_photos(
                self._catalog_path,
                progress_cb=lambda d, t: None,
            )
            self._result_queue.put(("geocode_done", stats))
        threading.Thread(target=run, daemon=True).start()

    def _on_detect_faces(self) -> None:
        from core.faces import detect_faces_batch
        stats = detect_faces_batch(self._catalog_path)
        self._status_label.setText(f"Face detection: {stats['enqueued']} jobs enqueued.")
        self._worker.wake()

    def _on_cluster_faces(self) -> None:
        from core.clustering import cluster_unassigned_faces
        stats = cluster_unassigned_faces(self._catalog_path)
        self._status_label.setText(
            f"Clustering: {stats['people_created']} people created, "
            f"{stats['faces_assigned']} faces assigned."
        )
        self._sidebar.refresh()

    def _on_tag_topics(self) -> None:
        from core.topics import tag_photos_batch
        stats = tag_photos_batch(self._catalog_path)
        self._status_label.setText(f"Topics: {stats['enqueued']} jobs enqueued.")
        self._worker.wake()

    def _on_score_quality(self) -> None:
        from core.quality import compute_quality_batch
        from core.duplicates import compute_phash_batch
        qs = compute_quality_batch(self._catalog_path)
        ps = compute_phash_batch(self._catalog_path)
        self._status_label.setText(
            f"Quality: {qs['enqueued']} jobs, pHash: {ps['enqueued']} jobs enqueued."
        )
        self._worker.wake()

    def _on_find_near_dupes(self) -> None:
        from core.duplicates import find_duplicate_groups
        groups = find_duplicate_groups(self._catalog_path)
        self._status_label.setText(
            f"Near-duplicates: {len(groups)} group{'s' if len(groups) != 1 else ''} found."
        )

    def _on_write_metadata(self) -> None:
        from core.writeback import write_back_batch, exiftool_available
        if not exiftool_available():
            self._status_label.setText("exiftool not found — metadata write-back unavailable.")
            return
        stats = write_back_batch(self._catalog_path)
        self._status_label.setText(
            f"Write-back: {stats['written']} written, {stats['errors']} errors."
        )

    def _on_group_trips(self) -> None:
        from core.places import auto_group_trips
        stats = auto_group_trips(self._catalog_path)
        self._status_label.setText(
            f"Trips: {stats['trips_created']} created, "
            f"{stats['photos_assigned']} photos assigned."
        )
        self._sidebar.refresh()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        if self._worker:
            self._worker.stop()
            self._worker.join(timeout=3)
        super().closeEvent(event)
