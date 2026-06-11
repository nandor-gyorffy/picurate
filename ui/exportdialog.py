"""Export dialog: configure and run a collection export."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.collections import get_collections
from core.export import (
    ExportOptions,
    LAYOUT_FLAT, LAYOUT_BY_YEAR, LAYOUT_BY_DATE,
    NAMING_ORIGINAL, NAMING_SEQUENTIAL, NAMING_DATE,
    export_collection,
)


# ── Background export thread ──────────────────────────────────────────────────

class _ExportThread(QThread):
    progress = Signal(int, int)
    finished = Signal(dict)
    failed  = Signal(str)

    def __init__(self, collection_id: int, dest: Path, options: ExportOptions,
                 catalog_path: Path, parent=None):
        super().__init__(parent)
        self._cid = collection_id
        self._dest = dest
        self._options = options
        self._catalog_path = catalog_path

    def run(self) -> None:
        try:
            stats = export_collection(
                self._cid, self._dest, self._options, self._catalog_path,
                progress_cb=lambda done, total: self.progress.emit(done, total),
            )
            self.finished.emit(stats)
        except Exception as exc:
            self.failed.emit(str(exc))


# ── Export dialog ─────────────────────────────────────────────────────────────

class ExportDialog(QDialog):
    """Configure and run an export for a collection."""

    def __init__(self, catalog_path: Path, collection_id: int | None = None, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self._thread: _ExportThread | None = None
        self.setWindowTitle("Export Collection")
        self.setMinimumWidth(480)
        self._build_ui()
        if collection_id is not None:
            self._select_collection(collection_id)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── Collection picker ─────────────────────────────────────────
        col_box = QGroupBox("Collection")
        col_layout = QFormLayout(col_box)
        self._col_combo = QComboBox()
        self._col_combo.setMinimumWidth(220)
        for col in get_collections(self._catalog_path):
            self._col_combo.addItem(f"{col['name']}  ({col['photo_count']})", col["id"])
        col_layout.addRow("Export:", self._col_combo)
        root.addWidget(col_box)

        # ── Destination ───────────────────────────────────────────────
        dest_box = QGroupBox("Destination")
        dest_layout = QHBoxLayout(dest_box)
        self._dest_edit = QLineEdit()
        self._dest_edit.setPlaceholderText("Choose output folder…")
        dest_layout.addWidget(self._dest_edit)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_dest)
        dest_layout.addWidget(browse_btn)
        root.addWidget(dest_box)

        # ── Image options ─────────────────────────────────────────────
        img_box = QGroupBox("Images")
        img_layout = QFormLayout(img_box)

        self._resize_check = QCheckBox("Resize to max dimension")
        img_layout.addRow(self._resize_check)

        self._max_dim_spin = QSpinBox()
        self._max_dim_spin.setRange(400, 7680)
        self._max_dim_spin.setValue(1920)
        self._max_dim_spin.setSuffix(" px")
        self._resize_check.toggled.connect(self._max_dim_spin.setEnabled)
        self._max_dim_spin.setEnabled(False)
        img_layout.addRow("Max dimension:", self._max_dim_spin)

        self._quality_spin = QSpinBox()
        self._quality_spin.setRange(50, 100)
        self._quality_spin.setValue(85)
        self._quality_spin.setSuffix("%")
        img_layout.addRow("JPEG quality:", self._quality_spin)

        self._strip_gps_check = QCheckBox("Strip GPS coordinates")
        img_layout.addRow(self._strip_gps_check)

        root.addWidget(img_box)

        # ── Layout & naming ───────────────────────────────────────────
        org_box = QGroupBox("Organisation")
        org_layout = QFormLayout(org_box)

        self._layout_combo = QComboBox()
        self._layout_combo.addItem("Flat (all in one folder)", LAYOUT_FLAT)
        self._layout_combo.addItem("By year (2024/…)", LAYOUT_BY_YEAR)
        self._layout_combo.addItem("By year/month (2024/01/…)", LAYOUT_BY_DATE)
        org_layout.addRow("Folder layout:", self._layout_combo)

        self._naming_combo = QComboBox()
        self._naming_combo.addItem("Keep original filename", NAMING_ORIGINAL)
        self._naming_combo.addItem("Sequential (0001_name.jpg)", NAMING_SEQUENTIAL)
        self._naming_combo.addItem("Date prefix (20240115_name.jpg)", NAMING_DATE)
        org_layout.addRow("File naming:", self._naming_combo)

        root.addWidget(org_box)

        # ── Extras ────────────────────────────────────────────────────
        extras_box = QGroupBox("Extras")
        extras_layout = QVBoxLayout(extras_box)
        self._gallery_check = QCheckBox("Generate HTML gallery (index.html)")
        self._contact_check = QCheckBox("Generate contact-sheet PDF")
        extras_layout.addWidget(self._gallery_check)
        extras_layout.addWidget(self._contact_check)
        root.addWidget(extras_box)

        # ── Progress ──────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status_label = QLabel("")
        root.addWidget(self._status_label)

        # ── Buttons ───────────────────────────────────────────────────
        buttons = QDialogButtonBox()
        self._export_btn = buttons.addButton("Export", QDialogButtonBox.ButtonRole.AcceptRole)
        self._export_btn.clicked.connect(self._start_export)
        close_btn = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

    def _select_collection(self, collection_id: int) -> None:
        for i in range(self._col_combo.count()):
            if self._col_combo.itemData(i) == collection_id:
                self._col_combo.setCurrentIndex(i)
                break

    def _browse_dest(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Export Folder")
        if folder:
            self._dest_edit.setText(folder)

    def _start_export(self) -> None:
        dest_text = self._dest_edit.text().strip()
        if not dest_text:
            self._status_label.setText("Please choose a destination folder.")
            return
        if self._col_combo.count() == 0:
            self._status_label.setText("No collections to export.")
            return

        cid = self._col_combo.currentData()
        dest = Path(dest_text)

        options = ExportOptions(
            resize=self._resize_check.isChecked(),
            max_dim=self._max_dim_spin.value(),
            quality=self._quality_spin.value(),
            layout=self._layout_combo.currentData(),
            naming=self._naming_combo.currentData(),
            strip_gps=self._strip_gps_check.isChecked(),
            html_gallery=self._gallery_check.isChecked(),
            contact_sheet=self._contact_check.isChecked(),
        )

        self._export_btn.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.setVisible(True)
        self._status_label.setText("Exporting…")

        self._thread = _ExportThread(cid, dest, options, self._catalog_path, self)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished.connect(self._on_finished)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)

    def _on_finished(self, stats: dict) -> None:
        self._progress.setVisible(False)
        self._export_btn.setEnabled(True)
        n = stats.get("exported", 0)
        skipped = stats.get("skipped", 0)
        errors = stats.get("errors", 0)
        vf = stats.get("verify_failures", 0)
        msg = f"Done — {n} exported"
        if skipped:
            msg += f", {skipped} skipped"
        if errors:
            msg += f", {errors} errors"
        if vf:
            msg += f", {vf} verify failures"
        msg += f"\n→ {stats.get('dest', '')}"
        self._status_label.setText(msg)

    def _on_failed(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._export_btn.setEnabled(True)
        self._status_label.setText(f"Export failed: {msg}")
