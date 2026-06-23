"""Main application window — three-pane layout with sidebar, grid, and props."""
from __future__ import annotations

import queue
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QObject
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
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
        self._job_error_count = 0

        self._setup_catalog()
        self._create_actions()
        self._build_ui()
        self._build_menubar()
        self._start_worker()
        self._start_result_poll()
        self._install_exception_hook()

    # ------------------------------------------------------------------
    def _setup_catalog(self) -> None:
        if self._catalog_path.exists() and not integrity_check(self._catalog_path):
            log.warning("Catalog integrity check failed — attempting restore")
            restore_latest_backup(self._catalog_path)
        open_catalog(self._catalog_path)

    def _install_exception_hook(self) -> None:
        """Show a dialog for uncaught exceptions instead of silently crashing."""
        _orig = sys.excepthook

        def _hook(exc_type, exc_value, exc_tb):
            try:
                import traceback
                msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                dlg = QMessageBox(self)
                dlg.setWindowTitle("Unexpected Error")
                dlg.setIcon(QMessageBox.Icon.Critical)
                dlg.setText(f"An unexpected error occurred:\n\n{exc_value}")
                dlg.setDetailedText(msg)
                dlg.exec()
            except Exception:
                pass
            _orig(exc_type, exc_value, exc_tb)

        sys.excepthook = _hook

    def _create_actions(self) -> None:
        """Create all QActions (shared between menu bar and toolbar)."""
        def _act(label, shortcut=None, checkable=False, checked=False, tip=None):
            a = QAction(label, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if checkable:
                a.setCheckable(True)
                a.setChecked(checked)
            if tip:
                a.setToolTip(tip)
            return a

        # File
        self._act_open        = _act("Open Folder…",    "Ctrl+O")
        self._act_rescan      = _act("Rescan",           "F5")
        self._act_settings    = _act("Settings…",        "Ctrl+,")
        self._act_quit        = _act("Quit",             "Ctrl+Q")
        # View (panel toggles)
        self._act_sidebar     = _act("Sidebar",          checkable=True, checked=True)
        self._act_props       = _act("Properties Panel", checkable=True, checked=True)
        self._act_filterbar   = _act("Filter Bar",       "Ctrl+F", checkable=True, checked=True)
        self._act_cull        = _act("Cull Mode",        "Ctrl+K", checkable=True)
        self._act_group_sim   = _act("Group Similar…")
        # Export / Import
        self._act_export      = _act("Export…",          "Ctrl+E")
        self._act_import      = _act("Import…",          "Ctrl+I")
        # Faces
        self._act_detect      = _act("Detect Faces",     tip="Enqueue face detection for new photos")
        self._act_redetect    = _act("Re-detect Faces",  tip="Force re-detection on photos with only tiny faces")
        self._act_cluster     = _act("Cluster Faces",    tip="Group similar faces into people")
        self._act_recluster   = _act("Re-cluster Faces", tip="Reset auto-named persons and re-cluster from scratch")
        self._act_unassigned  = _act("Unassigned Faces…",tip="Review and assign faces not yet linked to a person")
        self._act_face_gallery = _act("People Gallery…", tip="Browse and manage all recognized people")
        self._act_writeback   = _act("Write Metadata",   tip="Mirror ratings/captions/keywords to XMP (requires exiftool)")
        # Places
        self._act_geocode     = _act("Geocode GPS",      tip="Reverse-geocode all GPS-tagged photos (offline)")
        self._act_trips       = _act("Group Trips",      tip="Auto-group photos into trips by date gap")
        self._act_merge_pl    = _act("Merge Nearby Places", tip="Merge place records within 500 m of each other")
        self._act_map         = _act("Places Map…",      tip="Show all GPS-tagged photos on an interactive map")
        # Library
        self._act_tag         = _act("Tag Topics",       tip="Enqueue CLIP topic tagging for all photos")
        self._act_quality     = _act("Score Quality",    tip="Enqueue quality scoring for all photos")
        self._act_dupes       = _act("Find Near-Dupes",  tip="Detect near-duplicate photos using perceptual hashing")
        self._act_download_clip = _act("Download CLIP Models…", tip="Instructions for downloading CLIP ONNX models")

        # Connect signals
        self._act_open.triggered.connect(self._on_open_folder)
        self._act_rescan.triggered.connect(self._on_rescan)
        self._act_settings.triggered.connect(self._on_settings)
        self._act_quit.triggered.connect(QApplication.quit)
        self._act_sidebar.triggered.connect(self._toggle_sidebar)
        self._act_props.triggered.connect(self._toggle_props)
        self._act_filterbar.triggered.connect(self._toggle_filterbar)
        self._act_cull.triggered.connect(self._toggle_cull_mode)
        self._act_group_sim.triggered.connect(self._on_group_similar)
        self._act_export.triggered.connect(self._on_export)
        self._act_import.triggered.connect(self._on_import)
        self._act_detect.triggered.connect(self._on_detect_faces)
        self._act_redetect.triggered.connect(self._on_redetect_faces)
        self._act_cluster.triggered.connect(self._on_cluster_faces)
        self._act_recluster.triggered.connect(self._on_recluster_faces)
        self._act_unassigned.triggered.connect(self._on_unassigned_faces)
        self._act_face_gallery.triggered.connect(self._on_face_gallery)
        self._act_writeback.triggered.connect(self._on_write_metadata)
        self._act_geocode.triggered.connect(self._on_geocode)
        self._act_trips.triggered.connect(self._on_group_trips)
        self._act_merge_pl.triggered.connect(self._on_merge_nearby_places)
        self._act_map.triggered.connect(self._on_show_map)
        self._act_tag.triggered.connect(self._on_tag_topics)
        self._act_quality.triggered.connect(self._on_score_quality)
        self._act_dupes.triggered.connect(self._on_find_near_dupes)
        self._act_download_clip.triggered.connect(self._on_download_clip)

    def _build_menubar(self) -> None:
        mb = self.menuBar()

        # ── File ──────────────────────────────────────────────────────
        m = mb.addMenu("&File")
        m.addAction(self._act_open)
        m.addAction(self._act_rescan)
        m.addSeparator()
        m.addAction(self._act_export)
        m.addAction(self._act_import)
        m.addSeparator()
        m.addAction(self._act_settings)
        m.addSeparator()
        m.addAction(self._act_quit)

        # ── View ──────────────────────────────────────────────────────
        m = mb.addMenu("&View")
        m.addAction(self._act_sidebar)
        m.addAction(self._act_props)
        m.addAction(self._act_filterbar)
        m.addSeparator()
        m.addAction(self._act_cull)
        m.addSeparator()
        m.addAction(self._act_group_sim)

        # ── Faces ─────────────────────────────────────────────────────
        m = mb.addMenu("F&aces")
        m.addAction(self._act_detect)
        m.addAction(self._act_redetect)
        m.addSeparator()
        m.addAction(self._act_cluster)
        m.addAction(self._act_recluster)
        m.addSeparator()
        m.addAction(self._act_unassigned)
        m.addAction(self._act_face_gallery)
        m.addSeparator()
        m.addAction(self._act_writeback)

        # ── Places ────────────────────────────────────────────────────
        m = mb.addMenu("&Places")
        m.addAction(self._act_map)
        m.addSeparator()
        m.addAction(self._act_geocode)
        m.addAction(self._act_trips)
        m.addAction(self._act_merge_pl)

        # ── Library ───────────────────────────────────────────────────
        m = mb.addMenu("&Library")
        m.addAction(self._act_tag)
        m.addAction(self._act_quality)
        m.addAction(self._act_dupes)
        m.addSeparator()
        m.addAction(self._act_download_clip)

    # ------------------------------------------------------------------
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
                elif event[0] == "job_error":
                    self._job_error_count += 1
                    self._error_label.setText(f"⚠ {self._job_error_count} error{'s' if self._job_error_count != 1 else ''}")
                    self._error_label.setVisible(True)
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

        # ── Slim Toolbar ──────────────────────────────────────────────
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction(self._act_open)
        toolbar.addAction(self._act_rescan)
        toolbar.addSeparator()
        toolbar.addAction(self._act_sidebar)
        toolbar.addAction(self._act_props)
        toolbar.addAction(self._act_filterbar)
        toolbar.addSeparator()
        toolbar.addAction(self._act_cull)   # Cull Mode in toolbar (user request)
        toolbar.addSeparator()
        toolbar.addAction(self._act_settings)

        # Keep reference for toggle_cull_mode sync
        self._cull_action = self._act_cull
        self._filterbar_action = self._act_filterbar

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
        self._grid.rating_changed.connect(self._on_grid_rating_changed)
        self._grid.flag_changed.connect(self._on_grid_flag_changed)
        center_layout.addWidget(self._grid)

        self._splitter.addWidget(self._center)

        self._props = PropertiesPanel(self._catalog_path)
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
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #cc4400; font-weight: bold;")
        self._error_label.setVisible(False)

        status_bar = QStatusBar()
        status_bar.addWidget(self._status_label)
        status_bar.addPermanentWidget(self._error_label)
        status_bar.addPermanentWidget(self._progress)
        self.setStatusBar(status_bar)

        # Initial load
        self._grid.load_photos({})

    # ------------------------------------------------------------------
    def _toggle_sidebar(self, checked: bool) -> None:
        self._sidebar.setVisible(checked)

    def _toggle_props(self, checked: bool) -> None:
        self._props.setVisible(checked)

    def _toggle_filterbar(self, checked: bool) -> None:
        self._filterbar.setVisible(checked)

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
            show_fb = self._filterbar_action.isChecked()
            self._filterbar.setVisible(show_fb)
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

    def _on_grid_rating_changed(self, photo_id: int, rating: int) -> None:
        """Refresh props panel when grid keyboard shortcut changes rating."""
        from core.db.catalog import get_connection
        from core.query import get_photo_by_id
        conn = get_connection(self._catalog_path)
        row = get_photo_by_id(conn, photo_id)
        if row:
            self._props.show_photo(row)

    def _on_grid_flag_changed(self, photo_id: int, flag: int) -> None:
        """Refresh props panel when grid keyboard shortcut changes flag."""
        from core.db.catalog import get_connection
        from core.query import get_photo_by_id
        conn = get_connection(self._catalog_path)
        row = get_photo_by_id(conn, photo_id)
        if row:
            self._props.show_photo(row)

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
        new_count = stats.get("inserted", 0) + stats.get("updated", 0)
        self._status_label.setText(
            f"Done — {stats.get('inserted', 0)} new, "
            f"{stats.get('updated', 0)} updated, "
            f"{stats.get('relinked', 0)} relinked, "
            f"{stats.get('errors', 0)} errors"
        )
        self._worker.wake()
        self._sidebar.refresh()
        self._grid.load_photos(self._merged_filter())
        if new_count > 0:
            self._auto_enrich_after_scan(new_count)

    def _auto_enrich_after_scan(self, new_count: int) -> None:
        """Silently enqueue pHash, CLIP tagging, and face detection for newly added photos."""
        try:
            from core.duplicates import compute_phash_batch
            ps = compute_phash_batch(self._catalog_path)
        except Exception:
            ps = {"enqueued": 0}
        try:
            from core.topics import tag_photos_batch
            ts = tag_photos_batch(self._catalog_path)
        except Exception:
            ts = {"enqueued": 0}
        try:
            from core.faces import detect_faces_batch
            fs = detect_faces_batch(self._catalog_path)
        except Exception:
            fs = {"enqueued": 0}
        self._worker.wake()
        total = ps.get("enqueued", 0) + ts.get("enqueued", 0) + fs.get("enqueued", 0)
        if total:
            self._status_label.setText(
                f"Scan done ({new_count} new). Enrichment queued: "
                f"{ps.get('enqueued',0)} pHash, "
                f"{ts.get('enqueued',0)} CLIP tags, "
                f"{fs.get('enqueued',0)} face jobs."
            )

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

    def _on_redetect_faces(self) -> None:
        from core.faces import detect_faces_batch
        r = QMessageBox.question(
            self, "Re-detect Faces",
            "This will re-run face detection on photos that only have tiny/distant faces "
            "(< 60 px). Small existing face records will be removed and replaced.\n\n"
            "Named person assignments for LARGE faces are kept. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r == QMessageBox.StandardButton.Yes:
            stats = detect_faces_batch(self._catalog_path, force_redetect=True)
            self._status_label.setText(f"Re-detect: {stats['enqueued']} photos re-queued.")
            self._worker.wake()

    def _on_cluster_faces(self) -> None:
        from core.clustering import cluster_unassigned_faces
        from core.people import cleanup_empty_persons
        stats = cluster_unassigned_faces(self._catalog_path)
        cleaned = cleanup_empty_persons(self._catalog_path)
        self._status_label.setText(
            f"Clustering: {stats['people_created']} people created, "
            f"{stats['faces_assigned']} faces assigned. "
            f"{cleaned} empty persons removed."
        )
        self._sidebar.refresh()

    def _on_recluster_faces(self) -> None:
        from core.db.catalog import CatalogWriter, get_connection
        from core.clustering import cluster_unassigned_faces
        from core.people import cleanup_empty_persons

        conn = get_connection(self._catalog_path)
        auto_persons = conn.execute(
            "SELECT id FROM people WHERE name GLOB 'Person [0-9]*'"
        ).fetchall()
        if auto_persons:
            ids = [r["id"] for r in auto_persons]
            with CatalogWriter(self._catalog_path) as wc:
                wc.execute(
                    f"UPDATE faces SET person_id=NULL WHERE person_id IN ({','.join('?'*len(ids))})",
                    ids
                )
                wc.execute(
                    f"DELETE FROM people WHERE id IN ({','.join('?'*len(ids))})",
                    ids
                )

        stats = cluster_unassigned_faces(self._catalog_path)
        cleaned = cleanup_empty_persons(self._catalog_path)
        self._status_label.setText(
            f"Re-clustered: {stats['people_created']} people, {stats['faces_assigned']} faces. "
            f"{cleaned} orphans removed."
        )
        self._sidebar.refresh()

    def _on_unassigned_faces(self) -> None:
        from ui.unassigned_faces import UnassignedFacesDialog
        dlg = UnassignedFacesDialog(self._catalog_path, self)
        dlg.people_changed.connect(self._sidebar.refresh)
        dlg.exec()

    def _on_face_gallery(self) -> None:
        from ui.face_gallery import PersonGalleryDialog
        dlg = PersonGalleryDialog(self._catalog_path, parent=self)
        dlg.people_changed.connect(self._sidebar.refresh)
        dlg.show()

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

    def _on_download_clip(self) -> None:
        from core.paths import data_dir
        clip_dir = data_dir() / "clip"
        QMessageBox.information(
            self,
            "Download CLIP Models",
            f"CLIP models must be placed in:\n{clip_dir}\n\n"
            "Files needed:\n"
            "  - clip_visual.onnx\n"
            "  - clip_text.onnx\n"
            "  - bpe_simple_vocab_16e6.txt.gz\n\n"
            "See build/CLIP_SETUP.md for download instructions.",
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

    def _on_group_similar(self) -> None:
        from ui.groupview import GroupViewDialog
        from core.query import get_photos
        merged = self._merged_filter()
        conn = __import__("core.db.catalog", fromlist=["get_connection"]).get_connection(self._catalog_path)
        rows = get_photos(conn, limit=5000, **merged)
        ids = [r["id"] for r in rows]
        scope = repr(sorted(merged.items()))[:120]
        dlg = GroupViewDialog(ids, self._catalog_path, scope=scope, parent=self)
        dlg.collection_changed.connect(self._sidebar.refresh)
        dlg.exec()

    def _on_merge_nearby_places(self) -> None:
        from core.places import cluster_by_gps_proximity
        result = cluster_by_gps_proximity(self._catalog_path)
        self._status_label.setText(
            f"Places merged: {result['merges']} merge{'s' if result['merges'] != 1 else ''}, "
            f"{result['places_removed']} place records removed."
        )
        self._sidebar.refresh()

    def _on_show_map(self) -> None:
        if not self._catalog_path:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "No Catalog", "Open a folder first.")
            return
        from ui.mapview import MapView
        dlg = MapView(self._catalog_path, parent=self)
        dlg.exec()

    def _on_group_trips(self) -> None:
        from core.places import auto_group_trips
        stats = auto_group_trips(self._catalog_path)
        self._status_label.setText(
            f"Trips: {stats['trips_created']} created, "
            f"{stats['photos_assigned']} photos assigned."
        )
        self._sidebar.refresh()

    def _on_settings(self) -> None:
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        dlg.exec()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:
        self._poll_timer.stop()
        if self._worker:
            self._worker.stop()
            self._worker.join(timeout=3)
        super().closeEvent(event)
