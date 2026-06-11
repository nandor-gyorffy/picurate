"""Search + rating/flag filter bar shown above the thumbnail grid."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

_RATING_OPTIONS = [
    ("Any rating", None),
    ("★+", 1),
    ("★★+", 2),
    ("★★★+", 3),
    ("★★★★+", 4),
    ("★★★★★", 5),
]

_FLAG_OPTIONS = [
    ("All", None),
    ("Picked ✓", 1),
    ("Rejected ✗", 2),
    ("Unflagged", 0),
]


class FilterBar(QWidget):
    """Emits filter_changed(dict) whenever any control changes."""

    filter_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)

        # Text search
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search filenames…")
        self._search.setMaximumWidth(200)
        self._search.setClearButtonEnabled(True)
        layout.addWidget(self._search)

        # Rating filter
        layout.addWidget(QLabel("Rating:"))
        self._rating_combo = QComboBox()
        for label, _ in _RATING_OPTIONS:
            self._rating_combo.addItem(label)
        self._rating_combo.setMaximumWidth(110)
        layout.addWidget(self._rating_combo)

        # Flag filter
        layout.addWidget(QLabel("Flag:"))
        self._flag_combo = QComboBox()
        for label, _ in _FLAG_OPTIONS:
            self._flag_combo.addItem(label)
        self._flag_combo.setMaximumWidth(110)
        layout.addWidget(self._flag_combo)

        # Clear button
        clear_btn = QPushButton("Clear filters")
        clear_btn.setMaximumWidth(100)
        clear_btn.clicked.connect(self.clear)
        layout.addWidget(clear_btn)

        layout.addStretch()

        # Debounce timer for text search (avoid rapid re-queries while typing)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._emit)

        self._search.textChanged.connect(lambda _: self._debounce.start())
        self._rating_combo.currentIndexChanged.connect(self._emit)
        self._flag_combo.currentIndexChanged.connect(self._emit)

    # ------------------------------------------------------------------
    def get_filter(self) -> dict:
        """Return the current filter dict (subset — merged with sidebar filter by caller)."""
        rating_min = _RATING_OPTIONS[self._rating_combo.currentIndex()][1]
        flag = _FLAG_OPTIONS[self._flag_combo.currentIndex()][1]
        search = self._search.text().strip() or None
        result: dict = {}
        if rating_min is not None:
            result["rating_min"] = rating_min
        if flag is not None:
            result["flag"] = flag
        if search:
            result["search"] = search
        return result

    def clear(self) -> None:
        self._search.clear()
        self._rating_combo.setCurrentIndex(0)
        self._flag_combo.setCurrentIndex(0)

    def _emit(self) -> None:
        self.filter_changed.emit(self.get_filter())
