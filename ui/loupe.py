"""Loupe (full-size) image viewer with prev/next navigation, zoom, and fullscreen."""
from __future__ import annotations

import io
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut, QWheelEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.db.catalog import get_connection
from core.query import get_adjacent_photo_ids, get_photo_by_id
from ui.propspanel import PropertiesPanel


# ── Background image loader ───────────────────────────────────────────────────

class _ImageLoader(QThread):
    loaded = Signal(QImage, str)   # (image, file_path)
    failed = Signal(str)

    MAX_DIM = 3000  # cap for display (keeps memory sane)

    def __init__(self, file_path: str, parent=None):
        super().__init__(parent)
        self._path = file_path

    def run(self) -> None:
        try:
            from PIL import Image, ImageOps
            img = Image.open(self._path)
            img = ImageOps.exif_transpose(img)   # always apply orientation
            img.thumbnail((self.MAX_DIM, self.MAX_DIM), Image.LANCZOS)
            img = img.convert("RGB")
            # Convert to QImage without a round-trip through a file
            raw = img.tobytes()
            qimg = QImage(raw, img.width, img.height, img.width * 3,
                          QImage.Format.Format_RGB888).copy()  # .copy() owns the buffer
            self.loaded.emit(qimg, self._path)
        except Exception as exc:
            self.failed.emit(str(exc))


# ── Zoomable image area ───────────────────────────────────────────────────────

