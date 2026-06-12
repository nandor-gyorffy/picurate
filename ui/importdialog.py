"""Import dialog: preview and apply metadata from Picasa/folder/XMP sources."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from core.importers.engine import (
    CONFLICT_KEEP, CONFLICT_MERGE, CONFLICT_PREFER,
    apply_records, match_records,
)


_SOURCE_TYPES = [
    ("Folder structure", "folder"),
    ("Picasa (.picasa.ini files)", "picasa"),
    ("XMP / IPTC embedded", "xmp"),
]

_CONFLICTS = [
    ("Prefer import (overwrite existing)", CONFLICT_PREFER),
    ("Keep existing (skip if already set)", CONFLICT_KEEP),
    ("Merge (combine lists, prefer import for scalars)", CONFLICT_MERGE),
]


# ── Background threads ────────────────────────────────────────────────────────

class _PreviewThread(QThread):
    done = Signal(list)
    failed = Signal(str)

    def __init__(self, source_type: str, source_path: str, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._stype = source_type
        self._spath = source_path
        self._cpath = catalog_path

    def run(self) -> None:
        try:
            recs = self._load_records()
            recs = match_records(recs, self._cpath)
            self.done.emit(recs)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _load_records(self):
        if self._stype == "folder":
            from core.importers.folder import FolderImporter
            return FolderImporter().preview(self._spath)
        if self._stype == "picasa":
            from core.importers.picasa import PicasaImporter
            return PicasaImporter().preview(self._spath)
        if self._stype == "xmp":
            from core.importers.xmp import XmpImporter
            return XmpImporter().preview(self._spath)
        return []


class _ApplyThread(QThread):
    progress = Signal(int, int)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, records, source_type, source_path, catalog_path, conflict, parent=None):
        super().__init__(parent)
        self._records = records
        self._stype = source_type
        self._spath = source_path
        self._cpath = catalog_path
        self._conflict = conflict

    def run(self) -> None:
        try:
            stats = apply_records(
                self._records, self._cpath,
                source_type=self._stype,
                source_path=self._spath,
                conflict=self._conflict,
                progress_cb=lambda d, t: self.progress.emit(d, t),
            )
            self.finished.emit(stats)
        except Exception as exc:
            self.failed.emit(str(exc))


# ── Dialog ────────────────────────────────────────────────────────────────────

class ImportDialog(QDialog):
    """Three-step: pick source → preview → apply."""

    import_done = Signal()

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self._preview_thread: _PreviewThread | None = None
        self._apply_thread: _ApplyThread | None = None
        self._records: list = []
        self.setWindowTitle("Import Existing Organisation")
        self.setMinimumSize(640, 500)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── Source ─────────────────────────────────────────────────────
        src_box = QGroupBox("Source")
        src_layout = QFormLayout(src_box)

        self._type_combo = QComboBox()
        for label, key in _SOURCE_TYPES:
            self._type_combo.addItem(label, key)
        src_layout.addRow("Import type:", self._type_combo)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Select source folder…")
        path_row.addWidget(self._path_edit)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(browse_btn)
        src_layout.addRow("Folder:", path_row)

        preview_btn = QPushButton("Preview import…")
        preview_btn.clicked.connect(self._run_preview)
        src_layout.addRow(preview_btn)
        root.addWidget(src_box)

        # ── Preview table ──────────────────────────────────────────────
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["File", "Matched", "Rating", "Caption", "Albums"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        root.addWidget(self._table)

        self._preview_label = QLabel("Preview will appear here after clicking 'Preview import…'")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._preview_label)

        # ── Conflict strategy ──────────────────────────────────────────
        conf_box = QGroupBox("Conflict strategy")
        conf_layout = QFormLayout(conf_box)
        self._conflict_combo = QComboBox()
        for label, key in _CONFLICTS:
            self._conflict_combo.addItem(label, key)
        conf_layout.addRow(self._conflict_combo)
        root.addWidget(conf_box)

        # ── Progress ───────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status_label = QLabel("")
        root.addWidget(self._status_label)

        # ── Buttons ────────────────────────────────────────────────────
        buttons = QDialogButtonBox()
        self._apply_btn = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._run_apply)
        close_btn = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Source Folder")
        if folder:
            self._path_edit.setText(folder)

    def _run_preview(self) -> None:
        path = self._path_edit.text().strip()
        if not path:
            self._status_label.setText("Please choose a source folder.")
            return

        self._status_label.setText("Scanning…")
        self._table.setRowCount(0)
        self._apply_btn.setEnabled(False)

        stype = self._type_combo.currentData()
        self._preview_thread = _PreviewThread(stype, path, self._catalog_path, self)
        self._preview_thread.done.connect(self._on_preview_done)
        self._preview_thread.failed.connect(self._on_preview_failed)
        self._preview_thread.start()

    def _on_preview_done(self, records: list) -> None:
        self._records = records
        matched = [r for r in records if r.matched_photo_id is not None]
        self._status_label.setText(
            f"Found {len(records)} records — {len(matched)} matched to catalog photos."
        )

        self._table.setRowCount(0)
        for rec in records[:500]:  # cap preview at 500 rows
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(rec.filename))
            matched_str = "✓" if rec.matched_photo_id else "✗"
            self._table.setItem(row, 1, QTableWidgetItem(matched_str))
            self._table.setItem(row, 2, QTableWidgetItem(
                "★" * rec.rating if rec.rating else ("Pick" if rec.flag == 1 else "")
            ))
            self._table.setItem(row, 3, QTableWidgetItem(rec.caption or ""))
            self._table.setItem(row, 4, QTableWidgetItem(", ".join(rec.album_names)))

        self._apply_btn.setEnabled(bool(matched))

    def _on_preview_failed(self, msg: str) -> None:
        self._status_label.setText(f"Preview failed: {msg}")

    def _run_apply(self) -> None:
        if not self._records:
            return
        stype = self._type_combo.currentData()
        path = self._path_edit.text().strip()
        conflict = self._conflict_combo.currentData()

        self._apply_btn.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._status_label.setText("Applying…")

        self._apply_thread = _ApplyThread(
            self._records, stype, path, self._catalog_path, conflict, self
        )
        self._apply_thread.progress.connect(self._on_progress)
        self._apply_thread.finished.connect(self._on_apply_done)
        self._apply_thread.failed.connect(self._on_apply_failed)
        self._apply_thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)

    def _on_apply_done(self, stats: dict) -> None:
        self._progress.setVisible(False)
        self._apply_btn.setEnabled(True)
        self._status_label.setText(
            f"Done — {stats['applied']} applied, {stats['skipped']} skipped, "
            f"{stats['albums_created']} collections created.  "
            f"(Batch #{stats['batch_id']})"
        )
        self.import_done.emit()

    def _on_apply_failed(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._apply_btn.setEnabled(True)
        self._status_label.setText(f"Import failed: {msg}")
