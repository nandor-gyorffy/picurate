"""Factory-reset helpers — wipe catalog and/or thumbnail cache."""
from __future__ import annotations

import shutil
from pathlib import Path

from core.logger import get_logger
from core.paths import backup_dir, catalog_path as default_catalog_path, thumbnail_dir

log = get_logger("picurate.reset")


def reset_catalog(catalog_path: Path | None = None) -> None:
    """Delete the catalog database and its backups."""
    cat = catalog_path or default_catalog_path()
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(cat) + suffix)
        if p.exists():
            p.unlink()
            log.info("Removed %s", p)

    bd = backup_dir()
    if bd.exists():
        shutil.rmtree(bd)
        log.info("Removed backup dir %s", bd)


def reset_thumbnails() -> None:
    """Delete the entire thumbnail cache."""
    td = thumbnail_dir()
    if td.exists():
        shutil.rmtree(td)
        log.info("Removed thumbnail cache %s", td)


def factory_reset(catalog_path: Path | None = None, *, clear_thumbnails: bool = True) -> None:
    """Full factory reset: catalog + optionally thumbnail cache."""
    reset_catalog(catalog_path)
    if clear_thumbnails:
        reset_thumbnails()
    log.info("Factory reset complete")
