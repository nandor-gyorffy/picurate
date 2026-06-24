"""Library Health dialog — scan errors, missing files, exact duplicates."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class HealthDialog(QDialog):
    """Show scan errors, missing files, and exact duplicates."""

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self.setWindowTitle("Library Health")
        self.resize(820, 560)
        self._build_ui()
        self._load()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)

        self._tabs = QTabWidget()
        lay.addWidget(self._tabs)

        self._errors_tree  = self._make_tree(["File", "Error", "When"])
        self._missing_tree = self._make_tree(["File", "Last seen"])
        self._dupes_tree   = self._make_tree(["Hash (first 12)", "File paths"])

        self._tabs.addTab(self._wrap(self._errors_tree),  "Scan Errors")
        self._tabs.addTab(self._wrap(self._missing_tree), "Missing Files")
        self._tabs.addTab(self._wrap(self._dupes_tree),   "Exact Duplicates")

        # Detail pane (path label)
        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self._detail)

        # Buttons
        row = QHBoxLayout()
        self._btn_clear_errors = QPushButton("Clear error log")
        self._btn_clear_errors.clicked.connect(self._clear_errors)
        row.addWidget(self._btn_clear_errors)
        row.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)

        self._errors_tree.currentItemChanged.connect(self._on_sel)
        self._missing_tree.currentItemChanged.connect(self._on_sel)
        self._dupes_tree.currentItemChanged.connect(self._on_sel)

    def _make_tree(self, headers: list[str]) -> QTreeWidget:
        t = QTreeWidget()
        t.setHeaderLabels(headers)
        t.setRootIsDecorated(False)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(True)
        return t

    def _wrap(self, widget: QWidget) -> QWidget:
        w = QWidget()
        QVBoxLayout(w).addWidget(widget)
        return w

    # ── Data loading ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        from core.db.catalog import get_connection
        conn = get_connection(self._catalog_path)

        # ── Scan errors ───────────────────────────────────────────────
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

        # ── Missing files ─────────────────────────────────────────────
        self._missing_tree.clear()
        rows = conn.execute(
            "SELECT file_path, mtime FROM photos WHERE status='missing' ORDER BY file_path"
        ).fetchall()
        for r in rows:
            from datetime import datetime
            mt = ""
            if r["mtime"]:
                try:
                    mt = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
            item = QTreeWidgetItem([r["file_path"], mt])
            item.setData(0, Qt.ItemDataRole.UserRole, r["file_path"])
            self._missing_tree.addTopLevelItem(item)
        self._missing_tree.resizeColumnToContents(1)
        self._tabs.setTabText(1, f"Missing Files ({len(rows)})")

        # ── Exact duplicates ──────────────────────────────────────────
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

        for tree in (self._errors_tree, self._missing_tree, self._dupes_tree):
            tree.resizeColumnToContents(0)

    def _on_sel(self, current, _prev) -> None:
        if current is None:
            self._detail.setText("")
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        self._detail.setText(str(data) if data else "")

    def _clear_errors(self) -> None:
        from core.db.catalog import CatalogWriter
        with CatalogWriter(self._catalog_path) as conn:
            conn.execute("DELETE FROM scan_errors")
        self._load()


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
