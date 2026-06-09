"""Resolve per-OS data/cache/log directories using platformdirs."""
from pathlib import Path
import platformdirs

APP_NAME = "Picurate"
APP_AUTHOR = "Picurate"


def data_dir() -> Path:
    p = Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = Path(platformdirs.user_cache_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = Path(platformdirs.user_log_dir(APP_NAME, APP_AUTHOR))
    p.mkdir(parents=True, exist_ok=True)
    return p


def catalog_path() -> Path:
    return data_dir() / "catalog.db"


def thumbnail_dir() -> Path:
    p = cache_dir() / "thumbnails"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_dir() -> Path:
    p = data_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p
