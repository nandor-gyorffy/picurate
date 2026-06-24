"""Right-side properties / EXIF panel."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_MONTHS = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _fmt_date(s: str | None) -> str:
    if not s:
        return "—"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(s)
        mn = _MONTHS[dt.month] if 1 <= dt.month <= 12 else str(dt.month)
        return f"{dt.day} {mn} {dt.year}  {dt.hour:02d}:{dt.minute:02d}"
    except Exception:
        return s


def _fmt_gps(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return "—"
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}° {ns},  {abs(lon):.4f}° {ew}"


class PropertiesPanel(QWidget):
    def __init__(self, catalog_path: Path | None = None, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self.setMinimumWidth(200)
        self.setMaximumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        header = QLabel("Properties")
        header.setStyleSheet("font-weight: bold; font-size: 13px;")
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)
        self._form = QFormLayout(inner)
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(6)

        self._labels: dict[str, QLabel] = {}
        for key in ("Filename", "Date", "Camera", "Dimensions", "File size", "GPS",
                    "Status", "People", "Caption", "Keywords"):
            val = QLabel("—")
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._form.addRow(f"{key}:", val)
            self._labels[key] = val

        # ── Face strip ────────────────────────────────────────────────
        # Hidden by default; shown only when the selected photo has detected faces.
        self._face_strip_area = QScrollArea()
        self._face_strip_area.setFixedHeight(90)
        self._face_strip_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._face_strip_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._face_strip_area.setWidgetResizable(False)
        self._face_strip_area.setFrameShape(QScrollArea.Shape.NoFrame)

        self._face_strip_inner = QWidget()
        self._face_strip_layout = QHBoxLayout(self._face_strip_inner)
        self._face_strip_layout.setContentsMargins(2, 2, 2, 2)
        self._face_strip_layout.setSpacing(4)
        self._face_strip_area.setWidget(self._face_strip_inner)

        outer.addWidget(self._face_strip_area)
        self._face_strip_area.setVisible(False)

        self.clear()

    def clear(self) -> None:
        for lbl in self._labels.values():
            lbl.setText("—")
        self._labels["Filename"].setText("Select a photo")
        self._face_strip_area.setVisible(False)
        self._clear_face_strip()

    def _clear_face_strip(self) -> None:
        """Remove all face thumbnails from the strip."""
        while self._face_strip_layout.count():
            item = self._face_strip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _populate_face_strip(self, photo_id: int) -> None:
        """Load face crops for photo_id and show/hide the strip accordingly."""
        self._clear_face_strip()
        if not self._catalog_path:
            self._face_strip_area.setVisible(False)
            return
        try:
            from core.faces import get_faces_for_photo, get_face_crop_pixmap
            from core.db.catalog import get_connection
            faces = get_faces_for_photo(photo_id, self._catalog_path)
            if not faces:
                self._face_strip_area.setVisible(False)
                return

            conn = get_connection(self._catalog_path)
            total_w = 0
            _FACE_SIZE = 64
            for face in faces:
                face_id = face["id"]
                person_id = face.get("person_id")
                # Get person name for tooltip
                person_name = "Unassigned"
                if person_id is not None:
                    pr = conn.execute(
                        "SELECT name FROM people WHERE id=?", (person_id,)
                    ).fetchone()
                    if pr:
                        person_name = pr["name"]

                pix = get_face_crop_pixmap(face_id, self._catalog_path, size=_FACE_SIZE)
                thumb = QLabel()
                thumb.setFixedSize(_FACE_SIZE, _FACE_SIZE)
                thumb.setToolTip(person_name)
                if pix:
                    thumb.setPixmap(pix)
                else:
                    thumb.setStyleSheet("background: #555;")
                self._face_strip_layout.addWidget(thumb)
                total_w += _FACE_SIZE + 4

            self._face_strip_layout.addStretch()
            self._face_strip_inner.setFixedWidth(max(total_w + 8, 80))
            self._face_strip_inner.setFixedHeight(_FACE_SIZE + 4)
            self._face_strip_area.setVisible(True)

        except Exception:
            self._face_strip_area.setVisible(False)

    def show_photo(self, row: sqlite3.Row) -> None:
        cam_parts = [row["camera_make"] or "", row["camera_model"] or ""]
        cam = " ".join(p for p in cam_parts if p).strip() or "—"
        dims = (
            f"{row['width']} × {row['height']} px"
            if row["width"] and row["height"] else "—"
        )
        self._labels["Filename"].setText(row["filename"] or "—")
        self._labels["Date"].setText(_fmt_date(row["date_taken"]))
        self._labels["Camera"].setText(cam)
        self._labels["Dimensions"].setText(dims)
        self._labels["File size"].setText(_fmt_size(row["file_size"]))
        self._labels["GPS"].setText(_fmt_gps(row["gps_lat"], row["gps_lon"]))
        self._labels["Status"].setText(str(row["status"]))

        # People: query faces → people for this photo
        people_text = "—"
        if self._catalog_path:
            try:
                from core.db.catalog import get_connection
                conn = get_connection(self._catalog_path)
                person_rows = conn.execute(
                    """SELECT DISTINCT pe.name
                       FROM faces f JOIN people pe ON pe.id = f.person_id
                       WHERE f.photo_id = ?
                       ORDER BY pe.name""",
                    (row["id"],)
                ).fetchall()
                if person_rows:
                    people_text = ", ".join(r["name"] for r in person_rows)
            except Exception:
                pass
        self._labels["People"].setText(people_text)

        try:
            self._labels["Caption"].setText(row["caption"] or "—")
            kw = (row["keywords"] or "").replace(",", ", ").strip(" ,") or "—"
            self._labels["Keywords"].setText(kw)
        except (IndexError, KeyError):
            pass

        # Face strip
        try:
            self._populate_face_strip(row["id"])
        except Exception:
            self._face_strip_area.setVisible(False)
