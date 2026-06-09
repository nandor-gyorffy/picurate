"""Key-value settings store backed by the catalog's settings table."""
import json
from pathlib import Path

from core.db.catalog import CatalogWriter, get_connection

DEFAULTS: dict[str, object] = {
    "watch_folders": [],
    "thumbnail_size": 256,
    "catalog_path": "",
}


def get(key: str, path: Path | None = None) -> object:
    conn = get_connection(path)
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return DEFAULTS.get(key)
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set_(key: str, value: object, path: Path | None = None) -> None:
    serialized = json.dumps(value)
    with CatalogWriter(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
            (key, serialized),
        )


def get_watch_folders(path: Path | None = None) -> list[str]:
    val = get("watch_folders", path)
    return val if isinstance(val, list) else []


def add_watch_folder(folder: str, path: Path | None = None) -> None:
    folders = get_watch_folders(path)
    if folder not in folders:
        folders.append(folder)
        set_("watch_folders", folders, path)
