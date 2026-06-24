"""Library Health dialog — scan errors, missing files, exact duplicates with auto-fix."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class _WorkerSignals(QObject):
    finished = Signal(dict)


class HealthDialog(QDialog):
    """Three-tab dialog: Scan Errors, Missing Files, Exact Duplicates."""

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self.setWindowTitle("Library Health")
        self.resize(860, 580)
        self._build_ui()
        self._load()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)

        self._tabs = QTabWidget()
        lay.addWidget(self._tabs)

        # ── Tab 1: Scan Errors ─────────────────────────────────────────
        self._errors_tree = self._make_tree(["File", "Error", "When"])
        err_widget = QWidget()
        err_lay = QVBoxLayout(err_widget)
        err_lay.setContentsMargins(4, 4, 4, 4)
        err_lay.addWidget(self._errors_tree)
        err_btn_row = QHBoxLayout()
        self._btn_retry_errors = QPushButton("Retry failed files")
        self._btn_retry_errors.setToolTip("Re-attempt scanning files that previously failed")
        self._btn_retry_errors.clicked.connect(self._on_retry_errors)
        self._btn_clear_errors = QPushButton("Clear error log")
        self._btn_clear_errors.setToolTip("Remove all error entries from the log")
        self._btn_clear_errors.clicked.connect(self._clear_errors)
        err_btn_row.addWidget(self._btn_retry_errors)
        err_btn_row.addWidget(self._btn_clear_errors)
        err_btn_row.addStretch()
        err_lay.addLayout(err_btn_row)
        self._tabs.addTab(err_widget, "Scan Errors")

        # ── Tab 2: Missing Files ───────────────────────────────────────
        self._missing_tree = self._make_tree(["File path", "Last seen"])
        miss_widget = QWidget()
        miss_lay = QVBoxLayout(miss_widget)
        miss_lay.setContentsMargins(4, 4, 4, 4)
        miss_lay.addWidget(QLabel(
            "Files that were catalogued but are no longer found at their original path. "
            "Auto-fix will try to locate them by content hash or filename."
        ))
        miss_lay.addWidget(self._missing_tree)
        miss_btn_row = QHBoxLayout()
        self._btn_autofix = QPushButton("Auto-fix (find moved files)")
        self._btn_autofix.setToolTip(
            "Try to relink missing files by matching content hash or searching nearby directories"
        )
        self._btn_autofix.clicked.connect(self._on_autofix_missing)
        self._btn_rescan = QPushButton("Rescan watched folders")
        self._btn_rescan.setToolTip("Re-scan all watched folders — handles most move/rename cases")
        self._btn_rescan.clicked.connect(self._on_rescan)
        self._btn_remove_missing = QPushButton("Remove from catalog")
        self._btn_remove_missing.setToolTip(
            "Permanently remove selected missing files from the catalog "
            "(does not touch any actual files)"
        )
        self._btn_remove_missing.clicked.connect(self._on_remove_missing)
        miss_btn_row.addWidget(self._btn_autofix)
        miss_btn_row.addWidget(self._btn_rescan)
        miss_btn_row.addStretch()
        miss_btn_row.addWidget(self._btn_remove_missing)
        miss_lay.addLayout(miss_btn_row)
        self._tabs.addTab(miss_widget, "Missing Files")

        # ── Tab 3: Exact Duplicates ────────────────────────────────────
        self._dupes_tree = self._make_tree(["Content hash", "Files with identical content"])
        dupe_widget = QWidget()
        dupe_lay = QVBoxLayout(dupe_widget)
        dupe_lay.setContentsMargins(4, 4, 4, 4)
        dupe_lay.addWidget(QLabel(
            "Groups of files with identical content (same SHA-256). "
            "Picurate never deletes files — use this list to remove copies manually."
        ))
        dupe_lay.addWidget(self._dupes_tree)
        dupe_btn_row = QHBoxLayout()
        self._btn_remove_dupes = QPushButton("Remove duplicates from catalog")
        self._btn_remove_dupes.setToolTip(
            "Keep one catalog entry per group and remove the extras. "
            "Does NOT delete any actual files from disk."
        )
        self._btn_remove_dupes.clicked.connect(self._on_remove_dupes_from_catalog)
        dupe_btn_row.addWidget(self._btn_remove_dupes)
        dupe_btn_row.addStretch()
        dupe_lay.addLayout(dupe_btn_row)
        self._tabs.addTab(dupe_widget, "Exact Duplicates")

        # ── Detail label ───────────────────────────────────────────────
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._detail)

        # ── Progress bar (hidden until needed) ────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setVisible(False)
        lay.addWidget(self._progress)

        # ── Bottom close button ────────────────────────────────────────
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        for tree in (self._errors_tree, self._missing_tree, self._dupes_tree):
            tree.currentItemChanged.connect(self._on_sel)

    def _make_tree(self, headers: list[str]) -> QTreeWidget:
        t = QTreeWidget()
        t.setHeaderLabels(headers)
        t.setRootIsDecorated(False)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(True)
        t.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        return t

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        from core.db.catalog import get_connection
        conn = get_connection(self._catalog_path)

        # Scan errors
        self._errors_tree.clear()
        rows = conn.execute(
            "SELECT file_path, error_msg, scan_time FROM scan_errors ORDER BY scan_time DESC LIMIT 500"
        ).fetchall()
        for r in rows:
            item = QTreeWidgetItem([r["file_path"], r["error_msg"], r["scan_time"] or ""])
            item.setData(0, Qt.ItemDataRole.UserRole, r["file_path"])
            self._errors_tree.addTopLevelItem(item)
        self._errors_tree.resizeColumnToContents(2)
        self._tabs.setTabText(0, f"Scan Errors ({len(rows)})")
        self._btn_retry_errors.setEnabled(bool(rows))
        self._btn_clear_errors.setEnabled(bool(rows))

        # Missing files
        self._missing_tree.clear()
        rows = conn.execute(
            "SELECT file_path, mtime FROM photos WHERE status='missing' ORDER BY file_path"
        ).fetchall()
        for r in rows:
            mt = ""
            if r["mtime"]:
                try:
                    from datetime import datetime
                    mt = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
            item = QTreeWidgetItem([r["file_path"], mt])
            item.setData(0, Qt.ItemDataRole.UserRole, r["file_path"])
            self._missing_tree.addTopLevelItem(item)
        self._missing_tree.resizeColumnToContents(1)
        self._tabs.setTabText(1, f"Missing Files ({len(rows)})")
        self._btn_autofix.setEnabled(bool(rows))
        self._btn_rescan.setEnabled(bool(rows))
        self._btn_remove_missing.setEnabled(bool(rows))

        # Exact duplicates
        self._dupes_tree.clear()
        rows = conn.execute("""
            SELECT full_hash, GROUP_CONCAT(file_path, '|') AS paths, COUNT(*) AS cnt
            FROM photos
            WHERE full_hash IS NOT NULL AND status='ok'
            GROUP BY full_hash
            HAVING cnt > 1
            ORDER BY cnt DESC
        """).fetchall()
        for r in rows:
            paths = r["paths"].split("|")
            display = "  //  ".join(Path(p).name for p in paths)
            item = QTreeWidgetItem([r["full_hash"][:12], display])
            item.setData(0, Qt.ItemDataRole.UserRole, "\n".join(paths))
            item.setToolTip(1, "\n".join(paths))
            self._dupes_tree.addTopLevelItem(item)
        self._tabs.setTabText(2, f"Exact Duplicates ({len(rows)} groups)")
        self._btn_remove_dupes.setEnabled(bool(rows))

        for tree in (self._errors_tree, self._missing_tree, self._dupes_tree):
            tree.resizeColumnToContents(0)

    def _on_sel(self, current, _prev) -> None:
        if current is None:
            self._detail.setText("")
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        self._detail.setText(str(data) if data else "")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool) -> None:
        self._progress.setVisible(busy)
        for btn in (
            self._btn_retry_errors, self._btn_clear_errors,
            self._btn_autofix, self._btn_rescan, self._btn_remove_missing,
            self._btn_remove_dupes,
        ):
            btn.setEnabled(not busy)

    def _on_retry_errors(self) -> None:
        self._set_busy(True)
        sig = _WorkerSignals()
        sig.finished.connect(self._on_retry_errors_done)
        def _run():
            from core.recovery import fix_scan_errors
            sig.finished.emit(fix_scan_errors(self._catalog_path))
        threading.Thread(target=_run, daemon=True).start()

    def _on_retry_errors_done(self, stats: dict) -> None:
        self._set_busy(False)
        self._load()
        QMessageBox.information(
            self, "Retry complete",
            f"Fixed: {stats['fixed']}  |  Still broken: {stats['still_broken']}\n\n"
            "Fixed files have been removed from the error log."
        )

    def _clear_errors(self) -> None:
        from core.db.catalog import CatalogWriter
        with CatalogWriter(self._catalog_path) as conn:
            conn.execute("DELETE FROM scan_errors")
        self._load()

    def _on_autofix_missing(self) -> None:
        self._set_busy(True)
        sig = _WorkerSignals()
        sig.finished.connect(self._on_autofix_done)
        def _run():
            from core.recovery import fix_missing_files
            sig.finished.emit(fix_missing_files(self._catalog_path))
        threading.Thread(target=_run, daemon=True).start()

    def _on_autofix_done(self, stats: dict) -> None:
        self._set_busy(False)
        self._load()
        total_fixed = stats["already_back"] + stats["relinked_by_hash"] + stats["relinked_by_name"]
        msg = (
            f"Auto-fix complete.\n\n"
            f"Files found at original path: {stats['already_back']}\n"
            f"Relinked by content hash:     {stats['relinked_by_hash']}\n"
            f"Relinked by filename search:  {stats['relinked_by_name']}\n"
            f"Still missing:                {stats['still_missing']}\n\n"
        )
        if stats["still_missing"] > 0:
            msg += (
                "For files still missing, try:\n"
                "  • Rescan watched folders (if you moved the photo folder)\n"
                "  • Open Folder… to add the new location\n"
                "  • Remove from catalog if the files are permanently gone"
            )
        QMessageBox.information(self, "Auto-fix result", msg)

    def _on_rescan(self) -> None:
        """Close dialog and trigger a rescan via the main window."""
        # Find the main window and call its rescan slot
        from ui.mainwindow import MainWindow
        mw = self.parent()
        while mw is not None and not isinstance(mw, MainWindow):
            mw = mw.parent()
        self.accept()
        if mw is not None:
            mw._on_rescan()

    def _on_remove_missing(self) -> None:
        selected = self._missing_tree.selectedItems()
        if not selected:
            # If nothing selected, offer to remove all
            answer = QMessageBox.question(
                self, "Remove missing files",
                f"Remove all {self._missing_tree.topLevelItemCount()} missing files from the catalog?\n\n"
                "This only removes catalog entries — no actual files are deleted.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            from core.recovery import remove_missing_from_catalog
            n = remove_missing_from_catalog(self._catalog_path)
            self._load()
            QMessageBox.information(self, "Done", f"Removed {n} entries from the catalog.")
        else:
            paths = [item.data(0, Qt.ItemDataRole.UserRole) for item in selected]
            answer = QMessageBox.question(
                self, "Remove selected",
                f"Remove {len(paths)} selected file(s) from the catalog?\n\n"
                "This only removes catalog entries — no actual files are deleted.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            from core.db.catalog import CatalogWriter
            with CatalogWriter(self._catalog_path) as w:
                for p in paths:
                    w.execute("DELETE FROM photos WHERE file_path=? AND status='missing'", (p,))
            self._load()

    def _on_remove_dupes_from_catalog(self) -> None:
        answer = QMessageBox.question(
            self, "Remove duplicate catalog entries",
            "For each group of identical files, keep the first entry and remove the rest.\n\n"
            "No files are deleted from disk — only catalog entries are removed.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        from core.db.catalog import CatalogWriter, get_connection
        conn = get_connection(self._catalog_path)
        groups = conn.execute("""
            SELECT full_hash, GROUP_CONCAT(id, '|') AS ids
            FROM photos
            WHERE full_hash IS NOT NULL AND status='ok'
            GROUP BY full_hash
            HAVING COUNT(*) > 1
        """).fetchall()

        removed = 0
        with CatalogWriter(self._catalog_path) as w:
            for g in groups:
                ids = [int(i) for i in g["ids"].split("|")]
                keep = ids[0]
                for drop_id in ids[1:]:
                    w.execute("DELETE FROM photos WHERE id=?", (drop_id,))
                    removed += 1

        self._load()
        QMessageBox.information(
            self, "Done",
            f"Removed {removed} duplicate catalog entries. "
            f"No files were deleted from disk."
        )


def health_summary(catalog_path: Path) -> dict:
    """Return counts for status-bar display without opening a dialog."""
    try:
        from core.db.catalog import get_connection
        conn = get_connection(catalog_path)
        errors  = conn.execute("SELECT COUNT(*) FROM scan_errors").fetchone()[0]
        missing = conn.execute("SELECT COUNT(*) FROM photos WHERE status='missing'").fetchone()[0]
        dupes   = conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT full_hash FROM photos
                WHERE full_hash IS NOT NULL AND status='ok'
                GROUP BY full_hash HAVING COUNT(*)>1
            )
        """).fetchone()[0]
        return {"errors": errors, "missing": missing, "duplicate_groups": dupes}
    except Exception:
        return {"errors": 0, "missing": 0, "duplicate_groups": 0}
