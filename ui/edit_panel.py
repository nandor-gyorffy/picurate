"""EditPanel — non-destructive photo edit widget (crop, rotate, adjust)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QRect, QPoint, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen, QBrush, QImage
from PySide6.QtWidgets import (
    QWidget,
    QTabWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSizePolicy,
)


# ── Crop canvas ───────────────────────────────────────────────────────────────

_HANDLE_RADIUS = 6      # half-side of a handle square, in pixels
_HANDLE_HIT    = 12     # hit-test radius for a handle


class _CropCanvas(QWidget):
    """Displays the photo with a draggable crop-rect overlay."""

    crop_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(200, 150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

        self._pixmap: QPixmap | None = None
        self._img_rect = QRect()          # where the image is drawn on canvas

        # Crop rect in *image-pixel* coordinates (within _img_rect)
        self._crop: QRect = QRect()       # empty = no crop set yet
        self._dragging: int = -1          # index of handle being dragged, or -1
        self._drag_start: QPoint = QPoint()
        self._drag_crop_start: QRect = QRect()
        self._drawing_new: bool = False
        self._draw_start: QPoint = QPoint()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._update_img_rect()
        self.reset_crop()
        self.update()

    def get_crop(self) -> tuple[float, float, float, float]:
        """Return (x, y, w, h) as fractions of the image size."""
        if not self._pixmap or self._crop.isEmpty():
            return (0.0, 0.0, 1.0, 1.0)
        iw = self._img_rect.width()
        ih = self._img_rect.height()
        if iw == 0 or ih == 0:
            return (0.0, 0.0, 1.0, 1.0)
        r = self._crop.normalized()
        x = (r.left()   - self._img_rect.left()) / iw
        y = (r.top()    - self._img_rect.top())  / ih
        w = r.width()  / iw
        h = r.height() / ih
        # Clamp
        x = max(0.0, min(x, 1.0))
        y = max(0.0, min(y, 1.0))
        w = max(0.01, min(w, 1.0 - x))
        h = max(0.01, min(h, 1.0 - y))
        return (x, y, w, h)

    def set_crop(self, x: float, y: float, w: float, h: float) -> None:
        """Restore from saved fractions."""
        if not self._pixmap or self._img_rect.isEmpty():
            return
        iw = self._img_rect.width()
        ih = self._img_rect.height()
        left   = self._img_rect.left()   + int(x * iw)
        top    = self._img_rect.top()    + int(y * ih)
        width  = int(w * iw)
        height = int(h * ih)
        self._crop = QRect(left, top, width, height)
        self.update()

    def reset_crop(self) -> None:
        """Reset to full image."""
        if self._img_rect.isEmpty():
            self._crop = QRect()
        else:
            self._crop = QRect(self._img_rect)
        self.update()

    # ── Layout helpers ────────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        old_crop_fracs = self.get_crop()
        self._update_img_rect()
        # Re-apply crop fractions in new coords
        self.set_crop(*old_crop_fracs)

    def _update_img_rect(self) -> None:
        if not self._pixmap:
            self._img_rect = QRect()
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        cw, ch = self.width(), self.height()
        if pw == 0 or ph == 0 or cw == 0 or ch == 0:
            self._img_rect = QRect()
            return
        scale = min(cw / pw, ch / ph)
        dw = int(pw * scale)
        dh = int(ph * scale)
        ox = (cw - dw) // 2
        oy = (ch - dh) // 2
        self._img_rect = QRect(ox, oy, dw, dh)

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))

        if not self._pixmap or self._img_rect.isEmpty():
            return

        # Draw full image dimmed
        p.setOpacity(0.4)
        p.drawPixmap(self._img_rect, self._pixmap)
        p.setOpacity(1.0)

        if not self._crop.isEmpty():
            crop = self._crop.normalized()

            # Clip to draw bright region inside crop
            p.setClipRect(crop)
            p.drawPixmap(self._img_rect, self._pixmap)
            p.setClipping(False)

            # White border
            pen = QPen(QColor(255, 255, 255), 1)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(crop)

            # Handles
            p.setBrush(QBrush(QColor(255, 255, 255)))
            for hx, hy in self._handle_centers(crop):
                p.drawRect(hx - _HANDLE_RADIUS, hy - _HANDLE_RADIUS,
                           _HANDLE_RADIUS * 2, _HANDLE_RADIUS * 2)

    def _handle_centers(self, r: QRect) -> list[tuple[int, int]]:
        l, t, rr, b = r.left(), r.top(), r.right(), r.bottom()
        mx = (l + rr) // 2
        my = (t + b)  // 2
        return [
            (l, t), (mx, t), (rr, t),
            (l, my),                    (rr, my),
            (l, b), (mx, b), (rr, b),
        ]

    def _hit_handle(self, pos: QPoint) -> int:
        """Return index (0-7) of nearest handle within hit distance, or -1."""
        if self._crop.isEmpty():
            return -1
        crop = self._crop.normalized()
        for i, (hx, hy) in enumerate(self._handle_centers(crop)):
            if abs(pos.x() - hx) <= _HANDLE_HIT and abs(pos.y() - hy) <= _HANDLE_HIT:
                return i
        return -1

    # ── Mouse ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        h = self._hit_handle(pos)
        if h >= 0:
            self._dragging = h
            self._drag_start = pos
            self._drag_crop_start = QRect(self._crop.normalized())
        else:
            # Start drawing new crop rect
            self._drawing_new = True
            self._draw_start = pos
            self._crop = QRect(pos, pos)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._drawing_new:
            self._crop = QRect(self._draw_start, pos).normalized()
            self._clamp_to_image()
            self.update()
        elif self._dragging >= 0:
            dx = pos.x() - self._drag_start.x()
            dy = pos.y() - self._drag_start.y()
            self._move_handle(self._dragging, dx, dy)
            self._clamp_to_image()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drawing_new = False
        self._dragging = -1
        # Ensure minimum size
        c = self._crop.normalized()
        if c.width() < 4 or c.height() < 4:
            self.reset_crop()
        else:
            self._crop = c
        self.crop_changed.emit()
        self.update()

    def _move_handle(self, handle: int, dx: int, dy: int) -> None:
        r = QRect(self._drag_crop_start)
        l, t, rr, b = r.left(), r.top(), r.right(), r.bottom()
        # Handles: 0=TL, 1=TM, 2=TR, 3=ML, 4=MR, 5=BL, 6=BM, 7=BR
        if handle in (0, 3, 5):    l  += dx
        if handle in (2, 4, 7):    rr += dx
        if handle in (0, 1, 2):    t  += dy
        if handle in (5, 6, 7):    b  += dy
        self._crop = QRect(QPoint(l, t), QPoint(rr, b)).normalized()

    def _clamp_to_image(self) -> None:
        if self._img_rect.isEmpty():
            return
        c = self._crop.normalized()
        l  = max(c.left(),   self._img_rect.left())
        t  = max(c.top(),    self._img_rect.top())
        rr = min(c.right(),  self._img_rect.right())
        b  = min(c.bottom(), self._img_rect.bottom())
        if rr > l and b > t:
            self._crop = QRect(QPoint(l, t), QPoint(rr, b))


# ── EditPanel ─────────────────────────────────────────────────────────────────

class EditPanel(QWidget):
    """Embeddable edit panel: Crop / Rotate / Adjust tabs."""

    edit_applied = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._catalog_path = None
        self._photo_id: int | None = None
        self._file_path: str | None = None
        self._rotate: int = 0          # 0 / 90 / 180 / 270

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        self._tabs.addTab(self._build_crop_tab(),   "Crop")
        self._tabs.addTab(self._build_rotate_tab(), "Rotate")
        self._tabs.addTab(self._build_adjust_tab(), "Adjust")

        # Bottom button row
        btn_row = QHBoxLayout()
        btn_apply = QPushButton("Apply")
        btn_reset = QPushButton("Reset All")
        btn_apply.clicked.connect(self._on_apply)
        btn_reset.clicked.connect(self._on_reset_all)
        btn_row.addStretch()
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_reset)
        root.addLayout(btn_row)

    def _build_crop_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        self._crop_canvas = _CropCanvas()
        lay.addWidget(self._crop_canvas, stretch=1)

        btn_reset_crop = QPushButton("Reset Crop")
        btn_reset_crop.clicked.connect(self._crop_canvas.reset_crop)
        lay.addWidget(btn_reset_crop)
        return w

    def _build_rotate_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)

        self._rotate_label = QLabel("Current angle: 0°")
        self._rotate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._rotate_label)

        grid = QGridLayout()
        btn_left  = QPushButton("↺ 90° Left")
        btn_right = QPushButton("↻ 90° Right")
        btn_180   = QPushButton("180°")
        btn_reset = QPushButton("Reset Rotation")

        btn_left.clicked.connect(lambda: self._rotate_by(-90))
        btn_right.clicked.connect(lambda: self._rotate_by(90))
        btn_180.clicked.connect(lambda: self._rotate_by(180))
        btn_reset.clicked.connect(self._reset_rotation)

        grid.addWidget(btn_left,  0, 0)
        grid.addWidget(btn_right, 0, 1)
        grid.addWidget(btn_180,   1, 0)
        grid.addWidget(btn_reset, 1, 1)
        lay.addLayout(grid)
        lay.addStretch()
        return w

    def _build_adjust_tab(self) -> QWidget:
        w = QWidget()
        lay = QGridLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setColumnStretch(1, 1)

        # Brightness
        self._sl_brightness = self._make_slider()
        self._lbl_brightness = QLabel("0")
        btn_rst_bright = QPushButton("Reset")
        lay.addWidget(QLabel("Brightness"), 0, 0)
        lay.addWidget(self._sl_brightness,  0, 1)
        lay.addWidget(self._lbl_brightness, 0, 2)
        lay.addWidget(btn_rst_bright,        0, 3)
        self._sl_brightness.valueChanged.connect(
            lambda v: self._lbl_brightness.setText(f"{v:+d}" if v else "0")
        )
        btn_rst_bright.clicked.connect(lambda: self._sl_brightness.setValue(0))

        # Contrast
        self._sl_contrast = self._make_slider()
        self._lbl_contrast = QLabel("0")
        btn_rst_contrast = QPushButton("Reset")
        lay.addWidget(QLabel("Contrast"), 1, 0)
        lay.addWidget(self._sl_contrast,  1, 1)
        lay.addWidget(self._lbl_contrast, 1, 2)
        lay.addWidget(btn_rst_contrast,   1, 3)
        self._sl_contrast.valueChanged.connect(
            lambda v: self._lbl_contrast.setText(f"{v:+d}" if v else "0")
        )
        btn_rst_contrast.clicked.connect(lambda: self._sl_contrast.setValue(0))

        # Saturation
        self._sl_saturation = self._make_slider()
        self._lbl_saturation = QLabel("0")
        btn_rst_sat = QPushButton("Reset")
        lay.addWidget(QLabel("Saturation"), 2, 0)
        lay.addWidget(self._sl_saturation,  2, 1)
        lay.addWidget(self._lbl_saturation, 2, 2)
        lay.addWidget(btn_rst_sat,          2, 3)
        self._sl_saturation.valueChanged.connect(
            lambda v: self._lbl_saturation.setText(f"{v:+d}" if v else "0")
        )
        btn_rst_sat.clicked.connect(lambda: self._sl_saturation.setValue(0))

        return w

    @staticmethod
    def _make_slider() -> QSlider:
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(-100, 100)
        sl.setValue(0)
        sl.setTickPosition(QSlider.TickPosition.NoTicks)
        return sl

    # ── Public API ────────────────────────────────────────────────────────────

    def load_photo(self, photo_id: int, file_path: str, catalog_path=None) -> None:
        """Load a photo into the panel.  Call before showing."""
        self._photo_id = photo_id
        self._file_path = file_path
        if catalog_path is not None:
            self._catalog_path = catalog_path

        # Load image for crop canvas
        try:
            from PIL import Image, ImageOps
            img = Image.open(file_path)
            img = ImageOps.exif_transpose(img)
            # Scale to fit 400×300
            img.thumbnail((400, 300), Image.LANCZOS)
            img = img.convert("RGB")
            qimg = QImage(
                img.tobytes("raw", "RGB"),
                img.width, img.height,
                img.width * 3,
                QImage.Format.Format_RGB888,
            )
            px = QPixmap.fromImage(qimg)
        except Exception:
            px = QPixmap(400, 300)
            px.fill(QColor(80, 80, 80))

        self._crop_canvas.load_pixmap(px)

        # Restore existing edits (if any)
        self._restore_from_catalog()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _restore_from_catalog(self) -> None:
        if self._photo_id is None or self._catalog_path is None:
            return
        from core.edits import get_edit
        edit = get_edit(self._photo_id, self._catalog_path)
        if edit is None:
            self._reset_ui()
            return

        # Crop
        self._crop_canvas.set_crop(
            edit["crop_x"], edit["crop_y"],
            edit["crop_w"], edit["crop_h"],
        )

        # Rotate
        self._rotate = int(edit.get("rotate", 0))
        self._update_rotate_label()

        # Adjust sliders (stored as fraction; slider stores int -100..+100 scaled by 100)
        # The stored value is a float like 0.23 meaning +23 on slider
        def _to_slider(v: float) -> int:
            return max(-100, min(100, int(round(v * 100))))

        self._sl_brightness.setValue(_to_slider(edit["brightness"]))
        self._sl_contrast.setValue(_to_slider(edit["contrast"]))
        self._sl_saturation.setValue(_to_slider(edit["saturation"]))

    def _reset_ui(self) -> None:
        self._crop_canvas.reset_crop()
        self._rotate = 0
        self._update_rotate_label()
        self._sl_brightness.setValue(0)
        self._sl_contrast.setValue(0)
        self._sl_saturation.setValue(0)

    def _rotate_by(self, deg: int) -> None:
        self._rotate = (self._rotate + deg) % 360
        self._update_rotate_label()

    def _reset_rotation(self) -> None:
        self._rotate = 0
        self._update_rotate_label()

    def _update_rotate_label(self) -> None:
        self._rotate_label.setText(f"Current angle: {self._rotate}°")

    def _on_apply(self) -> None:
        if self._photo_id is None or self._catalog_path is None:
            return

        from core.edits import set_edit

        cx, cy, cw, ch = self._crop_canvas.get_crop()

        # Convert slider int (-100..+100) → fraction
        def _from_slider(v: int) -> float:
            return v / 100.0

        set_edit(
            self._photo_id,
            self._catalog_path,
            crop_x=cx,
            crop_y=cy,
            crop_w=cw,
            crop_h=ch,
            rotate=self._rotate,
            brightness=_from_slider(self._sl_brightness.value()),
            contrast=_from_slider(self._sl_contrast.value()),
            saturation=_from_slider(self._sl_saturation.value()),
        )

        # Regenerate thumbnail
        if self._file_path:
            try:
                from core.thumbnails import get_thumbnail
                get_thumbnail(Path(self._file_path), force_regen=True)
            except Exception:
                pass

        self.edit_applied.emit()

    def _on_reset_all(self) -> None:
        if self._photo_id is None or self._catalog_path is None:
            return

        from core.edits import clear_edit
        clear_edit(self._photo_id, self._catalog_path)
        self._reset_ui()

        # Regenerate thumbnail
        if self._file_path:
            try:
                from core.thumbnails import get_thumbnail
                get_thumbnail(Path(self._file_path), force_regen=True)
            except Exception:
                pass

        self.edit_applied.emit()


# ── Thin dialog wrapper ───────────────────────────────────────────────────────

from PySide6.QtWidgets import QDialog  # noqa: E402 (appended after class)
from PySide6.QtCore import Signal as _Signal  # already imported as Signal above


class EditPanelDialog(QDialog):
    """Floating dialog wrapping EditPanel — opened from loupe or cull view."""

    edit_applied = _Signal()

    def __init__(
        self,
        photo_id: int,
        file_path: str,
        catalog_path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Photo")
        self.setMinimumSize(480, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._panel = EditPanel(self)
        self._panel.load_photo(photo_id, file_path, catalog_path)
        self._panel.edit_applied.connect(self.edit_applied)
        layout.addWidget(self._panel)
