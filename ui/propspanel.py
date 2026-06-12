"""Right-side properties / EXIF panel."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(200)
        self.setMaximumWidth(320)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        header = QLabel("Properties")
        header.setStyleSheet("font-weight: bold; font-size: 13px;")
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)

        inner = QWidget()
        scroll.setWidget(inner)
        self._form = QFormLayout(inner)
        self._form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self._form.setLabelAlignment(Qt.AlignRight | Qt.AlignTop)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setSpacing(6)

        self._labels: dict[str, QLabel] = {}
        for key in ("Filename", "Date", "Camera", "Dimensions", "File size", "GPS",
                    "Status", "Caption", "Keywords"):
            val = QLabel("—")
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._form.addRow(f"{key}:", val)
            self._labels[key] = val

        self.clear()

    def clear(self) -> None:
        for lbl in self._labels.values():
            lbl.setText("—")
        self._labels["Filename"].setText("Select a photo")

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
        try:
            self._labels["Caption"].setText(row["caption"] or "—")
            kw = (row["keywords"] or "").replace(",", ", ").strip(" ,") or "—"
            self._labels["Keywords"].setText(kw)
        except (IndexError, KeyError):
            pass
