"""Left sidebar: Library, Folders, Collections, Timeline trees."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QInputDialog,
    QMenu,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.db.catalog import get_connection
from core.query import count_photos, get_timeline, get_unique_folders
from core import settings as _settings
from core.collections import (
    create_collection,
    delete_collection,
    get_collections,
    rename_collection,
)

_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

_ROLE_FILTER = Qt.UserRole
_ROLE_COLLECTION_ID = Qt.UserRole + 1


class SidebarWidget(QWidget):
    """Emits filter_changed(dict) when the user selects a node."""

    filter_changed = Signal(dict)

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self.setMinimumWidth(180)
        self.setMaximumWidth(260)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(0)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(14)
        self._tree.setAnimated(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree)

        self.refresh()

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        # Remember current selection filter so we can restore it
        cur = self._tree.currentItem()
        cur_filt = cur.data(0, _ROLE_FILTER) if cur else None

        self._tree.blockSignals(True)
        self._tree.clear()

        conn = get_connection(self._catalog_path)
        total = count_photos(conn)

        # ── Library ──────────────────────────────────────────────────
        lib_root = QTreeWidgetItem(self._tree, ["Library"])
        lib_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        lib_root.setExpanded(True)

        all_item = QTreeWidgetItem(lib_root, [f"All Photos  ({total})"])
        all_item.setData(0, _ROLE_FILTER, {})

        picked = count_photos(conn, flag=1)
        picked_item = QTreeWidgetItem(lib_root, [f"Picked  ({picked})"])
        picked_item.setData(0, _ROLE_FILTER, {"flag": 1})

        rated = count_photos(conn, rating_min=1)
        rated_item = QTreeWidgetItem(lib_root, [f"Rated  ({rated})"])
        rated_item.setData(0, _ROLE_FILTER, {"rating_min": 1})

        # ── Collections ───────────────────────────────────────────────
        col_root = QTreeWidgetItem(self._tree, ["Collections"])
        col_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        col_root.setExpanded(True)
        col_root.setData(0, _ROLE_FILTER, None)  # no filter on root

        for col in get_collections(self._catalog_path):
            node = QTreeWidgetItem(col_root, [f"{col['name']}  ({col['photo_count']})"])
            node.setData(0, _ROLE_FILTER, {"collection_id": col["id"]})
            node.setData(0, _ROLE_COLLECTION_ID, col["id"])
            node.setToolTip(0, col["name"])

        # ── Folders ───────────────────────────────────────────────────
        folders_root = QTreeWidgetItem(self._tree, ["Folders"])
        folders_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        folders_root.setExpanded(True)

        watch = _settings.get_watch_folders(self._catalog_path)
        folder_counts = get_unique_folders(conn)

        if watch:
            self._build_folder_tree(folders_root, watch, folder_counts)
        else:
            for folder, cnt in sorted(folder_counts.items()):
                node = QTreeWidgetItem(folders_root, [f"{Path(folder).name}  ({cnt})"])
                node.setData(0, _ROLE_FILTER, {"folder": folder})
                node.setToolTip(0, folder)

        # ── Timeline ─────────────────────────────────────────────────
        time_root = QTreeWidgetItem(self._tree, ["Timeline"])
        time_root.setFlags(Qt.ItemFlag.ItemIsEnabled)
        time_root.setExpanded(True)

        timeline = get_timeline(conn)
        current_year: int | None = None
        year_node: QTreeWidgetItem | None = None
        year_total = 0

        for y, m, cnt in timeline:
            if y != current_year:
                if year_node is not None:
                    year_node.setText(0, f"{current_year}  ({year_total})")
                    year_node.setData(0, _ROLE_FILTER, {"year": current_year})
                current_year = y
                year_total = 0
                year_node = QTreeWidgetItem(time_root, [str(y)])
                year_node.setData(0, _ROLE_FILTER, {"year": y})
            year_total += cnt
            mn = _MONTH_NAMES[m] if 1 <= m <= 12 else str(m)
            m_node = QTreeWidgetItem(year_node, [f"{mn}  ({cnt})"])
            m_node.setData(0, _ROLE_FILTER, {"year": y, "month": m})

        if year_node is not None:
            year_node.setText(0, f"{current_year}  ({year_total})")
            year_node.setData(0, _ROLE_FILTER, {"year": current_year})

        self._tree.blockSignals(False)
        self._tree.setCurrentItem(all_item)

    def _build_folder_tree(
        self,
        parent: QTreeWidgetItem,
        watch_folders: list[str],
        folder_counts: dict[str, int],
    ) -> None:
        for root_str in watch_folders:
            root = Path(root_str)
            root_total = sum(
                cnt for f, cnt in folder_counts.items()
                if f == str(root) or f.startswith(str(root) + "/")
            )
            root_node = QTreeWidgetItem(parent, [f"{root.name}  ({root_total})"])
            root_node.setData(0, _ROLE_FILTER, {"folder": str(root)})
            root_node.setToolTip(0, str(root))
            root_node.setExpanded(True)

            sub_folders = sorted({
                f for f in folder_counts
                if Path(f).parent == root or (
                    f.startswith(str(root) + "/") and
                    str(Path(f).relative_to(root)).count("/") == 0
                )
            })
            for sf in sub_folders:
                cnt = folder_counts.get(sf, 0)
                node = QTreeWidgetItem(root_node, [f"{Path(sf).name}  ({cnt})"])
                node.setData(0, _ROLE_FILTER, {"folder": sf})
                node.setToolTip(0, sf)

    # ------------------------------------------------------------------
    def _on_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        filt = item.data(0, _ROLE_FILTER)
        if filt is not None:
            self.filter_changed.emit(filt)

    def _on_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        cid = item.data(0, _ROLE_COLLECTION_ID)
        if cid is None:
            # Right-click on Collections root → offer "New collection"
            parent = item.parent()
            if item.text(0) == "Collections" or (parent and parent.text(0) == "Collections" and cid is None):
                menu = QMenu(self)
                menu.addAction("New collection…", self._new_collection)
                menu.exec(self._tree.viewport().mapToGlobal(pos))
            return

        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self._rename_collection(cid, item))
        menu.addSeparator()
        menu.addAction("Delete collection", lambda: self._delete_collection(cid))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _new_collection(self) -> None:
        name, ok = QInputDialog.getText(self, "New Collection", "Collection name:")
        if ok and name.strip():
            create_collection(name.strip(), catalog_path=self._catalog_path)
            self.refresh()

    def _rename_collection(self, cid: int, item: QTreeWidgetItem) -> None:
        current = item.data(0, _ROLE_COLLECTION_ID)
        name, ok = QInputDialog.getText(self, "Rename Collection", "New name:")
        if ok and name.strip():
            rename_collection(cid, name.strip(), self._catalog_path)
            self.refresh()

    def _delete_collection(self, cid: int) -> None:
        from PySide6.QtWidgets import QMessageBox
        r = QMessageBox.question(
            self, "Delete Collection",
            "Delete this collection? Photos are not affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r == QMessageBox.StandardButton.Yes:
            delete_collection(cid, self._catalog_path)
            self.refresh()
            self.filter_changed.emit({})
