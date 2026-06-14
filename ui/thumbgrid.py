"""Thumbnail grid with size slider, rating/flag overlay, and context menu."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, Signal, QRect, QPoint
from PySide6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSlider,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from core.db.catalog import get_connection
from core.query import get_photos
from core import metadata as _meta

_ROLE_ID    = Qt.UserRole
_ROLE_PATH  = Qt.UserRole + 1
_ROLE_THUMB = Qt.UserRole + 2
_ROLE_RATE  = Qt.UserRole + 3
_ROLE_FLAG  = Qt.UserRole + 4


def _placeholder_pixmap(size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.darkGray)
    return pix


# ── Rating/flag delegate ──────────────────────────────────────────────────────

class _ThumbDelegate(QStyledItemDelegate):
    """Draws star rating and flag colour border on top of the standard icon."""

    _STAR_FONT = QFont("Arial", 9)
    _STAR_COLOR = QColor(255, 210, 0)
    _PICK_COLOR = QColor(60, 200, 60)
    _REJECT_COLOR = QColor(210, 50, 50)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        super().paint(painter, option, index)

        rating = index.data(_ROLE_RATE) or 0
        flag   = index.data(_ROLE_FLAG) or 0

        rect: QRect = option.rect

        # Draw flag border
        if flag == 1:
            painter.save()
            painter.setPen(QPen(self._PICK_COLOR, 3))
            painter.drawRect(rect.adjusted(2, 2, -2, -2))
            painter.restore()
        elif flag == 2:
            painter.save()
            painter.setPen(QPen(self._REJECT_COLOR, 3))
            painter.drawRect(rect.adjusted(2, 2, -2, -2))
            painter.restore()

        # Draw star rating at bottom-left
        if rating > 0:
            stars = "★" * rating
            painter.save()
            painter.setFont(self._STAR_FONT)
            painter.setPen(self._STAR_COLOR)
            text_rect = QRect(rect.left() + 4, rect.bottom() - 18, rect.width() - 8, 16)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, stars)
            painter.restore()


# ── Thumbnail grid widget ─────────────────────────────────────────────────────

class ThumbnailGrid(QWidget):
    photo_activated = Signal(int)   # double-click → loupe
    photo_selected  = Signal(int)   # single-click  → props panel

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self._filter: dict = {}
        self._thumb_size = 200
        self._item_map: dict[int, QListWidgetItem] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── toolbar ───────────────────────────────────────────────────
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
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setItemDelegate(_ThumbDelegate(self._list))
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._apply_icon_size(self._thumb_size)

        self._list.itemClicked.connect(self._on_clicked)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._list)

        # lazy pixmap loader
        self._pending: list[QListWidgetItem] = []
        self._pixmap_timer = QTimer(self)
        self._pixmap_timer.setInterval(0)
        self._pixmap_timer.timeout.connect(self._load_pixmap_batch)

    # ------------------------------------------------------------------
    def load_photos(self, filt: dict | None = None) -> None:
        if filt is not None:
            self._filter = filt

        conn = get_connection(self._catalog_path)
        rows = get_photos(
            conn,
            folder=self._filter.get("folder"),
            year=self._filter.get("year"),
            month=self._filter.get("month"),
            rating_min=self._filter.get("rating_min"),
            flag=self._filter.get("flag"),
            search=self._filter.get("search"),
            collection_id=self._filter.get("collection_id"),
            place_id=self._filter.get("place_id"),
            trip_id=self._filter.get("trip_id"),
            person_id=self._filter.get("person_id"),
            tag=self._filter.get("tag"),
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
            item.setData(_ROLE_ID,    row["id"])
            item.setData(_ROLE_PATH,  row["file_path"])
            item.setData(_ROLE_THUMB, row["thumbnail_path"])
            item.setData(_ROLE_RATE,  row["rating"] or 0)
            item.setData(_ROLE_FLAG,  row["flag"] or 0)
            item.setToolTip(row["filename"] or "")
            item.setSizeHint(QSize(self._thumb_size + 8, self._thumb_size + 28))
            self._list.addItem(item)
            self._item_map[row["id"]] = item
            if row["thumbnail_path"] and Path(row["thumbnail_path"]).exists():
                self._pending.append(item)

        if self._pending:
            self._pixmap_timer.start()

    def update_thumbnail(self, photo_id: int, thumb_path: str) -> None:
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

    def refresh_item_metadata(self, photo_id: int) -> None:
        """Re-read rating/flag from DB and repaint the item (no image reload)."""
        item = self._item_map.get(photo_id)
        if item is None:
            return
        conn = get_connection(self._catalog_path)
        row = conn.execute(
            "SELECT rating, flag FROM photos WHERE id=?", (photo_id,)
        ).fetchone()
        if row:
            item.setData(_ROLE_RATE, row["rating"] or 0)
            item.setData(_ROLE_FLAG, row["flag"] or 0)
            self._list.update(self._list.indexFromItem(item))

    # ------------------------------------------------------------------
    def _apply_icon_size(self, size: int) -> None:
        self._list.setIconSize(QSize(size, size))
        self._list.setGridSize(QSize(size + 8, size + 28))

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
        hint = QSize(value + 8, value + 28)
        for item in self._item_map.values():
            item.setSizeHint(hint)
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

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        photo_id = item.data(_ROLE_ID)
        if photo_id is None:
            return

        menu = QMenu(self)
        rating_menu = menu.addMenu("Set rating")
        for r in range(6):
            label = "No rating" if r == 0 else "★" * r
            rating_menu.addAction(label, lambda _r=r, _pid=photo_id: self._set_rating(_pid, _r))

        flag_menu = menu.addMenu("Set flag")
        flag_menu.addAction("✓ Pick",   lambda _pid=photo_id: self._set_flag(_pid, _meta.FLAG_PICK))
        flag_menu.addAction("✗ Reject", lambda _pid=photo_id: self._set_flag(_pid, _meta.FLAG_REJECT))
        flag_menu.addAction("○ Unflag", lambda _pid=photo_id: self._set_flag(_pid, _meta.FLAG_NONE))

        menu.addSeparator()
        menu.addAction("Add to collection…", lambda _pid=photo_id: self._add_to_collection(_pid))

        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _set_rating(self, photo_id: int, rating: int) -> None:
        _meta.set_rating(photo_id, rating, self._catalog_path)
        self.refresh_item_metadata(photo_id)

    def _set_flag(self, photo_id: int, flag: int) -> None:
        _meta.set_flag(photo_id, flag, self._catalog_path)
        self.refresh_item_metadata(photo_id)

    def _add_to_collection(self, photo_id: int) -> None:
        from ui.collectiondialog import CollectionPickerDialog
        from core.collections import add_photo
        dlg = CollectionPickerDialog(self._catalog_path, self)
        if dlg.exec() and dlg.chosen_id is not None:
            add_photo(dlg.chosen_id, photo_id, self._catalog_path)
