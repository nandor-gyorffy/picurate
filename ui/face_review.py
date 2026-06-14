"""Face review dialog: shows face-crop thumbnails for a person, allows corrections."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.db.catalog import get_connection
from core.faces import get_face_crop_pixmap, delete_face
from core.people import rename_person, delete_person, get_people


class FaceReviewDialog(QDialog):
    """
    Grid of face-crop thumbnails for one person.
    Right-click a face to unassign it or move it to another person.
    """
    people_changed = Signal()

    CROP_SIZE = 96

    def __init__(self, person_id: int, person_name: str, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._person_id = person_id
        self._catalog_path = catalog_path
        self.setWindowTitle(f"Review: {person_name}")
        self.resize(600, 500)

        layout = QVBoxLayout(self)

        # Name editor
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._name_edit = QLineEdit(person_name)
        self._name_edit.setMaximumWidth(220)
        name_row.addWidget(self._name_edit)
        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._do_rename)
        name_row.addWidget(rename_btn)
        name_row.addStretch()
        layout.addLayout(name_row)

        # Info label
        self._info = QLabel()
        layout.addWidget(self._info)

        # Face grid
        self._grid = QListWidget()
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setIconSize(QSize(self.CROP_SIZE, self.CROP_SIZE))
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setSpacing(4)
        self._grid.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._grid.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._grid)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        self._load_faces()

    def _load_faces(self) -> None:
        self._grid.setIconSize(QSize(self.CROP_SIZE, self.CROP_SIZE))
        self._grid.clear()
        conn = get_connection(self._catalog_path)
        rows = conn.execute(
            """SELECT f.id, p.filename
               FROM faces f JOIN photos p ON p.id = f.photo_id
               WHERE f.person_id = ?
               ORDER BY p.date_taken""",
            (self._person_id,)
        ).fetchall()
        self._info.setText(f"{len(rows)} face(s) assigned to this person")
        for row in rows:
            pix = get_face_crop_pixmap(row["id"], self._catalog_path, self.CROP_SIZE)
            if pix is None:
                pix = QPixmap(self.CROP_SIZE, self.CROP_SIZE)
                pix.fill(Qt.GlobalColor.darkGray)
            item = QListWidgetItem(QIcon(pix), row["filename"] or "")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(row["filename"] or "")
            self._grid.addItem(item)

    def _do_rename(self) -> None:
        name = self._name_edit.text().strip()
        if name:
            rename_person(self._person_id, name, self._catalog_path)
            self.setWindowTitle(f"Review: {name}")
            self.people_changed.emit()

    def _on_context_menu(self, pos) -> None:
        items = self._grid.selectedItems()
        if not items:
            item = self._grid.itemAt(pos)
            if item:
                item.setSelected(True)
                items = [item]
        if not items:
            return

        menu = QMenu(self)
        menu.addAction(
            f"Unassign {len(items)} face(s) (keep for re-clustering)",
            lambda: self._unassign(items)
        )
        menu.addAction(
            f"Move {len(items)} face(s) to person…",
            lambda: self._move_to_person(items)
        )
        menu.addSeparator()
        menu.addAction(
            f"Not a face — delete {len(items)} permanently",
            lambda: self._delete_permanently(items)
        )
        menu.exec(self._grid.viewport().mapToGlobal(pos))

    def _unassign(self, items: list) -> None:
        from core.db.catalog import CatalogWriter
        face_ids = [it.data(Qt.ItemDataRole.UserRole) for it in items]
        with CatalogWriter(self._catalog_path) as conn:
            conn.executemany(
                "UPDATE faces SET person_id=NULL WHERE id=?",
                [(fid,) for fid in face_ids]
            )
        self._load_faces()
        self.people_changed.emit()

    def _delete_permanently(self, items: list) -> None:
        n = len(items)
        r = QMessageBox.question(
            self, "Delete Faces",
            f"Permanently delete {n} face record{'s' if n != 1 else ''} from the database?\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r == QMessageBox.StandardButton.Yes:
            for it in items:
                delete_face(it.data(Qt.ItemDataRole.UserRole), self._catalog_path)
            self._load_faces()
            self.people_changed.emit()

    def _move_to_person(self, items: list) -> None:
        from core.db.catalog import CatalogWriter

        people = [p for p in get_people(self._catalog_path) if p["id"] != self._person_id]
        if not people:
            QMessageBox.information(self, "Move Face", "No other people to move to.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Move to person…")
        dlg.resize(280, 320)
        v = QVBoxLayout(dlg)
        lst = QListWidget()
        for p in people:
            li = QListWidgetItem(f"{p['name']}  ({p['photo_count']} photos)")
            li.setData(Qt.ItemDataRole.UserRole, p["id"])
            lst.addItem(li)
        v.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)

        if dlg.exec() and lst.currentItem():
            target_pid = lst.currentItem().data(Qt.ItemDataRole.UserRole)
            face_ids = [it.data(Qt.ItemDataRole.UserRole) for it in items]
            with CatalogWriter(self._catalog_path) as conn:
                conn.executemany(
                    "UPDATE faces SET person_id=? WHERE id=?",
                    [(target_pid, fid) for fid in face_ids]
                )
            self._load_faces()
            self.people_changed.emit()
