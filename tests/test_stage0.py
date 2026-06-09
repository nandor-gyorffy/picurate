"""Stage 0 tests: paths, catalog open, schema version, settings."""
import sqlite3
from pathlib import Path

import pytest

from core.db.catalog import open_catalog, integrity_check
from core.db.schema import get_version
from core.paths import data_dir, cache_dir, log_dir


def test_paths_resolve():
    assert data_dir().is_dir()
    assert cache_dir().is_dir()
    assert log_dir().is_dir()


def test_catalog_creates_schema(tmp_path):
    db = tmp_path / "test.db"
    conn = open_catalog(db)
    assert get_version(conn) >= 1
    # Key tables exist
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "photos" in tables
    assert "jobs" in tables
    assert "settings" in tables
    conn.close()


def test_wal_mode(tmp_path):
    db = tmp_path / "test.db"
    conn = open_catalog(db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
    conn.close()


def test_integrity_check(tmp_path):
    db = tmp_path / "test.db"
    open_catalog(db)
    assert integrity_check(db)


def test_settings_store(tmp_path):
    db = tmp_path / "test.db"
    open_catalog(db)
    from core import settings
    settings.set_("test_key", {"nested": True}, db)
    val = settings.get("test_key", db)
    assert val == {"nested": True}


def test_watch_folders(tmp_path):
    db = tmp_path / "test.db"
    open_catalog(db)
    from core import settings
    settings.add_watch_folder("/some/path", db)
    settings.add_watch_folder("/other/path", db)
    settings.add_watch_folder("/some/path", db)  # duplicate — should not double-add
    folders = settings.get_watch_folders(db)
    assert "/some/path" in folders
    assert "/other/path" in folders
    assert folders.count("/some/path") == 1
