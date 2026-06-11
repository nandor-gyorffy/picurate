"""Dialog to pick an existing collection or create a new one."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from core.collections import create_collection, get_collections


class CollectionPickerDialog(QDialog):
    """Shows existing collections; user picks one or creates a new one.
    After exec(), read .chosen_id (int or None).
    """

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self.chosen_id: int | None = None
        self.setWindowTitle("Add to Collection")
        self.resize(340, 300)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select a collection or create a new one:"))

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._accept_selection)
        layout.addWidget(self._list)
        self._populate()

        btn_row = QHBoxLayout()
        new_btn = QPushButton("New collection…")
        new_btn.clicked.connect(self._create_new)
        btn_row.addWidget(new_btn)
        btn_row.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

    def _populate(self) -> None:
        self._list.clear()
        for row in get_collections(self._catalog_path):
            item = QListWidgetItem(f"{row['name']}  ({row['photo_count']})")
            item.setData(Qt.UserRole, row["id"])
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _create_new(self) -> None:
        name, ok = QInputDialog.getText(self, "New Collection", "Collection name:")
        if ok and name.strip():
            cid = create_collection(name.strip(), catalog_path=self._catalog_path)
            self.chosen_id = cid
            self.accept()

    def _accept_selection(self) -> None:
        item = self._list.currentItem()
        if item:
            self.chosen_id = item.data(Qt.UserRole)
            self.accept()
