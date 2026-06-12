"""Stage 11 headless tests: first-run detection, model status, desktop launcher."""
from __future__ import annotations

import json
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from core.firstrun import (
    check_model_status,
    get_setup_config,
    install_desktop_launcher,
    is_first_run,
    mark_setup_complete,
    remove_desktop_launcher,
    uninstall_data,
)


# ── First-run detection ────────────────────────────────────────────────────────

class TestFirstRun:
    def test_is_first_run_when_no_config(self, tmp_path: Path) -> None:
        with patch("core.firstrun.data_dir", return_value=tmp_path):
            assert is_first_run() is True

    def test_not_first_run_after_mark_complete(self, tmp_path: Path) -> None:
        with patch("core.firstrun.data_dir", return_value=tmp_path):
            mark_setup_complete()
            assert is_first_run() is False

    def test_get_setup_config_empty_before_setup(self, tmp_path: Path) -> None:
        with patch("core.firstrun.data_dir", return_value=tmp_path):
            assert get_setup_config() == {}

    def test_get_setup_config_after_setup(self, tmp_path: Path) -> None:
        with patch("core.firstrun.data_dir", return_value=tmp_path):
            mark_setup_complete()
            cfg = get_setup_config()
            assert cfg.get("setup_complete") is True
            assert "version" in cfg
            assert "platform" in cfg

    def test_mark_setup_idempotent(self, tmp_path: Path) -> None:
        with patch("core.firstrun.data_dir", return_value=tmp_path):
            mark_setup_complete()
            mark_setup_complete()
            assert is_first_run() is False


# ── Model status ──────────────────────────────────────────────────────────────

class TestModelStatus:
    def test_model_status_returns_dict(self) -> None:
        status = check_model_status()
        assert isinstance(status, dict)
        assert "faces" in status
        assert "clip" in status
        assert "exiftool" in status

    def test_model_status_booleans(self) -> None:
        status = check_model_status()
        assert isinstance(status["faces"], bool)
        assert isinstance(status["clip"], bool)
        assert isinstance(status["exiftool"], bool)

    def test_model_dirs_are_strings(self) -> None:
        status = check_model_status()
        assert isinstance(status["insightface_dir"], str)
        assert isinstance(status["clip_dir"], str)


# ── Desktop launcher ──────────────────────────────────────────────────────────

class TestDesktopLauncher:
    def test_install_on_linux(self, tmp_path: Path) -> None:
        if platform.system() != "Linux":
            pytest.skip("Linux-only test")
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        with patch("pathlib.Path.home", return_value=fake_home):
            result = install_desktop_launcher(app_dir)
        assert result is True
        launcher = fake_home / ".local" / "share" / "applications" / "picurate.desktop"
        assert launcher.exists()
        content = launcher.read_text()
        assert "[Desktop Entry]" in content
        assert "Picurate" in content

    def test_install_non_linux_returns_false(self) -> None:
        with patch("core.firstrun.platform.system", return_value="Windows"):
            result = install_desktop_launcher()
        assert result is False

    def test_remove_launcher_returns_false_when_absent(self, tmp_path: Path) -> None:
        with patch("pathlib.Path.home", return_value=tmp_path):
            assert remove_desktop_launcher() is False

    def test_remove_launcher_after_install(self, tmp_path: Path) -> None:
        if platform.system() != "Linux":
            pytest.skip("Linux-only test")
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        fake_home = tmp_path / "home2"
        fake_home.mkdir()
        with patch("pathlib.Path.home", return_value=fake_home):
            install_desktop_launcher(app_dir)
            removed = remove_desktop_launcher()
        assert removed is True


# ── Uninstall ─────────────────────────────────────────────────────────────────

class TestUninstall:
    def test_uninstall_removes_config(self, tmp_path: Path) -> None:
        with patch("core.firstrun.data_dir", return_value=tmp_path):
            mark_setup_complete()
            result = uninstall_data()
        assert "firstrun_config" in result["removed"]

    def test_uninstall_leaves_catalog_by_default(self, tmp_path: Path) -> None:
        catalog_file = tmp_path / "catalog.db"
        catalog_file.write_text("db")
        with (
            patch("core.firstrun.data_dir", return_value=tmp_path),
            patch("core.paths.data_dir", return_value=tmp_path),
        ):
            result = uninstall_data(remove_catalog=False)
        assert "catalog" not in result["removed"]
        assert catalog_file.exists()

    def test_uninstall_removes_catalog_when_asked(self, tmp_path: Path) -> None:
        catalog_file = tmp_path / "catalog.db"
        catalog_file.write_text("db")
        with (
            patch("core.firstrun.data_dir", return_value=tmp_path),
            patch("core.firstrun.Path.home", return_value=tmp_path),
        ):
            from core import paths as _paths
            orig = _paths.catalog_path
            _paths.catalog_path = lambda: catalog_file
            try:
                result = uninstall_data(remove_catalog=True)
            finally:
                _paths.catalog_path = orig
        assert "catalog" in result["removed"]
        assert not catalog_file.exists()
