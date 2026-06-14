"""Application settings dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from core import settings as _settings


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        # ── Appearance ────────────────────────────────────────────────
        appearance_group = QGroupBox("Appearance")
        form = QFormLayout(appearance_group)

        current_font = QApplication.font()
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(8, 22)
        self._font_size_spin.setValue(current_font.pointSize())
        self._font_size_spin.setSuffix(" pt")
        self._font_size_spin.valueChanged.connect(self._preview_font)
        form.addRow("Font size:", self._font_size_spin)

        self._font_preview = QLabel("The quick brown fox jumps over the lazy dog.")
        self._font_preview.setWordWrap(True)
        form.addRow("Preview:", self._font_preview)

        layout.addWidget(appearance_group)

        # ── Thumbnail defaults ────────────────────────────────────────
        thumb_group = QGroupBox("Thumbnails")
        thumb_form = QFormLayout(thumb_group)

        catalog_path = parent._catalog_path if hasattr(parent, "_catalog_path") else None
        saved_thumb = 200
        if catalog_path:
            saved_thumb = _settings.get("thumb_default_size", catalog_path) or 200

        self._thumb_slider = QSlider(Qt.Orientation.Horizontal)
        self._thumb_slider.setRange(64, 384)
        self._thumb_slider.setValue(int(saved_thumb))
        self._thumb_label = QLabel(f"{int(saved_thumb)} px")
        self._thumb_slider.valueChanged.connect(
            lambda v: self._thumb_label.setText(f"{v} px")
        )
        thumb_form.addRow("Default size:", self._thumb_slider)
        thumb_form.addRow("", self._thumb_label)

        layout.addWidget(thumb_group)

        # ── Face clustering ───────────────────────────────────────────
        face_group = QGroupBox("Face Clustering")
        face_form = QFormLayout(face_group)

        saved_thresh = 0.50
        if catalog_path:
            saved_thresh = _settings.get("face_cluster_threshold", catalog_path) or 0.50

        self._face_thresh_slider = QSlider(Qt.Orientation.Horizontal)
        self._face_thresh_slider.setRange(30, 80)
        self._face_thresh_slider.setValue(int(float(saved_thresh) * 100))
        self._face_thresh_label = QLabel(f"{float(saved_thresh):.2f}")
        self._face_thresh_slider.valueChanged.connect(
            lambda v: self._face_thresh_label.setText(f"{v/100:.2f}")
        )
        face_form.addRow("Similarity threshold:", self._face_thresh_slider)
        face_form.addRow("  (higher = fewer, stricter groups)", self._face_thresh_label)

        layout.addWidget(face_group)

        # ── Buttons ───────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._catalog_path = catalog_path

    def _preview_font(self, size: int) -> None:
        f = QApplication.font()
        f.setPointSize(size)
        self._font_preview.setFont(f)

    def _on_accept(self) -> None:
        font_size = self._font_size_spin.value()
        f = QApplication.font()
        f.setPointSize(font_size)
        QApplication.setFont(f)

        if self._catalog_path:
            _settings.set_("font_size", font_size, self._catalog_path)
            _settings.set_("thumb_default_size", self._thumb_slider.value(), self._catalog_path)
            _settings.set_(
                "face_cluster_threshold",
                self._face_thresh_slider.value() / 100.0,
                self._catalog_path,
            )

        self.accept()
