"""Cull/review mode: step through photos, rate, flag, with always-on similarity panel."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget,
)
from core.db.catalog import get_connection
from core.logger import get_logger
from core import metadata as _meta
from core.collections import add_photo
from core.query import get_photos, get_photo_by_id

log = get_logger("picurate.cullview")
_ROLE_ID    = Qt.UserRole
_ROLE_SCORE = Qt.UserRole + 1


class _ImageLoader(QThread):
    loaded = Signal(QImage, int)
    failed = Signal(str)
    MAX_DIM = 2400
    def __init__(self, file_path, photo_id, parent=None):
        super().__init__(parent)
        self._path = file_path
        self._photo_id = photo_id
    def run(self):
        try:
            from PIL import Image, ImageOps
            img = Image.open(self._path)
            img = ImageOps.exif_transpose(img)
            img.thumbnail((self.MAX_DIM, self.MAX_DIM), Image.LANCZOS)
            img = img.convert("RGB")
            raw = img.tobytes()
            qimg = QImage(raw, img.width, img.height, img.width * 3,
                          QImage.Format.Format_RGB888).copy()
            self.loaded.emit(qimg, self._photo_id)
        except Exception as exc:
            self.failed.emit(str(exc))


class _SimilarLoader(QThread):
    found = Signal(list, int)
    def __init__(self, photo_id, catalog_path, parent=None):
        super().__init__(parent)
        self._photo_id = photo_id
        self._catalog_path = catalog_path
    def run(self):
        try:
            from core.similar import find_similar
            results = find_similar(self._photo_id, self._catalog_path, limit=12)
            self.found.emit(results, self._photo_id)
        except Exception as exc:
            log.debug("similar search failed: %s", exc)
            self.found.emit([], self._photo_id)


class _ImageArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setWidget(self._label)
        self._pixmap = None
        self.setStyleSheet("background: #111;")
    def set_pixmap(self, pix):
        self._pixmap = pix
        self._fit()
    def _fit(self):
        if self._pixmap is None:
            return
        vp = self.viewport().size()
        scaled = self._pixmap.scaled(vp, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
        self._label.setPixmap(scaled)
        self._label.resize(scaled.size())
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()
    def clear(self):
        self._pixmap = None
        self._label.clear()


class _Filmstrip(QListWidget):
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


class _SimilarPanel(QWidget):
    compare_with = Signal(int)
    navigate_to  = Signal(int)
    THUMB = 110
    def __init__(self, catalog_path, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self.setMinimumWidth(200)
        self.setMaximumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self._header = QLabel("Similar")
        self._header.setStyleSheet("font-weight: bold; font-size: 12px;")
        layout.addWidget(self._header)
        self._spinner = QProgressBar()
        self._spinner.setRange(0, 0)
        self._spinner.setFixedHeight(4)
        self._spinner.setTextVisible(False)
        layout.addWidget(self._spinner)
        self._empty = QLabel("No similar photos")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("color: #888; font-size: 11px;")
        self._empty.setVisible(False)
        layout.addWidget(self._empty)
        self._list = QListWidget()
        from PySide6.QtWidgets import QListView
        self._list.setViewMode(QListView.ViewMode.IconMode)
        self._list.setIconSize(QSize(self.THUMB, self.THUMB))
        self._list.setMovement(QListView.Movement.Static)
        self._list.setResizeMode(QListView.ResizeMode.Adjust)
        self._list.setSpacing(4)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_ctx)
        self._list.itemDoubleClicked.connect(lambda it: self.compare_with.emit(it.data(_ROLE_ID)))
        self._list.setToolTip("Double-click: compare side-by-side\nRight-click: options")
        layout.addWidget(self._list, stretch=1)
        hint = QLabel("Double-click to compare")
        hint.setStyleSheet("color: #666; font-size: 10px;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
    def set_searching(self):
        self._header.setText("Similar  (searching...)")
        self._spinner.setVisible(True)
        self._empty.setVisible(False)
        self._list.clear()
    def populate(self, results, current_photo_id):
        self._spinner.setVisible(False)
        self._list.clear()
        if not results:
            self._header.setText("Similar  (none found)")
            self._empty.setVisible(True)
            return
        self._empty.setVisible(False)
        self._header.setText(f"Similar  ({len(results)})")
        conn = get_connection(self._catalog_path)
        for r in results:
            pid = r["id"]
            row = get_photo_by_id(conn, pid)
            if row is None:
                continue
            item = QListWidgetItem()
            item.setData(_ROLE_ID, pid)
            score = r.get("score", r.get("distance", 0))
            item.setData(_ROLE_SCORE, score)
            if row["thumbnail_path"] and Path(row["thumbnail_path"]).exists():
                pix = QPixmap(row["thumbnail_path"]).scaled(
                    self.THUMB, self.THUMB,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                item.setIcon(QIcon(pix))
            rating = row["rating"] or 0
            stars = "\u2605" * rating if rating else ""
            fname = (row["filename"] or "")[:18]
            if "score" in r:
                tip = f"{fname}\n{stars}\nSimilarity: {int(score*100)}%"
            else:
                tip = f"{fname}\n{stars}\nDistance: {score}"
            item.setToolTip(tip)
            item.setText(stars)
            self._list.addItem(item)
    def _on_ctx(self, pos):
        item = self._list.itemAt(pos)
        if item is None:
            return
        pid = item.data(_ROLE_ID)
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("Compare side-by-side", lambda: self.compare_with.emit(pid))
        menu.addAction("Navigate to this photo", lambda: self.navigate_to.emit(pid))
        menu.exec(self._list.viewport().mapToGlobal(pos))


class CullView(QWidget):
    exit_requested    = Signal()
    photo_changed     = Signal(int)
    collection_changed = Signal()

    def __init__(self, catalog_path, filter_ctx, parent=None):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self._filter_ctx = filter_ctx
        self._photo_id = None
        self._photo_ids = []
        self._loader = None
        self._sim_loader = None
        self._fullscreen = False
        self._build_ui()
        self._setup_shortcuts()
        self._load_photo_list()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QWidget()
        top.setFixedHeight(36)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(8, 4, 8, 4)
        self._info_label = QLabel("Cull mode")
        tl.addWidget(self._info_label)
        tl.addStretch()
        hint = QLabel("1-5 rate | P pick | X reject | U unflag | LR navigate | F fullscreen")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        tl.addWidget(hint)
        tl.addStretch()
        self._fs_btn = QPushButton("Fullscreen [F]")
        self._fs_btn.setFixedWidth(118)
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        tl.addWidget(self._fs_btn)
        exit_btn = QPushButton("Exit Cull Mode")
        exit_btn.clicked.connect(self.exit_requested)
        tl.addWidget(exit_btn)
        root.addWidget(top)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(4)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)

        self._stack = QStackedWidget()

        # Page 0 single view
        single_page = QWidget()
        sl = QVBoxLayout(single_page)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self._image_area = _ImageArea()
        sl.addWidget(self._image_area, stretch=1)
        self._spinner = QProgressBar()
        self._spinner.setRange(0, 0)
        self._spinner.setFixedHeight(3)
        self._spinner.setTextVisible(False)
        self._spinner.setVisible(False)
        sl.addWidget(self._spinner)
        self._stack.addWidget(single_page)

        # Page 1 compare
        cmp_page = QWidget()
        cml = QVBoxLayout(cmp_page)
        cml.setContentsMargins(0, 0, 0, 0)
        cml.setSpacing(0)
        self._cmp_label_a = QLabel()
        self._cmp_label_a.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cmp_label_b = QLabel()
        self._cmp_label_b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_row = QHBoxLayout()
        lbl_row.addWidget(self._cmp_label_a, stretch=1)
        lbl_row.addWidget(self._cmp_label_b, stretch=1)
        cml.addLayout(lbl_row)
        cmp_split = QSplitter(Qt.Orientation.Horizontal)
        self._cmp_area_a = _ImageArea()
        self._cmp_area_b = _ImageArea()
        cmp_split.addWidget(self._cmp_area_a)
        cmp_split.addWidget(self._cmp_area_b)
        cml.addWidget(cmp_split, stretch=1)
        back_btn = QPushButton("<- Back to single view  [Esc]")
        back_btn.clicked.connect(self._exit_compare)
        back_row = QHBoxLayout()
        back_row.addWidget(back_btn)
        back_row.addStretch()
        cml.addLayout(back_row)
        self._stack.addWidget(cmp_page)

        ll.addWidget(self._stack, stretch=1)

        ctrl = QWidget()
        ctrl.setFixedHeight(44)
        cl = QHBoxLayout(ctrl)
        cl.setContentsMargins(8, 4, 8, 4)
        cl.setSpacing(4)
        prev_btn = QPushButton("<")
        prev_btn.setFixedWidth(30)
        prev_btn.clicked.connect(self.go_prev)
        cl.addWidget(prev_btn)
        next_btn = QPushButton(">")
        next_btn.setFixedWidth(30)
        next_btn.clicked.connect(self.go_next)
        cl.addWidget(next_btn)
        cl.addSpacing(6)
        STAR = "\u2605"
        for i in range(1, 6):
            btn = QPushButton(STAR * i)
            btn.setFixedWidth(30 + i * 6)
            btn.clicked.connect(lambda _, r=i: self._rate(r))
            cl.addWidget(btn)
        clr = QPushButton("x")
        clr.setFixedWidth(24)
        clr.setToolTip("Clear rating (0)")
        clr.clicked.connect(lambda: self._rate(0))
        cl.addWidget(clr)
        cl.addSpacing(6)
        for label, flag in [("Pick P", _meta.FLAG_PICK), ("Reject X", _meta.FLAG_REJECT), ("Unflag U", _meta.FLAG_NONE)]:
            b = QPushButton(label)
            b.setFixedWidth(74)
            b.clicked.connect(lambda _, f=flag: self._flag(f))
            cl.addWidget(b)
        cl.addSpacing(6)
        col_btn = QPushButton("+ Collection")
        col_btn.clicked.connect(self._add_to_collection)
        cl.addWidget(col_btn)
        cl.addStretch()
        self._status_label = QLabel("")
        self._status_label.setMinimumWidth(180)
        cl.addWidget(self._status_label)
        ll.addWidget(ctrl)

        self._splitter.addWidget(left)

        self._similar_panel = _SimilarPanel(self._catalog_path, self)
        self._similar_panel.compare_with.connect(self._enter_compare)
        self._similar_panel.navigate_to.connect(self._show_photo)
        self._splitter.addWidget(self._similar_panel)
        self._splitter.setStretchFactor(0, 7)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setSizes([900, 260])

        root.addWidget(self._splitter, stretch=1)

        self._filmstrip = _Filmstrip()
        self._filmstrip.itemClicked.connect(self._on_filmstrip_click)
        root.addWidget(self._filmstrip)

    def _setup_shortcuts(self):
        def sc(key, slot):
            QShortcut(QKeySequence(key), self).activated.connect(slot)
        sc(Qt.Key.Key_Left,  self.go_prev)
        sc(Qt.Key.Key_Right, self.go_next)
        sc(Qt.Key.Key_Space, self.go_next)
        for i in range(1, 6):
            sc(getattr(Qt.Key, f"Key_{i}"), lambda _, r=i: self._rate(r))
        sc(Qt.Key.Key_0, lambda: self._rate(0))
        sc(Qt.Key.Key_P, lambda: self._flag(_meta.FLAG_PICK))
        sc(Qt.Key.Key_X, lambda: self._flag(_meta.FLAG_REJECT))
        sc(Qt.Key.Key_U, lambda: self._flag(_meta.FLAG_NONE))
        sc(Qt.Key.Key_C, self._add_to_collection)
        sc(Qt.Key.Key_F11, self._toggle_fullscreen)
        sc(Qt.Key.Key_F,   self._toggle_fullscreen)
        sc(Qt.Key.Key_Escape, self._on_escape)

    def _toggle_fullscreen(self):
        win = self.window()
        if self._fullscreen:
            self._fullscreen = False
            self._fs_btn.setText("Fullscreen [F]")
            win.showNormal()
            self._restore_panels()
        else:
            self._fullscreen = True
            self._fs_btn.setText("Exit Fullscreen")
            self._hide_panels()
            win.showFullScreen()

    def _hide_panels(self):
        win = self.window()
        try:
            win.menuBar().hide()
            from PySide6.QtWidgets import QToolBar
            for tb in win.findChildren(QToolBar):
                tb.hide()
            win.statusBar().hide()
            if hasattr(win, "_sidebar"): win._sidebar.hide()
            if hasattr(win, "_props"):   win._props.hide()
        except Exception:
            pass

    def _restore_panels(self):
        win = self.window()
        try:
            win.menuBar().show()
            from PySide6.QtWidgets import QToolBar
            for tb in win.findChildren(QToolBar):
                tb.show()
            win.statusBar().show()
            if hasattr(win, "_sidebar") and hasattr(win, "_act_sidebar"):
                win._sidebar.setVisible(win._act_sidebar.isChecked())
            if hasattr(win, "_props") and hasattr(win, "_act_props"):
                win._props.setVisible(win._act_props.isChecked())
        except Exception:
            pass

    def _on_escape(self):
        if self._fullscreen:
            self._toggle_fullscreen()
        elif self._stack.currentIndex() == 1:
            self._exit_compare()

    def _enter_compare(self, other_photo_id):
        if self._photo_id is None:
            return
        self._stack.setCurrentIndex(1)
        conn = get_connection(self._catalog_path)
        row_a = get_photo_by_id(conn, self._photo_id)
        row_b = get_photo_by_id(conn, other_photo_id)
        def fmt(row):
            if not row: return ""
            r = row["rating"] or 0
            return f"{row['filename'] or ''}"
        self._cmp_label_a.setText(fmt(row_a))
        self._cmp_label_b.setText(fmt(row_b))
        self._cmp_area_a.clear()
        self._cmp_area_b.clear()
        if row_a:
            la = _ImageLoader(row_a["file_path"], self._photo_id, self)
            la.loaded.connect(lambda img, _, a=self._cmp_area_a: a.set_pixmap(QPixmap.fromImage(img)))
            la.start()
        if row_b:
            lb = _ImageLoader(row_b["file_path"], other_photo_id, self)
            lb.loaded.connect(lambda img, _, a=self._cmp_area_b: a.set_pixmap(QPixmap.fromImage(img)))
            lb.start()

    def _exit_compare(self):
        self._stack.setCurrentIndex(0)

    def _load_photo_list(self):
        conn = get_connection(self._catalog_path)
        rows = get_photos(conn,
            folder=self._filter_ctx.get("folder"), year=self._filter_ctx.get("year"),
            month=self._filter_ctx.get("month"), rating_min=self._filter_ctx.get("rating_min"),
            flag=self._filter_ctx.get("flag"), search=self._filter_ctx.get("search"),
            collection_id=self._filter_ctx.get("collection_id"),
            place_id=self._filter_ctx.get("place_id"), trip_id=self._filter_ctx.get("trip_id"),
            person_id=self._filter_ctx.get("person_id"), tag=self._filter_ctx.get("tag"),
            limit=5000)
        self._photo_ids = [r["id"] for r in rows]
        total = len(self._photo_ids)
        self._info_label.setText(f"Cull mode - {total} photo{'s' if total != 1 else ''}")
        if self._photo_ids:
            self._show_photo(self._photo_ids[0])

    def _show_photo(self, photo_id):
        self._photo_id = photo_id
        if self._stack.currentIndex() == 1:
            self._exit_compare()
        conn = get_connection(self._catalog_path)
        row = get_photo_by_id(conn, photo_id)
        if row is None:
            return
        rating = row["rating"] or 0
        flag   = row["flag"] or 0
        STAR = "\u2605"; EMPTY = "\u2606"
        stars = STAR * rating + EMPTY * (5 - rating) if rating else EMPTY * 5
        flag_str = {0: "-", 1: "Picked", 2: "Rejected"}.get(flag, "")
        self._status_label.setText(f"{stars}  {flag_str}")
        self.photo_changed.emit(photo_id)
        self._update_filmstrip(photo_id)
        if self._loader and self._loader.isRunning():
            self._loader.quit(); self._loader.wait(80)
        self._image_area.clear()
        self._spinner.setVisible(True)
        self._loader = _ImageLoader(row["file_path"], photo_id, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(lambda _: self._spinner.setVisible(False))
        self._loader.start()
        self._similar_panel.set_searching()
        if self._sim_loader and self._sim_loader.isRunning():
            self._sim_loader.quit(); self._sim_loader.wait(80)
        self._sim_loader = _SimilarLoader(photo_id, self._catalog_path, self)
        self._sim_loader.found.connect(self._on_similar_found)
        self._sim_loader.start()

    def _on_loaded(self, qimg, photo_id):
        self._spinner.setVisible(False)
        if photo_id == self._photo_id:
            self._image_area.set_pixmap(QPixmap.fromImage(qimg))
        self._populate_filmstrip()

    def _on_similar_found(self, results, photo_id):
        if photo_id == self._photo_id:
            self._similar_panel.populate(results, photo_id)

    def _update_filmstrip(self, photo_id):
        for i in range(self._filmstrip.count()):
            item = self._filmstrip.item(i)
            if item.data(_ROLE_ID) == photo_id:
                self._filmstrip.setCurrentItem(item)
                self._filmstrip.scrollToItem(item)
                break

    def _populate_filmstrip(self):
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
                    Qt.TransformationMode.SmoothTransformation)
                item.setIcon(QIcon(pix))
            self._filmstrip.addItem(item)
        self._update_filmstrip(self._photo_id)

    def _on_filmstrip_click(self, item):
        pid = item.data(_ROLE_ID)
        if pid and pid != self._photo_id:
            self._show_photo(pid)

    def go_prev(self):
        if not self._photo_id or not self._photo_ids: return
        idx = self._photo_ids.index(self._photo_id) if self._photo_id in self._photo_ids else 0
        if idx > 0: self._show_photo(self._photo_ids[idx - 1])

    def go_next(self):
        if not self._photo_id or not self._photo_ids: return
        idx = self._photo_ids.index(self._photo_id) if self._photo_id in self._photo_ids else -1
        if idx < len(self._photo_ids) - 1: self._show_photo(self._photo_ids[idx + 1])

    def _rate(self, rating):
        if self._photo_id is None: return
        _meta.set_rating(self._photo_id, rating, self._catalog_path)
        self._show_photo(self._photo_id)

    def _flag(self, flag):
        if self._photo_id is None: return
        _meta.set_flag(self._photo_id, flag, self._catalog_path)
        self._show_photo(self._photo_id)

    def _add_to_collection(self):
        if self._photo_id is None: return
        from ui.collectiondialog import CollectionPickerDialog
        dlg = CollectionPickerDialog(self._catalog_path, self)
        if dlg.exec() and dlg.chosen_id is not None:
            add_photo(dlg.chosen_id, self._photo_id, self._catalog_path)
            self.collection_changed.emit()

    def set_filter(self, filter_ctx):
        self._filter_ctx = filter_ctx
        self._photo_ids.clear()
        self._filmstrip.clear()
        self._image_area.clear()
        self._similar_panel.set_searching()
        self._load_photo_list()
