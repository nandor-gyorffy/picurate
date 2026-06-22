"""First-run welcome dialog shown once on first launch."""
from __future__ import annotations
import platform
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout,
)
from core.firstrun import check_model_status, install_desktop_launcher, mark_setup_complete
from core.logger import get_logger

log = get_logger("picurate.firstrun_ui")


def _row(name: str, available: bool, hint: str) -> str:
    icon = "&#x2705;" if available else "&#x274C;"
    color = "#2a2" if available else "#888"
    extra = "" if available else f" &nbsp;<span style='font-size:11px'>{hint}</span>"
    return f"<span style='color:{color}'>{icon} <b>{name}</b></span>{extra}"


class FirstRunDialog(QDialog):
    """One-time welcome + setup wizard."""

    def __init__(self, app_dir: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Picurate")
        self.setMinimumWidth(540)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._app_dir = app_dir
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        # Header row with icon + title
        header = QHBoxLayout()
        icon_lbl = QLabel()
        icon_path = self._app_dir / "assets" / "icon" / "picurate.png"
        if icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(
                64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            icon_lbl.setPixmap(pix)
        header.addWidget(icon_lbl)
        title = QLabel(
            "<h2>Welcome to Picurate</h2>"
            "<p style='color:#888;'>Your local, private photo organizer.</p>"
        )
        title.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(title, stretch=1)
        layout.addLayout(header)

        # ML model status
        status = check_model_status()
        rows = [
            _row("exiftool", status["exiftool"],
                 "Download from exiftool.org and add to PATH"),
            _row("Face recognition (InsightFace)", status["faces"],
                 "Models download automatically on first face-detect run"),
            _row("Topic tagging (CLIP)", status["clip"],
                 f"Place ONNX models in: {status['clip_dir']}"),
        ]
        info = QTextBrowser()
        info.setOpenExternalLinks(True)
        info.setMaximumHeight(140)
        info.setHtml(
            "<b>Optional ML components:</b><br>"
            + "<br>".join(rows)
            + "<br><br><i>The app works without these — you can add them later.</i>"
        )
        layout.addWidget(info)

        # Quick-start guide
        qs = QLabel(
            "<b>Quick start:</b><br>"
            "1. Use <b>File &rarr; Open Folder</b> (Ctrl+O) to point Picurate at your photos.<br>"
            "2. Picurate indexes them in the background &mdash; nothing is moved or modified.<br>"
            "3. Rate, flag, collect. Use <b>Ctrl+K</b> to enter Cull Mode."
        )
        qs.setTextFormat(Qt.TextFormat.RichText)
        qs.setWordWrap(True)
        layout.addWidget(qs)

        # Linux: offer to install desktop launcher
        if platform.system() == "Linux":
            self._launcher_btn = QPushButton("Install desktop launcher (add to app menu)")
            self._launcher_btn.setToolTip(
                "Installs a .desktop file so Picurate appears in your applications menu "
                "and can be launched by double-clicking."
            )
            self._launcher_btn.clicked.connect(self._install_launcher)
            layout.addWidget(self._launcher_btn)

        # Dismiss button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Get Started")
        ok_btn.setDefault(True)
        ok_btn.setFixedWidth(120)
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _install_launcher(self):
        ok = install_desktop_launcher(self._app_dir)
        if ok:
            self._launcher_btn.setText("Launcher installed ✓")
            self._launcher_btn.setEnabled(False)
        else:
            self._launcher_btn.setText("Install failed — run ./install_launcher.sh manually")
            self._launcher_btn.setEnabled(False)

    def _on_ok(self):
        mark_setup_complete()
        self.accept()
