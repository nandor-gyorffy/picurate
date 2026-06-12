"""Cull/review mode: step through photos, rate, flag, add to collection."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut, QColor, QPainter, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.db.catalog import get_connection
from core.logger import get_logger
from core import metadata as _meta
from core.collections import add_photo, get_collections
from core.query import get_photos, get_photo_by_id

log = get_logger("picurate.cullview")

_ROLE_ID = Qt.UserRole


# ── Background image loader (shared with loupe) ───────────────────────────────

class _ImageLoader(QThread):
    loaded = Signal(QImage, int)  # (qimage, photo_id)
    failed = Signal(str)

    MAX_DIM = 2400

    def __init__(self, file_path: str, photo_id: int, parent=None):
        super().__init__(parent)
        self._path = file_path
        self._photo_id = photo_id

    def run(self) -> None:
        try:
            from PIL import Image, ImageOps
            img = Image.open(self._path)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((self.MAX_DIM, self.MAX_DIM), Image.LANCZOS)
            img = img.convert("RGB")
            raw = img.tobytes()
            qimg = QImage(
                raw, img.width, img.height, img.width * 3,
                QImage.Format.Format_RGB888,
            ).copy()
            self.loaded.emit(qimg, self._photo_id)
        except Exception as exc:
            self.failed.emit(str(exc))


# ── Zoomable image label ──────────────────────────────────────────────────────

class _ImageArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setWidget(self._label)
        self._pixmap: QPixmap | None = None

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pixmap = pix
        self._fit()

    def _fit(self) -> None:
        if self._pixmap is None:
            return
        vp = self.viewport().size()
        scaled = self._pixmap.scaled(
            vp, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)
        self._label.resize(scaled.size())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def clear(self) -> None:
        self._pixmap = None
        self._label.clear()


# ── Filmstrip ─────────────────────────────────────────────────────────────────

class _Filmstrip(QListWidget):
    """Horizontal strip of small thumbnails for surrounding photos."""

    THUMB = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QListView
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(QSize(self.THUMB, self.THUMB))
        self.setFixedHeight(self.THUMB + 12)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMovement(QListView.Movement.Static)
        self.setSpacing(2)


# ── Main Cull View widget ─────────────────────────────────────────────────────

class CullView(QWidget):
    """Full-pane single-photo review widget.

    Signals:
        exit_requested — user wants to leave cull mode
        photo_changed(int) — current photo_id changed (for props panel update)
        collection_changed — a photo was added to a collection (sidebar refresh)
    """

    exit_requested = Signal()
    photo_changed = Signal(int)
    collection_changed = Signal()

    def __init__(self, catalog_path: Path, filter_ctx: dict, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self._filter_ctx = filter_ctx
        self._photo_id: int | None = None
        self._photo_ids: list[int] = []
        self._loader: _ImageLoader | None = None

        self._build_ui()
        self._setup_shortcuts()
        self._load_photo_list()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Top bar ───────────────────────────────────────────────────
        top_bar = QWidget()
        top_bar.setFixedHeight(36)
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(8, 4, 8, 4)

        self._info_label = QLabel("Cull mode")
        top_layout.addWidget(self._info_label)
        top_layout.addStretch()

        hint = QLabel("1–5 rate  |  P pick  |  X reject  |  U unflag  |  C collection  |  ←→ navigate  |  Space next")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        top_layout.addWidget(hint)
        top_layout.addStretch()

        exit_btn = QPushButton("Exit Cull Mode")
        exit_btn.clicked.connect(self.exit_requested)
        top_layout.addWidget(exit_btn)

        root.addWidget(top_bar)

        # ── Main image area ───────────────────────────────────────────
        self._image_area = _ImageArea()
        root.addWidget(self._image_area, stretch=1)

        # ── Loading spinner ───────────────────────────────────────────
        self._spinner = QProgressBar()
        self._spinner.setRange(0, 0)
        self._spinner.setFixedHeight(4)
        self._spinner.setTextVisible(False)
        self._spinner.setVisible(False)
        root.addWidget(self._spinner)

        # ── Controls bar ──────────────────────────────────────────────
        ctrl_bar = QWidget()
        ctrl_bar.setFixedHeight(44)
        ctrl_layout = QHBoxLayout(ctrl_bar)
        ctrl_layout.setContentsMargins(8, 4, 8, 4)
        ctrl_layout.setSpacing(4)

        # Navigation
        prev_btn = QPushButton("◀ Prev")
        prev_btn.clicked.connect(self.go_prev)
        ctrl_layout.addWidget(prev_btn)

        next_btn = QPushButton("Next ▶")
        next_btn.clicked.connect(self.go_next)
        ctrl_layout.addWidget(next_btn)

        ctrl_layout.addSpacing(16)

        # Star rating buttons
        self._star_btns: list[QPushButton] = []
        for i in range(1, 6):
            btn = QPushButton("★" * i)
            btn.setFixedWidth(40 + i * 8)
            btn.setToolTip(f"Rate {i} star{'s' if i > 1 else ''} ({i})")
            btn.clicked.connect(lambda _, r=i: self._rate(r))
            self._star_btns.append(btn)
            ctrl_layout.addWidget(btn)

        clear_rating_btn = QPushButton("✕ rating")
        clear_rating_btn.setFixedWidth(72)
        clear_rating_btn.clicked.connect(lambda: self._rate(0))
        ctrl_layout.addWidget(clear_rating_btn)

        ctrl_layout.addSpacing(16)

        # Flag buttons
        pick_btn = QPushButton("✓ Pick (P)")
        pick_btn.setFixedWidth(90)
        pick_btn.clicked.connect(lambda: self._flag(_meta.FLAG_PICK))
        ctrl_layout.addWidget(pick_btn)

        reject_btn = QPushButton("✗ Reject (X)")
        reject_btn.setFixedWidth(100)
        reject_btn.clicked.connect(lambda: self._flag(_meta.FLAG_REJECT))
        ctrl_layout.addWidget(reject_btn)

        unflag_btn = QPushButton("○ Unflag (U)")
        unflag_btn.setFixedWidth(100)
        unflag_btn.clicked.connect(lambda: self._flag(_meta.FLAG_NONE))
        ctrl_layout.addWidget(unflag_btn)

        ctrl_layout.addSpacing(16)

        # Collection
        col_btn = QPushButton("+ Collection (C)")
        col_btn.clicked.connect(self._add_to_collection)
        ctrl_layout.addWidget(col_btn)

        ctrl_layout.addStretch()

        # Status (current rating/flag display)
        self._status_label = QLabel("")
        self._status_label.setMinimumWidth(160)
        ctrl_layout.addWidget(self._status_label)

        root.addWidget(ctrl_bar)

        # ── Filmstrip ─────────────────────────────────────────────────
        self._filmstrip = _Filmstrip()
        self._filmstrip.itemClicked.connect(self._on_filmstrip_click)
        root.addWidget(self._filmstrip)

    def _setup_shortcuts(self) -> None:
        def shortcut(key, slot):
            QShortcut(QKeySequence(key), self).activated.connect(slot)

        shortcut(Qt.Key.Key_Left, self.go_prev)
        shortcut(Qt.Key.Key_Right, self.go_next)
        shortcut(Qt.Key.Key_Space, self.go_next)
        shortcut(Qt.Key.Key_1, lambda: self._rate(1))
        shortcut(Qt.Key.Key_2, lambda: self._rate(2))
        shortcut(Qt.Key.Key_3, lambda: self._rate(3))
        shortcut(Qt.Key.Key_4, lambda: self._rate(4))
        shortcut(Qt.Key.Key_5, lambda: self._rate(5))
        shortcut(Qt.Key.Key_0, lambda: self._rate(0))
        shortcut(Qt.Key.Key_P, lambda: self._flag(_meta.FLAG_PICK))
        shortcut(Qt.Key.Key_X, lambda: self._flag(_meta.FLAG_REJECT))
        shortcut(Qt.Key.Key_U, lambda: self._flag(_meta.FLAG_NONE))
        shortcut(Qt.Key.Key_C, self._add_to_collection)

    # ------------------------------------------------------------------
    def _load_photo_list(self) -> None:
        conn = get_connection(self._catalog_path)
        rows = get_photos(
            conn,
            folder=self._filter_ctx.get("folder"),
            year=self._filter_ctx.get("year"),
            month=self._filter_ctx.get("month"),
            rating_min=self._filter_ctx.get("rating_min"),
            flag=self._filter_ctx.get("flag"),
            search=self._filter_ctx.get("search"),
            collection_id=self._filter_ctx.get("collection_id"),
            place_id=self._filter_ctx.get("place_id"),
            trip_id=self._filter_ctx.get("trip_id"),
            person_id=self._filter_ctx.get("person_id"),
            tag=self._filter_ctx.get("tag"),
            limit=5000,
        )
        self._photo_ids = [r["id"] for r in rows]
        total = len(self._photo_ids)
        self._info_label.setText(f"Cull mode — {total} photo{'s' if total != 1 else ''}")
        if self._photo_ids:
            self._show_photo(self._photo_ids[0])

    def _show_photo(self, photo_id: int) -> None:
        self._photo_id = photo_id
        conn = get_connection(self._catalog_path)
        row = get_photo_by_id(conn, photo_id)
        if row is None:
            return

        # Update status label
        rating = row["rating"] or 0
        flag = row["flag"] or 0
        stars = "★" * rating + "☆" * (5 - rating) if rating else "☆☆☆☆☆"
        flag_str = {0: "—", 1: "✓ Picked", 2: "✗ Rejected"}.get(flag, "")
        self._status_label.setText(f"{stars}  {flag_str}")

        # Emit for props panel
        self.photo_changed.emit(photo_id)

        # Update filmstrip selection
        self._update_filmstrip(photo_id)

        # Load full image in background
        if self._loader and self._loader.isRunning():
            self._loader.quit()
            self._loader.wait(100)

        self._image_area.clear()
        self._spinner.setVisible(True)
        self._loader = _ImageLoader(row["file_path"], photo_id, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(lambda msg: self._spinner.setVisible(False))
        self._loader.start()

    def _on_loaded(self, qimg: QImage, photo_id: int) -> None:
        self._spinner.setVisible(False)
        if photo_id == self._photo_id:
            self._image_area.set_pixmap(QPixmap.fromImage(qimg))
        self._populate_filmstrip()

    def _update_filmstrip(self, photo_id: int) -> None:
        """Scroll filmstrip so the current photo is visible."""
        for i in range(self._filmstrip.count()):
            item = self._filmstrip.item(i)
            if item.data(_ROLE_ID) == photo_id:
                self._filmstrip.setCurrentItem(item)
                self._filmstrip.scrollToItem(item)
                break

    def _populate_filmstrip(self) -> None:
        """Populate filmstrip around current photo (lazy — only if empty)."""
        if self._filmstrip.count() == len(self._photo_ids):
            self._update_filmstrip(self._photo_id)
            return
        self._filmstrip.clear()
        conn = get_connection(self._catalog_path)
        # Load small thumbnails for all photos
        for pid in self._photo_ids:
            row = get_photo_by_id(conn, pid)
            item = QListWidgetItem()
            item.setData(_ROLE_ID, pid)
            item.setToolTip(row["filename"] if row else str(pid))
            if row and row["thumbnail_path"] and Path(row["thumbnail_path"]).exists():
                pix = QPixmap(row["thumbnail_path"]).scaled(
                    _Filmstrip.THUMB, _Filmstrip.THUMB,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                from PySide6.QtGui import QIcon
                item.setIcon(QIcon(pix))
            self._filmstrip.addItem(item)
        self._update_filmstrip(self._photo_id)

    def _on_filmstrip_click(self, item: QListWidgetItem) -> None:
        pid = item.data(_ROLE_ID)
        if pid and pid != self._photo_id:
            self._show_photo(pid)

    # ------------------------------------------------------------------
    def go_prev(self) -> None:
        if self._photo_id is None or not self._photo_ids:
            return
        idx = self._photo_ids.index(self._photo_id) if self._photo_id in self._photo_ids else 0
        if idx > 0:
            self._show_photo(self._photo_ids[idx - 1])

    def go_next(self) -> None:
        if self._photo_id is None or not self._photo_ids:
            return
        idx = self._photo_ids.index(self._photo_id) if self._photo_id in self._photo_ids else -1
        if idx < len(self._photo_ids) - 1:
            self._show_photo(self._photo_ids[idx + 1])

    def _rate(self, rating: int) -> None:
        if self._photo_id is None:
            return
        _meta.set_rating(self._photo_id, rating, self._catalog_path)
        self._show_photo(self._photo_id)  # refresh status label

    def _flag(self, flag: int) -> None:
        if self._photo_id is None:
            return
        _meta.set_flag(self._photo_id, flag, self._catalog_path)
        self._show_photo(self._photo_id)

    def _add_to_collection(self) -> None:
        if self._photo_id is None:
            return
        from ui.collectiondialog import CollectionPickerDialog
        dlg = CollectionPickerDialog(self._catalog_path, self)
        if dlg.exec() and dlg.chosen_id is not None:
            add_photo(dlg.chosen_id, self._photo_id, self._catalog_path)
            self.collection_changed.emit()

    # ------------------------------------------------------------------
    def set_filter(self, filter_ctx: dict) -> None:
        """Called when the sidebar filter changes while in cull mode."""
        self._filter_ctx = filter_ctx
        self._photo_ids.clear()
        self._filmstrip.clear()
        self._image_area.clear()
        self._load_photo_list()