class _ImageArea(QScrollArea):
    """Scrollable, zoomable image display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWidgetResizable(False)
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setWidget(self._label)
        self._pixmap: QPixmap | None = None
        self._scale = 1.0

    def set_pixmap(self, pix: QPixmap) -> None:
        self._pixmap = pix
        self._scale = 1.0
        self._fit_to_view()

    def _fit_to_view(self) -> None:
        if self._pixmap is None:
            return
        vp = self.viewport().size()
        pix_scaled = self._pixmap.scaled(
            vp, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # Update scale factor so wheel zoom is relative to fit size
        if self._pixmap.width() > 0:
            self._scale = pix_scaled.width() / self._pixmap.width()
        self._label.setPixmap(pix_scaled)
        self._label.resize(pix_scaled.size())

    def zoom(self, factor: float) -> None:
        if self._pixmap is None:
            return
        self._scale = max(0.05, min(self._scale * factor, 8.0))
        w = int(self._pixmap.width() * self._scale)
        h = int(self._pixmap.height() * self._scale)
        pix = self._pixmap.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(pix)
        self._label.resize(pix.size())

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.zoom(factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._pixmap and self._scale == self._scale:  # always True — triggers fit on resize
            self._fit_to_view()


# ── Loupe dialog ─────────────────────────────────────────────────────────────

class LoupeView(QDialog):
    def __init__(
        self,
        photo_id: int,
        catalog_path: Path,
        filter_ctx: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._catalog_path = catalog_path
        self._filter_ctx = filter_ctx or {}
        self._photo_id = photo_id
        self._loader: _ImageLoader | None = None
        self._is_fullscreen = False

        self.setWindowTitle("Picurate — Loupe")
        self.resize(1100, 780)
        self.setModal(False)

        self._build_ui()
        self._setup_shortcuts()
        self._load(photo_id)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Main splitter: image | props
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # Left: image + spinner
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self._image_area = _ImageArea()
        left_layout.addWidget(self._image_area)

        self._spinner = QProgressBar()
        self._spinner.setRange(0, 0)  # indeterminate
        self._spinner.setFixedHeight(4)
        self._spinner.setTextVisible(False)
        self._spinner.setVisible(False)
        left_layout.addWidget(self._spinner)

        self._error_label = QLabel()
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setVisible(False)
        left_layout.addWidget(self._error_label)

        splitter.addWidget(left)

        # Right: props panel
        self._props = PropertiesPanel()
        splitter.addWidget(self._props)
        splitter.setSizes([820, 240])

        # Bottom bar
        bar = QWidget()
        bar.setFixedHeight(40)
        bar.setStyleSheet("background:#1e1e1e;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 4, 8, 4)

        self._title_label = QLabel()
        self._title_label.setStyleSheet("color:#ccc; font-size:12px;")
        bar_layout.addWidget(self._title_label)
        bar_layout.addStretch()

        for label, slot in [("◀", self._go_prev), ("▶", self._go_next)]:
            btn = QPushButton(label)
            btn.setFixedWidth(36)
            btn.clicked.connect(slot)
            bar_layout.addWidget(btn)

        zoom_in = QPushButton("+")
        zoom_in.setFixedWidth(30)
        zoom_in.clicked.connect(lambda: self._image_area.zoom(1.25))
        bar_layout.addWidget(zoom_in)

        zoom_out = QPushButton("−")
        zoom_out.setFixedWidth(30)
        zoom_out.clicked.connect(lambda: self._image_area.zoom(1 / 1.25))
        bar_layout.addWidget(zoom_out)

        zoom_fit = QPushButton("Fit")
        zoom_fit.setFixedWidth(36)
        zoom_fit.clicked.connect(self._image_area._fit_to_view)
        bar_layout.addWidget(zoom_fit)

        fs_btn = QPushButton("⛶")
        fs_btn.setFixedWidth(30)
        fs_btn.setToolTip("Toggle fullscreen (F)")
        fs_btn.clicked.connect(self._toggle_fullscreen)
        bar_layout.addWidget(fs_btn)

        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(30)
        close_btn.clicked.connect(self.close)
        bar_layout.addWidget(close_btn)

        root.addWidget(bar)

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Left), self).activated.connect(self._go_prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self).activated.connect(self._go_next)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self).activated.connect(self.close)
        QShortcut(QKeySequence(Qt.Key.Key_F), self).activated.connect(self._toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Plus), self).activated.connect(
            lambda: self._image_area.zoom(1.25)
        )
        QShortcut(QKeySequence(Qt.Key.Key_Minus), self).activated.connect(
            lambda: self._image_area.zoom(1 / 1.25)
        )

    # ------------------------------------------------------------------
    def _load(self, photo_id: int) -> None:
        self._photo_id = photo_id
        conn = get_connection(self._catalog_path)
        row = get_photo_by_id(conn, photo_id)
        if row is None:
            return

        self._title_label.setText(f"{row['filename']}  —  {row['date_taken'] or ''}")
        self._props.show_photo(row)

        # Stop any running loader
        if self._loader and self._loader.isRunning():
            self._loader.quit()
            self._loader.wait(200)

        self._spinner.setVisible(True)
        self._error_label.setVisible(False)

        self._loader = _ImageLoader(row["file_path"], self)
        self._loader.loaded.connect(self._on_image_loaded)
        self._loader.failed.connect(self._on_image_failed)
        self._loader.start()

    def _on_image_loaded(self, qimg: QImage, _path: str) -> None:
        self._spinner.setVisible(False)
        self._image_area.set_pixmap(QPixmap.fromImage(qimg))

    def _on_image_failed(self, msg: str) -> None:
        self._spinner.setVisible(False)
        self._error_label.setText(f"Could not load image:\n{msg}")
        self._error_label.setVisible(True)

    # ------------------------------------------------------------------
    def _go_prev(self) -> None:
        conn = get_connection(self._catalog_path)
        prev_id, _ = get_adjacent_photo_ids(
            conn, self._photo_id,
            folder=self._filter_ctx.get("folder"),
            year=self._filter_ctx.get("year"),
            month=self._filter_ctx.get("month"),
        )
        if prev_id is not None:
            self._load(prev_id)

    def _go_next(self) -> None:
        conn = get_connection(self._catalog_path)
        _, next_id = get_adjacent_photo_ids(
            conn, self._photo_id,
            folder=self._filter_ctx.get("folder"),
            year=self._filter_ctx.get("year"),
            month=self._filter_ctx.get("month"),
        )
        if next_id is not None:
            self._load(next_id)

    def _toggle_fullscreen(self) -> None:
        if self._is_fullscreen:
            self.showNormal()
        else:
            self.showFullScreen()
        self._is_fullscreen = not self._is_fullscreen
