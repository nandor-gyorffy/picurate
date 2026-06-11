"""Thumbnail grid with size slider and lazy pixmap loading."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.db.catalog import get_connection
from core.query import get_photos

_ROLE_ID = Qt.UserRole
_ROLE_PATH = Qt.UserRole + 1
_ROLE_THUMB = Qt.UserRole + 2


def _placeholder_pixmap(size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.darkGray)
    return pix


class ThumbnailGrid(QWidget):
    """Scrollable thumbnail grid with a size slider."""

    photo_activated = Signal(int)   # double-click → open loupe
    photo_selected = Signal(int)    # single-click → show props

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self._filter: dict = {}
        self._thumb_size = 200
        self._item_map: dict[int, QListWidgetItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── toolbar bar ───────────────────────────────────────────────
        bar = QWidget()
        bar.setFixedHeight(36)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 4, 8, 4)

        self._count_label = QLabel("0 photos")
        bar_layout.addWidget(self._count_label)
        bar_layout.addStretch()
        bar_layout.addWidget(QLabel("Size:"))

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(64)
        self._slider.setMaximum(384)
        self._slider.setValue(self._thumb_size)
        self._slider.setFixedWidth(120)
        self._slider.valueChanged.connect(self._on_size_changed)
        bar_layout.addWidget(self._slider)

        layout.addWidget(bar)

        # ── list widget ───────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setSpacing(4)
        self._list.setUniformItemSizes(True)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._apply_icon_size(self._thumb_size)
        self._list.itemClicked.connect(self._on_clicked)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._list)

        # Lazy pixmap loader — processes one batch per event-loop tick
        self._pending: list[QListWidgetItem] = []
        self._pixmap_timer = QTimer(self)
        self._pixmap_timer.setInterval(0)
        self._pixmap_timer.timeout.connect(self._load_pixmap_batch)

    # ------------------------------------------------------------------
    def load_photos(self, filt: dict | None = None) -> None:
        """Rebuild the grid for the given filter (folder / year / month)."""
        if filt is not None:
            self._filter = filt

        conn = get_connection(self._catalog_path)
        rows = get_photos(
            conn,
            folder=self._filter.get("folder"),
            year=self._filter.get("year"),
            month=self._filter.get("month"),
        )

        n = len(rows)
        self._count_label.setText(f"{n} photo{'s' if n != 1 else ''}")

        self._pixmap_timer.stop()
        self._pending.clear()
        self._item_map.clear()
        self._list.clear()

        placeholder = _placeholder_pixmap(self._thumb_size)
        for row in rows:
            item = QListWidgetItem(QIcon(placeholder), row["filename"] or "")
            item.setData(_ROLE_ID, row["id"])
            item.setData(_ROLE_PATH, row["file_path"])
            item.setData(_ROLE_THUMB, row["thumbnail_path"])
            item.setToolTip(row["filename"] or "")
            item.setSizeHint(QSize(self._thumb_size + 8, self._thumb_size + 24))
            self._list.addItem(item)
            self._item_map[row["id"]] = item
            if row["thumbnail_path"] and Path(row["thumbnail_path"]).exists():
                self._pending.append(item)

        if self._pending:
            self._pixmap_timer.start()

    def update_thumbnail(self, photo_id: int, thumb_path: str) -> None:
        """Called by the main thread when the worker finishes a thumbnail job."""
        item = self._item_map.get(photo_id)
        if item is None:
            return
        pix = QPixmap(thumb_path)
        if not pix.isNull():
            item.setIcon(QIcon(pix.scaled(
                self._thumb_size, self._thumb_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )))
            item.setData(_ROLE_THUMB, thumb_path)

    # ------------------------------------------------------------------
    def _apply_icon_size(self, size: int) -> None:
        self._list.setIconSize(QSize(size, size))
        self._list.setGridSize(QSize(size + 8, size + 24))

    def _load_pixmap_batch(self) -> None:
        BATCH = 30
        for _ in range(BATCH):
            if not self._pending:
                self._pixmap_timer.stop()
                return
            item = self._pending.pop(0)
            thumb = item.data(_ROLE_THUMB)
            if not thumb:
                continue
            pix = QPixmap(thumb)
            if not pix.isNull():
                item.setIcon(QIcon(pix.scaled(
                    self._thumb_size, self._thumb_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )))

    def _on_size_changed(self, value: int) -> None:
        self._thumb_size = value
        self._apply_icon_size(value)
        # Re-scale already-loaded thumbnails
        self._pending = [
            item for item in self._item_map.values()
            if item.data(_ROLE_THUMB) and Path(str(item.data(_ROLE_THUMB))).exists()
        ]
        if self._pending:
            self._pixmap_timer.start()

    def _on_clicked(self, item: QListWidgetItem) -> None:
        pid = item.data(_ROLE_ID)
        if pid is not None:
            self.photo_selected.emit(pid)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        pid = item.data(_ROLE_ID)
        if pid is not None:
            self.photo_activated.emit(pid)
