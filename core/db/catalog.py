"""Single-writer catalog connection with WAL mode and backup/integrity utilities."""
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from core.db.schema import migrate
from core.logger import get_logger
from core.paths import backup_dir, catalog_path

log = get_logger("picurate.catalog")

_write_lock = threading.Lock()
_local = threading.local()
_writers: dict[str, sqlite3.Connection] = {}  # one real writer per catalog path


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    """Return a per-thread read connection (WAL allows concurrent readers)."""
    p = path or catalog_path()
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _open(p)
    return _local.conn


def writer_connection(path: Path | None = None) -> sqlite3.Connection:
    """Return the single shared writer connection (truly one per catalog path)."""
    key = str(path or catalog_path())
    if key not in _writers:
        _writers[key] = _open(Path(key))
    return _writers[key]


class CatalogWriter:
    """Context manager that serialises writes through a single lock."""

    def __init__(self, path: Path | None = None):
        self._path = path or catalog_path()

    def __enter__(self) -> sqlite3.Connection:
        _write_lock.acquire()
        self._conn = writer_connection(self._path)
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        _write_lock.release()
        return False


def open_catalog(path: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the catalog, run migrations, return a connection."""
    p = path or catalog_path()
    conn = _open(p)
    migrate(conn)
    return conn


def integrity_check(path: Path | None = None) -> bool:
    p = path or catalog_path()
    try:
        conn = _open(p)
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        return result == "ok"
    except Exception as exc:
        log.error("integrity_check failed: %s", exc)
        return False


def backup(path: Path | None = None, label: str = "") -> Path | None:
    """Create a rotating SQLite online backup. Returns backup path or None."""
    p = path or catalog_path()
    if not p.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{label}" if label else ""
    dest = backup_dir() / f"catalog{tag}_{ts}.db"
    try:
        src = _open(p)
        dst = sqlite3.connect(str(dest))
        src.backup(dst)
        dst.close()
        src.close()
        _prune_backups()
        log.info("Backup written to %s", dest)
        return dest
    except Exception as exc:
        log.error("Backup failed: %s", exc)
        return None


def _prune_backups(keep: int = 5) -> None:
    bdir = backup_dir()
    backups = sorted(bdir.glob("catalog*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def restore_latest_backup(path: Path | None = None) -> bool:
    p = path or catalog_path()
    bdir = backup_dir()
    backups = sorted(bdir.glob("catalog*.db"), key=lambda b: b.stat().st_mtime, reverse=True)
    if not backups:
        log.warning("No backups found to restore")
        return False
    latest = backups[0]
    try:
        if p.exists():
            shutil.copy2(p, p.with_suffix(".corrupt"))
        shutil.copy2(latest, p)
        log.info("Restored catalog from %s", latest)
        return True
    except Exception as exc:
        log.error("Restore failed: %s", exc)
        return False
