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

_ROLE_ID    = Qt.UserRole
_ROLE_SCORE = Qt.UserRole + 1


# ── Background image loader ────────────────────────────────────────────────────

class _ImageLoader(QThread):
    loaded = Signal(QImage, int)
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


# ── Background similarity search ──────────────────────────────────────────────

class _SimilarLoader(QThread):
    """Finds similar photos for a given photo_id in a background thread."""
    found = Signal(list, int)   # (results, photo_id)

    def __init__(self, photo_id: int, catalog_path: Path, parent=None):
        super().__init__(parent)
        self._photo_id = photo_id
        self._catalog_path = catalog_path

    def run(self) -> None:
        try:
            from core.similar import find_similar
            results = find_similar(self._photo_id, self._catalog_path, limit=10)
            self.found.emit(results, self._photo_id)
        except Exception as exc:
            log.debug("similar search failed: %s", exc)
            self.found.emit([], self._photo_id)


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
    """Horizontal strip of small thumbnails."""
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


# ── Side-by-side comparison dialog ───────────────────────────────────────────

class _CompareDialog(QWidget):
    """Floating side-by-side comparison of two photos."""

    def __init__(self, photo_id_a: int, photo_id_b: int, catalog_path: Path, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Compare")
        self.resize(1200, 700)
        self._catalog_path = catalog_path

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Info bar
        info_bar = QHBoxLayout()
        self._label_a = QLabel()
        self._label_b = QLabel()
        self._label_a.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label_b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_bar.addWidget(self._label_a)
        info_bar.addWidget(self._label_b)
        layout.addLayout(info_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._area_a = _ImageArea()
        self._area_b = _ImageArea()
        splitter.addWidget(self._area_a)
        splitter.addWidget(self._area_b)
        layout.addWidget(splitter, stretch=1)

        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.close)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(close_btn)
        layout.addLayout(h)

        self._load(photo_id_a, self._area_a, self._label_a)
        self._load(photo_id_b, self._area_b, self._label_b)

    def _load(self, photo_id: int, area: _ImageArea, lbl: QLabel) -> None:
        conn = get_connection(self._catalog_path)
        row = get_photo_by_id(conn, photo_id)
        if row is None:
            return
        name = row["filename"] or str(photo_id)
        rating = row["rating"] or 0
        stars = ("★" * rating) if rating else "—"
        lbl.setText(f"{name}  {stars}")

        loader = _ImageLoader(row["file_path"], photo_id, self)
        loader.loaded.connect(lambda img, pid, a=area: a.set_pixmap(QPixmap.fromImage(img)))
        loader.start()


# ── Similar photos strip ──────────────────────────────────────────────────────

class _SimilarStrip(QWidget):
    """
    Horizontal strip shown below the main image when similar photos exist.
    Clicking a thumbnail navigates to that photo; right-click offers comparison.
    """
    navigate_to = Signal(int)    # photo_id to navigate to
    compare_with = Signal(int)   # photo_id to compare side-by-side

    THUMB = 88

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        header = QHBoxLayout()
        self._label = QLabel("Similar photos:")
        self._label.setStyleSheet("color: #aaa; font-size: 11px; font-weight: bold;")
        header.addWidget(self._label)
        header.addStretch()
        hide_btn = QPushButton("✕")
        hide_btn.setFixedSize(20, 20)
        hide_btn.setFlat(True)
        hide_btn.setStyleSheet("color: #888;")
        hide_btn.clicked.connect(lambda: self.setVisible(False))
        header.addWidget(hide_btn)
        layout.addLayout(header)

        self._list = QListWidget()
        from PySide6.QtWidgets import QListView
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setIconSize(QSize(self.THUMB, self.THUMB))
        self._list.setFixedHeight(self.THUMB + 20)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setMovement(QListView.Movement.Static)
        self._list.setSpacing(4)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_ctx)
        self._list.itemDoubleClicked.connect(
            lambda it: self.navigate_to.emit(it.data(_ROLE_ID))
        )
        layout.addWidget(self._list)

        self.setFixedHeight(self.THUMB + 48)

    def populate(self, results: list, current_photo_id: int, catalog_path: Path) -> None:
        self._current_photo_id = current_photo_id
        self._catalog_path = catalog_path
        self._list.clear()
        if not results:
            self.setVisible(False)
            return

        conn = get_connection(catalog_path)
        n = len(results)
        self._label.setText(f"Similar: {n} photo{'s' if n != 1 else ''} — double-click to navigate, right-click to compare")
        for r in results:
            pid = r["id"]
            score = r.get("score", r.get("distance", 0))
            row = get_photo_by_id(conn, pid)
            if row is None:
                continue

            item = QListWidgetItem()
            item.setData(_ROLE_ID, pid)
            item.setData(_ROLE_SCORE, score)

            # Load thumbnail
            if row["thumbnail_path"] and Path(row["thumbnail_path"]).exists():
                from PySide6.QtGui import QIcon
                pix = QPixmap(row["thumbnail_path"]).scaled(
                    self.THUMB, self.THUMB,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                item.setIcon(QIcon(pix))

            fname = row["filename"] or str(pid)
            rating = row["rating"] or 0
            item.setText("★" * rating if rating else "")
            item.setToolTip(f"{fname}\n{'Score' if 'score' in r else 'Distance'}: {score}")
            self._list.addItem(item)

        self.setVisible(True)

    def clear(self) -> None:
        self._list.clear()
        self.setVisible(False)

    def _on_ctx(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        pid = item.data(_ROLE_ID)
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("Navigate to this photo", lambda: self.navigate_to.emit(pid))
        menu.addAction("Compare side-by-side", lambda: self.compare_with.emit(pid))
        menu.exec(self._list.viewport().mapToGlobal(pos))


# ── Main Cull View widget ─────────────────────────────────────────────────────

class CullView(QWidget):
    """Full-pane single-photo review widget."""

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
        self._sim_loader: _SimilarLoader | None = None
        self._compare_dlg: _CompareDialog | None = None

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

        # ── Similar photos strip ──────────────────────────────────────
        self._similar_strip = _SimilarStrip()
        self._similar_strip.navigate_to.connect(self._show_photo)
        self._similar_strip.compare_with.connect(self._compare_with)
        root.addWidget(self._similar_strip)

        # ── Controls bar ──────────────────────────────────────────────
        ctrl_bar = QWidget()
        ctrl_bar.setFixedHeight(44)
        ctrl_layout = QHBoxLayout(ctrl_bar)
        ctrl_layout.setContentsMargins(8, 4, 8, 4)
        ctrl_layout.setSpacing(4)

        prev_btn = QPushButton("◀ Prev")
        prev_btn.clicked.connect(self.go_prev)
        ctrl_layout.addWidget(prev_btn)

        next_btn = QPushButton("Next ▶")
        next_btn.clicked.connect(self.go_next)
        ctrl_layout.addWidget(next_btn)

        ctrl_layout.addSpacing(16)

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

        col_btn = QPushButton("+ Collection (C)")
        col_btn.clicked.connect(self._add_to_collection)
        ctrl_layout.addWidget(col_btn)

        ctrl_layout.addStretch()

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

        rating = row["rating"] or 0
        flag = row["flag"] or 0
        stars = "★" * rating + "☆" * (5 - rating) if rating else "☆☆☆☆☆"
        flag_str = {0: "—", 1: "✓ Picked", 2: "✗ Rejected"}.get(flag, "")
        self._status_label.setText(f"{stars}  {flag_str}")

        self.photo_changed.emit(photo_id)
        self._update_filmstrip(photo_id)

        # Load image
        if self._loader and self._loader.isRunning():
            self._loader.quit()
            self._loader.wait(100)

        self._image_area.clear()
        self._spinner.setVisible(True)
        self._loader = _ImageLoader(row["file_path"], photo_id, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(lambda msg: self._spinner.setVisible(False))
        self._loader.start()

        # Kick off similarity search in background
        self._similar_strip.clear()
        if self._sim_loader and self._sim_loader.isRunning():
            self._sim_loader.quit()
            self._sim_loader.wait(100)
        self._sim_loader = _SimilarLoader(photo_id, self._catalog_path, self)
        self._sim_loader.found.connect(self._on_similar_found)
        self._sim_loader.start()

    def _on_loaded(self, qimg: QImage, photo_id: int) -> None:
        self._spinner.setVisible(False)
        if photo_id == self._photo_id:
            self._image_area.set_pixmap(QPixmap.fromImage(qimg))
        self._populate_filmstrip()

    def _on_similar_found(self, results: list, photo_id: int) -> None:
        if photo_id == self._photo_id:
            self._similar_strip.populate(results, photo_id, self._catalog_path)

    def _compare_with(self, other_photo_id: int) -> None:
        if self._photo_id is None:
            return
        if self._compare_dlg is not None:
            self._compare_dlg.close()
        self._compare_dlg = _CompareDialog(
            self._photo_id, other_photo_id, self._catalog_path, self
        )
        self._compare_dlg.show()

    def _update_filmstrip(self, photo_id: int) -> None:
        for i in range(self._filmstrip.count()):
            item = self._filmstrip.item(i)
            if item.data(_ROLE_ID) == photo_id:
                self._filmstrip.setCurrentItem(item)
                self._filmstrip.scrollToItem(item)
                break

    def _populate_filmstrip(self) -> None:
        if self._filmstrip.count() == len(self._photo_ids):
            self._update_filmstrip(self._photo_id)
            return
        self._filmstrip.clear()
        conn = get_connection(self._catalog_path)
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
        self._show_photo(self._photo_id)

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
        self._filter_ctx = filter_ctx
        self._photo_ids.clear()
        self._filmstrip.clear()
        self._image_area.clear()
        self._similar_strip.clear()
        self._load_photo_list()
