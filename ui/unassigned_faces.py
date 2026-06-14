"""Dialog for reviewing and assigning faces that have no person yet."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.db.catalog import get_connection, CatalogWriter
from core.faces import get_face_crop_pixmap, delete_face
from core.people import get_people

CROP_SIZE = 96
_ROLE_FACE_ID   = Qt.UserRole
_ROLE_PERSON_ID = Qt.UserRole + 1   # suggested person id (may be None)


class UnassignedFacesDialog(QDialog):
    """
    Shows all faces that have no person assignment.

    Each face crop shows its best-match person suggestion (if similarity ≥ threshold).
    Right-click to: accept suggestion, assign to any person, or delete permanently.
    The "Accept All" button bulk-assigns every face that has a confident suggestion.
    """

    people_changed = Signal()

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self.setWindowTitle("Unassigned Faces")
        self.resize(720, 560)

        layout = QVBoxLayout(self)

        # Info bar
        self._info = QLabel()
        layout.addWidget(self._info)

        # Suggestion threshold note
        note = QLabel("Faces with a green border have a confident person match. Right-click any face for options.")
        note.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(note)

        # Face grid
        self._grid = QListWidget()
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setIconSize(QSize(CROP_SIZE, CROP_SIZE))
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setSpacing(4)
        self._grid.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._grid.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._grid)

        # Buttons
        btn_row = QHBoxLayout()
        self._accept_all_btn = QPushButton("Accept All Suggestions")
        self._accept_all_btn.setToolTip("Assign every face that has a confident match to its suggested person")
        self._accept_all_btn.clicked.connect(self._accept_all)
        btn_row.addWidget(self._accept_all_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        close_btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btns.rejected.connect(self.accept)
        layout.addWidget(close_btns)

        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        self._grid.clear()
        conn = get_connection(self._catalog_path)

        # Load unassigned faces with their embeddings
        face_rows = conn.execute(
            """SELECT f.id, f.embedding, p.filename
               FROM faces f JOIN photos p ON p.id = f.photo_id
               WHERE f.person_id IS NULL AND f.embedding IS NOT NULL
               ORDER BY p.date_taken"""
        ).fetchall()

        # Build person centroids for suggestions
        suggestions = self._compute_suggestions(conn, face_rows)

        suggested_count = sum(1 for s in suggestions.values() if s is not None)
        self._info.setText(
            f"{len(face_rows)} unassigned face{'s' if len(face_rows) != 1 else ''} — "
            f"{suggested_count} with confident suggestions"
        )
        self._accept_all_btn.setEnabled(suggested_count > 0)

        for row in face_rows:
            fid = row["id"]
            suggestion = suggestions.get(fid)  # (person_id, person_name, score) or None

            pix = get_face_crop_pixmap(fid, self._catalog_path, CROP_SIZE)
            if pix is None:
                pix = QPixmap(CROP_SIZE, CROP_SIZE)
                pix.fill(Qt.GlobalColor.darkGray)

            # Draw a coloured border if there's a confident suggestion
            if suggestion:
                from PySide6.QtGui import QPainter, QPen, QColor
                bordered = QPixmap(CROP_SIZE + 6, CROP_SIZE + 6)
                bordered.fill(QColor(60, 200, 60))
                painter = QPainter(bordered)
                painter.drawPixmap(3, 3, pix)
                painter.end()
                pix = bordered

            label = row["filename"] or ""
            if suggestion:
                label = f"→ {suggestion[1]}"

            item = QListWidgetItem(QIcon(pix), label)
            item.setData(_ROLE_FACE_ID, fid)
            item.setData(_ROLE_PERSON_ID, suggestion[0] if suggestion else None)
            tip = row["filename"] or str(fid)
            if suggestion:
                tip += f"\nSuggested: {suggestion[1]} ({suggestion[2]:.0%})"
            item.setToolTip(tip)
            self._grid.addItem(item)

    def _compute_suggestions(self, conn, face_rows) -> dict:
        """Return {face_id: (person_id, name, score) | None} using centroid matching."""
        import numpy as np

        try:
            threshold = float(
                __import__("core.settings", fromlist=["get"]).get(
                    "face_cluster_threshold", self._catalog_path
                ) or 0.42
            )
        except Exception:
            threshold = 0.42

        # Build centroids for all persons
        named = conn.execute(
            """SELECT p.id, p.name, f.embedding
               FROM people p JOIN faces f ON f.person_id = p.id
               WHERE f.embedding IS NOT NULL"""
        ).fetchall()

        person_embs: dict[int, list] = {}
        person_names: dict[int, str] = {}
        for r in named:
            pid = r["id"]
            try:
                emb = json.loads(r["embedding"])
                person_embs.setdefault(pid, []).append(emb)
                person_names[pid] = r["name"]
            except Exception:
                pass

        centroids: dict[int, np.ndarray] = {
            pid: np.mean(embs, axis=0)
            for pid, embs in person_embs.items()
        }

        def cosine_sim(a, b):
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            if na == 0 or nb == 0:
                return 0.0
            return float(np.dot(a, b) / (na * nb))

        suggestions = {}
        for row in face_rows:
            fid = row["id"]
            try:
                emb = np.array(json.loads(row["embedding"]), dtype=np.float32)
            except Exception:
                suggestions[fid] = None
                continue

            best_pid, best_score = None, threshold
            for pid, centroid in centroids.items():
                score = cosine_sim(emb, centroid)
                if score > best_score:
                    best_score = score
                    best_pid = pid

            if best_pid is not None:
                suggestions[fid] = (best_pid, person_names[best_pid], best_score)
            else:
                suggestions[fid] = None

        return suggestions

    # ------------------------------------------------------------------
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

        # Accept suggestion (only if all selected have the same suggestion)
        if len(items) == 1:
            pid = items[0].data(_ROLE_PERSON_ID)
            if pid is not None:
                suggested_name = items[0].toolTip().split("Suggested: ")[-1].split(" (")[0]
                menu.addAction(
                    f"Accept: assign to {suggested_name}",
                    lambda: self._assign_items(items, pid)
                )
        else:
            suggested = [(it, it.data(_ROLE_PERSON_ID)) for it in items if it.data(_ROLE_PERSON_ID)]
            if suggested:
                menu.addAction(
                    f"Accept suggestions for {len(suggested)} face(s)",
                    lambda: self._accept_items_suggestions(suggested)
                )

        menu.addAction(
            f"Assign {len(items)} face(s) to person…",
            lambda: self._assign_to_person_dialog(items)
        )
        menu.addSeparator()
        menu.addAction(
            f"Not a face — delete {len(items)} permanently",
            lambda: self._delete_permanently(items)
        )
        menu.exec(self._grid.viewport().mapToGlobal(pos))

    def _assign_items(self, items: list, person_id: int) -> None:
        face_ids = [it.data(_ROLE_FACE_ID) for it in items]
        with CatalogWriter(self._catalog_path) as conn:
            conn.executemany(
                "UPDATE faces SET person_id=? WHERE id=?",
                [(person_id, fid) for fid in face_ids]
            )
        self.people_changed.emit()
        self._load()

    def _accept_items_suggestions(self, suggested: list) -> None:
        with CatalogWriter(self._catalog_path) as conn:
            conn.executemany(
                "UPDATE faces SET person_id=? WHERE id=?",
                [(pid, it.data(_ROLE_FACE_ID)) for it, pid in suggested]
            )
        self.people_changed.emit()
        self._load()

    def _assign_to_person_dialog(self, items: list) -> None:
        people = get_people(self._catalog_path)
        if not people:
            QMessageBox.information(self, "Assign Face", "No people in the database yet. Run Cluster Faces first.")
            return

        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QListWidgetItem, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Assign to person…")
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
            self._assign_items(items, target_pid)

    def _accept_all(self) -> None:
        items_with_suggestion = [
            (self._grid.item(i), self._grid.item(i).data(_ROLE_PERSON_ID))
            for i in range(self._grid.count())
            if self._grid.item(i).data(_ROLE_PERSON_ID) is not None
        ]
        if not items_with_suggestion:
            return
        n = len(items_with_suggestion)
        r = QMessageBox.question(
            self, "Accept All Suggestions",
            f"Assign {n} face{'s' if n != 1 else ''} to their suggested persons?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r == QMessageBox.StandardButton.Yes:
            self._accept_items_suggestions(items_with_suggestion)

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
                delete_face(it.data(_ROLE_FACE_ID), self._catalog_path)
            self.people_changed.emit()
            self._load()
