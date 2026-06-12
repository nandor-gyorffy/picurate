"""Similarity Group viewer dialog.

Shows all similarity groups for a given scope, highlights the suggested best,
lets the user adjust the threshold and re-run, and bulk-adds the best of each
group into a collection.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.grouping import auto_pick_best_of_groups, get_similarity_groups, group_photos
from core.collections import create_collection, get_collections
from core.logger import get_logger

log = get_logger("picurate.ui.groupview")

_THUMB = 100


# ── Background grouping thread ────────────────────────────────────────────────

class _GroupThread(QThread):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, photo_ids, catalog_path, threshold, scope, parent=None):
        super().__init__(parent)
        self._ids = photo_ids
        self._cpath = catalog_path
        self._threshold = threshold
        self._scope = scope

    def run(self) -> None:
        try:
            result = group_photos(
                self._ids, self._cpath,
                threshold=self._threshold,
                scope=self._scope,
            )
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


# ── Single group widget ───────────────────────────────────────────────────────

class _GroupWidget(QWidget):
    """Displays one similarity group as a horizontal row of thumbnails."""

    def __init__(self, group: dict, parent=None):
        super().__init__(parent)
        self._group = group
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        n = len(group["photos"])
        label = QLabel(f"Group {group['id']}  ({n} photos)")
        label.setFixedWidth(100)
        label.setWordWrap(True)
        layout.addWidget(label)

        for photo in group["photos"]:
            box = self._make_photo_card(photo)
            layout.addWidget(box)

        layout.addStretch()

    def _make_photo_card(self, photo: dict) -> QWidget:
        card = QWidget()
        card.setFixedWidth(_THUMB + 4)
        vl = QVBoxLayout(card)
        vl.setContentsMargins(2, 2, 2, 2)
        vl.setSpacing(2)

        img_label = QLabel()
        img_label.setFixedSize(_THUMB, _THUMB)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        thumb = photo.get("thumbnail_path") or ""
        if thumb and Path(thumb).exists():
            pix = QPixmap(thumb).scaled(
                _THUMB, _THUMB,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            pix = QPixmap(_THUMB, _THUMB)
            pix.fill(Qt.GlobalColor.darkGray)
        img_label.setPixmap(pix)

        is_best = bool(photo.get("is_suggested_best"))
        if is_best:
            img_label.setStyleSheet("border: 2px solid #f4c430;")
            img_label.setToolTip("Suggested best")
        vl.addWidget(img_label)

        qs = photo.get("quality_score")
        score_txt = f"Q: {qs:.2f}" if qs else "Q: —"
        sharp = photo.get("sharpness_score")
        exp = photo.get("exposure_score")
        if sharp is not None and exp is not None:
            score_txt += f"\nS:{sharp:.2f} E:{exp:.2f}"

        score_label = QLabel(score_txt)
        score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_label.setStyleSheet("font-size: 9px; color: #aaa;")
        vl.addWidget(score_label)

        name_label = QLabel(photo.get("filename", "")[:18])
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet("font-size: 9px;")
        vl.addWidget(name_label)

        if is_best:
            best_lbl = QLabel("⭐ best")
            best_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            best_lbl.setStyleSheet("color: #f4c430; font-size: 9px; font-weight: bold;")
            vl.addWidget(best_lbl)

        return card


# ── Main dialog ───────────────────────────────────────────────────────────────

class GroupViewDialog(QDialog):
    """Show similarity groups, allow re-running with different threshold,
    and bulk-add the best of each group into a collection."""

    collection_changed = Signal()

    def __init__(
        self,
        photo_ids: list[int],
        catalog_path: Path,
        scope: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._photo_ids = photo_ids
        self._catalog_path = catalog_path
        self._scope = scope
        self._thread: _GroupThread | None = None

        self.setWindowTitle("Similarity Groups")
        self.setMinimumSize(860, 560)
        self._build_ui()
        # Load any already-computed groups for this scope
        self._show_groups(get_similarity_groups(scope, catalog_path))

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── Controls bar ──────────────────────────────────────────────
        ctrl_box = QGroupBox("Options")
        ctrl = QHBoxLayout(ctrl_box)

        ctrl.addWidget(QLabel("Similarity threshold:"))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(30, 95)
        self._slider.setValue(65)
        self._slider.setTickInterval(5)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setFixedWidth(220)
        ctrl.addWidget(self._slider)

        self._threshold_label = QLabel("65%")
        self._threshold_label.setFixedWidth(36)
        self._slider.valueChanged.connect(
            lambda v: self._threshold_label.setText(f"{v}%")
        )
        ctrl.addWidget(self._threshold_label)

        ctrl.addSpacing(16)
        ctrl.addWidget(QLabel("(← loose  aggressive →)"))
        ctrl.addStretch()

        run_btn = QPushButton("Run grouping")
        run_btn.clicked.connect(self._run_grouping)
        ctrl.addWidget(run_btn)
        root.addWidget(ctrl_box)

        # ── Spinner ───────────────────────────────────────────────────
        self._spinner = QProgressBar()
        self._spinner.setRange(0, 0)
        self._spinner.setFixedHeight(4)
        self._spinner.setTextVisible(False)
        self._spinner.setVisible(False)
        root.addWidget(self._spinner)

        # ── Status ────────────────────────────────────────────────────
        self._status = QLabel("Click 'Run grouping' to detect similar photos.")
        root.addWidget(self._status)

        # ── Groups scroll area ────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._groups_widget = QWidget()
        self._groups_layout = QVBoxLayout(self._groups_widget)
        self._groups_layout.setContentsMargins(4, 4, 4, 4)
        self._groups_layout.setSpacing(8)
        self._groups_layout.addStretch()
        self._scroll.setWidget(self._groups_widget)
        root.addWidget(self._scroll, stretch=1)

        # ── Bottom: auto-pick ─────────────────────────────────────────
        bottom = QHBoxLayout()

        bottom.addWidget(QLabel("Add best of each group to:"))

        self._col_combo = QComboBox()
        self._col_combo.setMinimumWidth(200)
        self._refresh_collections()
        bottom.addWidget(self._col_combo)

        new_col_btn = QPushButton("New collection…")
        new_col_btn.clicked.connect(self._new_collection)
        bottom.addWidget(new_col_btn)

        pick_btn = QPushButton("Auto-pick best →")
        pick_btn.setToolTip("Add the suggested best from each group to the selected collection")
        pick_btn.clicked.connect(self._auto_pick)
        bottom.addWidget(pick_btn)

        bottom.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)

        root.addLayout(bottom)

    # ------------------------------------------------------------------
    def _run_grouping(self) -> None:
        threshold = self._slider.value() / 100.0
        self._spinner.setVisible(True)
        self._status.setText(f"Grouping {len(self._photo_ids)} photos (threshold {self._slider.value()}%)…")

        self._thread = _GroupThread(
            self._photo_ids, self._catalog_path, threshold, self._scope, self
        )
        self._thread.done.connect(self._on_group_done)
        self._thread.failed.connect(self._on_group_failed)
        self._thread.start()

    def _on_group_done(self, result: dict) -> None:
        self._spinner.setVisible(False)
        n = result["groups_created"]
        p = result["photos_grouped"]
        self._status.setText(
            f"Found {n} group{'s' if n != 1 else ''} covering {p} photos."
            if n else "No similar groups found at this threshold."
        )
        self._show_groups(get_similarity_groups(self._scope, self._catalog_path))

    def _on_group_failed(self, msg: str) -> None:
        self._spinner.setVisible(False)
        self._status.setText(f"Grouping failed: {msg}")

    def _show_groups(self, groups: list[dict]) -> None:
        # Clear existing group widgets (keep the stretch at the end)
        while self._groups_layout.count() > 1:
            item = self._groups_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for g in groups:
            w = _GroupWidget(g)
            self._groups_layout.insertWidget(self._groups_layout.count() - 1, w)

    def _refresh_collections(self) -> None:
        self._col_combo.clear()
        for col in get_collections(self._catalog_path):
            self._col_combo.addItem(f"{col['name']}  ({col['photo_count']})", col["id"])

    def _new_collection(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Collection", "Collection name:")
        if ok and name.strip():
            create_collection(name.strip(), catalog_path=self._catalog_path)
            self._refresh_collections()

    def _auto_pick(self) -> None:
        if self._col_combo.count() == 0:
            self._status.setText("Please create a collection first.")
            return
        cid = self._col_combo.currentData()
        result = auto_pick_best_of_groups(self._scope, cid, self._catalog_path)
        self._status.setText(
            f"Added {result['added']} photo{'s' if result['added'] != 1 else ''} "
            f"(best of {result['groups']} groups) to the collection."
        )
        self.collection_changed.emit()
        self._refresh_collections()
