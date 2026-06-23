"""Application settings dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
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
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)

        catalog_path = parent._catalog_path if hasattr(parent, "_catalog_path") else None
        self._catalog_path = catalog_path

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

        saved_thumb = int(_settings.get("thumb_default_size", catalog_path) or 200)

        self._thumb_slider = QSlider(Qt.Orientation.Horizontal)
        self._thumb_slider.setRange(64, 384)
        self._thumb_slider.setValue(saved_thumb)
        self._thumb_label = QLabel(f"{saved_thumb} px")
        self._thumb_slider.valueChanged.connect(
            lambda v: self._thumb_label.setText(f"{v} px")
        )
        thumb_form.addRow("Default size:", self._thumb_slider)
        thumb_form.addRow("", self._thumb_label)

        layout.addWidget(thumb_group)

        # ── Photo similarity (cull mode) ──────────────────────────────
        sim_group = QGroupBox("Photo Similarity  (Cull Mode panel)")
        sim_form = QFormLayout(sim_group)

        saved_phash = int(_settings.get("phash_similarity_threshold", catalog_path) or 10)
        self._phash_slider = QSlider(Qt.Orientation.Horizontal)
        self._phash_slider.setRange(1, 30)
        self._phash_slider.setValue(saved_phash)
        self._phash_label = QLabel(f"{saved_phash}")
        self._phash_slider.valueChanged.connect(
            lambda v: self._phash_label.setText(str(v))
        )
        sim_form.addRow("pHash distance limit:", self._phash_slider)
        sim_form.addRow("  (lower = stricter match)", self._phash_label)

        saved_clip = float(_settings.get("clip_similarity_min_score", catalog_path) or 0.60)
        self._clip_slider = QSlider(Qt.Orientation.Horizontal)
        self._clip_slider.setRange(30, 95)
        self._clip_slider.setValue(int(saved_clip * 100))
        self._clip_label = QLabel(f"{saved_clip:.2f}")
        self._clip_slider.valueChanged.connect(
            lambda v: self._clip_label.setText(f"{v/100:.2f}")
        )
        sim_form.addRow("CLIP min score:", self._clip_slider)
        sim_form.addRow("  (higher = stricter CLIP match)", self._clip_label)

        layout.addWidget(sim_group)

        # ── Face recognition ──────────────────────────────────────────
        face_group = QGroupBox("Face Recognition")
        face_form = QFormLayout(face_group)

        saved_face_model = str(_settings.get("face_model", catalog_path) or "buffalo_sc")
        self._face_model_combo = QComboBox()
        self._face_model_combo.addItem("buffalo_sc  (fast, ~170 MB)", "buffalo_sc")
        self._face_model_combo.addItem("buffalo_l  (accurate, ~500 MB)", "buffalo_l")
        idx = self._face_model_combo.findData(saved_face_model)
        if idx >= 0:
            self._face_model_combo.setCurrentIndex(idx)
        model_note = QLabel(
            "buffalo_l gives noticeably better accuracy for recognition.\n"
            "Model downloads automatically on first use."
        )
        model_note.setWordWrap(True)
        model_note.setStyleSheet("color: #888; font-size: 11px;")
        face_form.addRow("Model:", self._face_model_combo)
        face_form.addRow("", model_note)

        saved_thresh = float(_settings.get("face_cluster_threshold", catalog_path) or 0.50)
        self._face_thresh_slider = QSlider(Qt.Orientation.Horizontal)
        self._face_thresh_slider.setRange(30, 80)
        self._face_thresh_slider.setValue(int(saved_thresh * 100))
        self._face_thresh_label = QLabel(f"{saved_thresh:.2f}")
        self._face_thresh_slider.valueChanged.connect(
            lambda v: self._face_thresh_label.setText(f"{v/100:.2f}")
        )
        face_form.addRow("Clustering threshold:", self._face_thresh_slider)
        face_form.addRow("  (higher = fewer, stricter groups)", self._face_thresh_label)

        layout.addWidget(face_group)

        # ── Buttons ───────────────────────────────────────────────────
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
            _settings.set_("phash_similarity_threshold", self._phash_slider.value(), self._catalog_path)
            _settings.set_("clip_similarity_min_score", self._clip_slider.value() / 100.0, self._catalog_path)
            _settings.set_(
                "face_cluster_threshold",
                self._face_thresh_slider.value() / 100.0,
                self._catalog_path,
            )
            new_model = self._face_model_combo.currentData()
            old_model = _settings.get("face_model", self._catalog_path) or "buffalo_sc"
            _settings.set_("face_model", new_model, self._catalog_path)
            if new_model != old_model:
                # Reset the cached model so it reloads on next use
                try:
                    import core.faces as _faces
                    _faces._analyzer = None
                    _faces._model_ready = False
                except Exception:
                    pass

        self.accept()
