"""Person Gallery dialog — browse and manage all recognized people and their faces."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.faces import get_face_crop_pixmap, assign_person, delete_face
from core.people import (
    delete_person,
    get_people,
    get_unassigned_face_count,
    merge_people,
    rename_person,
)
from core.db.catalog import get_connection

_CROP_SIZE = 80
_STRIP_MAX = 8  # max face thumbnails shown per person row


class _FaceThumb(QLabel):
    """A single face-crop thumbnail with a right-click context menu."""

    def __init__(self, face_id: int, person_id: int, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._face_id = face_id
        self._person_id = person_id
        self._catalog_path = catalog_path
        self.setFixedSize(_CROP_SIZE, _CROP_SIZE)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    def _show_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Assign to different person…", self._on_reassign)
        menu.addAction("Not a face — delete", self._on_delete)
        menu.exec(self.mapToGlobal(pos))

    def _on_reassign(self) -> None:
        people = [p for p in get_people(self._catalog_path) if p["id"] != self._person_id]
        if not people:
            QMessageBox.information(self, "Reassign Face", "No other people to assign to.")
            return
        names = [f"{p['name']}  ({p['photo_count']} photos)" for p in people]
        choice, ok = QInputDialog.getItem(
            self, "Assign to Person", "Select person:", names, 0, False
        )
        if ok and choice:
            idx = names.index(choice)
            assign_person(self._face_id, people[idx]["id"], self._catalog_path)
            # Notify the containing _PersonRow to reload
            row = self._find_person_row()
            if row:
                row.reload()
                gallery = self._find_gallery()
                if gallery:
                    gallery.people_changed.emit()

    def _on_delete(self) -> None:
        r = QMessageBox.question(
            self, "Delete Face",
            "Permanently delete this face record from the database?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r == QMessageBox.StandardButton.Yes:
            delete_face(self._face_id, self._catalog_path)
            row = self._find_person_row()
            if row:
                row.reload()
                gallery = self._find_gallery()
                if gallery:
                    gallery.people_changed.emit()

    def _find_person_row(self):
        w = self.parent()
        while w is not None:
            if isinstance(w, _PersonRow):
                return w
            w = w.parent()
        return None

    def _find_gallery(self):
        w = self.parent()
        while w is not None:
            if isinstance(w, PersonGalleryDialog):
                return w
            w = w.parent()
        return None


class _PersonRow(QWidget):
    """One row per person: name/count | face strip | action buttons."""

    def __init__(self, person: dict, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._person = person
        self._catalog_path = catalog_path

        self._outer = QHBoxLayout(self)
        self._outer.setContentsMargins(4, 4, 4, 4)
        self._outer.setSpacing(8)

        # ── Left: name + count ───────────────────────────────────────
        left = QWidget()
        left.setFixedWidth(130)
        left_v = QVBoxLayout(left)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(2)

        self._name_label = QLabel(person["name"])
        bold_font = QFont()
        bold_font.setBold(True)
        self._name_label.setFont(bold_font)
        self._name_label.setWordWrap(True)
        left_v.addWidget(self._name_label)

        self._name_edit = QLineEdit(person["name"])
        self._name_edit.setVisible(False)
        left_v.addWidget(self._name_edit)

        self._count_label = QLabel(f"{person['photo_count']} photo(s)")
        small_font = QFont()
        small_font.setPointSize(9)
        self._count_label.setFont(small_font)
        self._count_label.setStyleSheet("color: gray;")
        left_v.addWidget(self._count_label)

        left_v.addStretch()
        self._outer.addWidget(left)

        # ── Center: horizontal face strip ────────────────────────────
        self._strip_scroll = QScrollArea()
        self._strip_scroll.setFixedHeight(_CROP_SIZE + 8)
        self._strip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._strip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._strip_scroll.setWidgetResizable(False)
        self._strip_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._strip_inner = QWidget()
        self._strip_layout = QHBoxLayout(self._strip_inner)
        self._strip_layout.setContentsMargins(2, 2, 2, 2)
        self._strip_layout.setSpacing(4)
        self._strip_scroll.setWidget(self._strip_inner)
        self._outer.addWidget(self._strip_scroll, 1)

        # ── Right: action buttons ────────────────────────────────────
        right = QWidget()
        right.setFixedWidth(110)
        right_v = QVBoxLayout(right)
        right_v.setContentsMargins(0, 0, 0, 0)
        right_v.setSpacing(4)

        self._rename_btn = QPushButton("Rename")
        self._rename_btn.setFixedHeight(26)
        self._rename_btn.clicked.connect(self._toggle_rename)
        right_v.addWidget(self._rename_btn)

        merge_btn = QPushButton("Merge into…")
        merge_btn.setFixedHeight(26)
        merge_btn.clicked.connect(self._on_merge)
        right_v.addWidget(merge_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.setFixedHeight(26)
        delete_btn.clicked.connect(self._on_delete)
        right_v.addWidget(delete_btn)

        right_v.addStretch()
        self._outer.addWidget(right)

        self._load_faces()

    # ──────────────────────────────────────────────────────────────────
    def _load_faces(self) -> None:
        # Clear existing thumbnails
        while self._strip_layout.count():
            item = self._strip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        conn = get_connection(self._catalog_path)
        rows = conn.execute(
            """SELECT f.id FROM faces f
               JOIN photos p ON p.id = f.photo_id
               WHERE f.person_id = ?
               ORDER BY p.date_taken
               LIMIT ?""",
            (self._person["id"], _STRIP_MAX),
        ).fetchall()

        total_w = 0
        for row in rows:
            pix = get_face_crop_pixmap(row["id"], self._catalog_path, _CROP_SIZE)
            thumb = _FaceThumb(row["id"], self._person["id"], self._catalog_path, self._strip_inner)
            if pix:
                thumb.setPixmap(pix)
            else:
                thumb.setStyleSheet("background: #555;")
            self._strip_layout.addWidget(thumb)
            total_w += _CROP_SIZE + 4

        self._strip_layout.addStretch()
        self._strip_inner.setFixedWidth(max(total_w + 8, 100))

    def reload(self) -> None:
        """Reload face strip from DB (called after reassign/delete)."""
        self._load_faces()

    # ──────────────────────────────────────────────────────────────────
    def _toggle_rename(self) -> None:
        """Toggle between name label and inline QLineEdit."""
        if self._name_edit.isVisible():
            # Commit rename
            new_name = self._name_edit.text().strip()
            if new_name and new_name != self._person["name"]:
                rename_person(self._person["id"], new_name, self._catalog_path)
                self._person = dict(self._person)
                self._person["name"] = new_name
                self._name_label.setText(new_name)
                gallery = self._find_gallery()
                if gallery:
                    gallery.people_changed.emit()
            self._name_edit.setVisible(False)
            self._name_label.setVisible(True)
            self._rename_btn.setText("Rename")
        else:
            # Enter edit mode
            self._name_edit.setText(self._person["name"])
            self._name_label.setVisible(False)
            self._name_edit.setVisible(True)
            self._name_edit.setFocus()
            self._name_edit.selectAll()
            self._rename_btn.setText("Save")

    def _on_merge(self) -> None:
        people = [p for p in get_people(self._catalog_path) if p["id"] != self._person["id"]]
        if not people:
            QMessageBox.information(self, "Merge", "No other people to merge into.")
            return
        names = [f"{p['name']}  ({p['photo_count']} photos)" for p in people]
        choice, ok = QInputDialog.getItem(
            self, "Merge Into Person",
            f"Merge '{self._person['name']}' into:", names, 0, False
        )
        if ok and choice:
            idx = names.index(choice)
            target = people[idx]
            r = QMessageBox.question(
                self, "Confirm Merge",
                f"Merge '{self._person['name']}' into '{target['name']}'?\n"
                "All faces will be moved to the target person and this person will be deleted.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if r == QMessageBox.StandardButton.Yes:
                merge_people(self._person["id"], target["id"], self._catalog_path)
                gallery = self._find_gallery()
                if gallery:
                    gallery.people_changed.emit()
                    gallery._refresh()

    def _on_delete(self) -> None:
        r = QMessageBox.question(
            self, "Delete Person",
            f"Delete '{self._person['name']}'?\n"
            "Their faces will be unassigned (kept for re-clustering). This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r == QMessageBox.StandardButton.Yes:
            delete_person(self._person["id"], self._catalog_path)
            gallery = self._find_gallery()
            if gallery:
                gallery.people_changed.emit()
                gallery._refresh()

    def _find_gallery(self):
        w = self.parent()
        while w is not None:
            if isinstance(w, PersonGalleryDialog):
                return w
            w = w.parent()
        return None


class PersonGalleryDialog(QDialog):
    """Non-modal dialog listing all people with face strips and management actions."""

    people_changed = Signal()

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self.setWindowTitle("People Gallery")
        self.resize(780, 560)
        self.setWindowFlag(Qt.WindowType.Window, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ── Top bar ───────────────────────────────────────────────────
        top_bar = QHBoxLayout()
        self._title_label = QLabel("People")
        bold = QFont()
        bold.setBold(True)
        bold.setPointSize(12)
        self._title_label.setFont(bold)
        top_bar.addWidget(self._title_label)

        top_bar.addStretch()

        merge_sel_btn = QPushButton("Merge Selected")
        merge_sel_btn.setToolTip("Merge is done per-person using the 'Merge into…' button in each row")
        merge_sel_btn.setEnabled(False)
        top_bar.addWidget(merge_sel_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        top_bar.addWidget(refresh_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        top_bar.addWidget(close_btn)

        outer.addLayout(top_bar)

        # ── Scrollable person list ────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(self._scroll, 1)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._scroll.setWidget(self._list_widget)

        # ── Bottom: unassigned faces ──────────────────────────────────
        bottom_bar = QHBoxLayout()
        self._unassigned_label = QLabel()
        bottom_bar.addWidget(self._unassigned_label)
        bottom_bar.addStretch()

        review_btn = QPushButton("→ Review Unassigned")
        review_btn.clicked.connect(self._on_review_unassigned)
        bottom_bar.addWidget(review_btn)
        outer.addLayout(bottom_bar)

        self._refresh()

    # ──────────────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        # Remove existing rows
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        people = get_people(self._catalog_path)
        self._title_label.setText(f"People ({len(people)})")

        for p in people:
            row = _PersonRow(p, self._catalog_path, self._list_widget)
            self._list_layout.addWidget(row)

        self._list_layout.addStretch()

        unassigned = get_unassigned_face_count(self._catalog_path)
        self._unassigned_label.setText(f"Unassigned faces: {unassigned}")

    def _on_review_unassigned(self) -> None:
        from ui.unassigned_faces import UnassignedFacesDialog
        dlg = UnassignedFacesDialog(self._catalog_path, self)
        dlg.people_changed.connect(self.people_changed)
        dlg.people_changed.connect(self._refresh)
        dlg.exec()
