"""Collection CRUD operations (no UI imports)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger

log = get_logger("picurate.collections")


def create_collection(
    name: str,
    type_: str = "manual",
    catalog_path: Path | None = None,
) -> int:
    """Create a new collection. Returns the new id."""
    with CatalogWriter(catalog_path) as conn:
        cur = conn.execute(
            "INSERT INTO collections(name, type) VALUES (?, ?)",
            (name.strip(), type_),
        )
        return cur.lastrowid


def delete_collection(collection_id: int, catalog_path: Path | None = None) -> None:
    with CatalogWriter(catalog_path) as conn:
        conn.execute("DELETE FROM collection_photos WHERE collection_id=?", (collection_id,))
        conn.execute("DELETE FROM collections WHERE id=?", (collection_id,))


def rename_collection(
    collection_id: int, name: str, catalog_path: Path | None = None
) -> None:
    with CatalogWriter(catalog_path) as conn:
        conn.execute(
            "UPDATE collections SET name=? WHERE id=?", (name.strip(), collection_id)
        )


def add_photo(
    collection_id: int, photo_id: int, catalog_path: Path | None = None
) -> None:
    """Add a photo to a collection (idempotent)."""
    with CatalogWriter(catalog_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO collection_photos(collection_id, photo_id) VALUES (?,?)",
            (collection_id, photo_id),
        )


def remove_photo(
    collection_id: int, photo_id: int, catalog_path: Path | None = None
) -> None:
    with CatalogWriter(catalog_path) as conn:
        conn.execute(
            "DELETE FROM collection_photos WHERE collection_id=? AND photo_id=?",
            (collection_id, photo_id),
        )


def get_collections(catalog_path: Path | None = None) -> list[sqlite3.Row]:
    """Return all collections with photo counts."""
    conn = get_connection(catalog_path)
    return conn.execute(
        """SELECT c.id, c.name, c.type,
                  COUNT(cp.photo_id) AS photo_count
           FROM collections c
           LEFT JOIN collection_photos cp ON cp.collection_id = c.id
           GROUP BY c.id
           ORDER BY c.name COLLATE NOCASE"""
    ).fetchall()


def get_collection(
    collection_id: int, catalog_path: Path | None = None
) -> sqlite3.Row | None:
    conn = get_connection(catalog_path)
    return conn.execute(
        "SELECT id, name, type FROM collections WHERE id=?", (collection_id,)
    ).fetchone()


def get_photo_collection_ids(
    photo_id: int, catalog_path: Path | None = None
) -> list[int]:
    """Return list of collection IDs that contain the given photo."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        "SELECT collection_id FROM collection_photos WHERE photo_id=?", (photo_id,)
    ).fetchall()
    return [r["collection_id"] for r in rows]


def photo_in_collection(
    collection_id: int, photo_id: int, catalog_path: Path | None = None
) -> bool:
    conn = get_connection(catalog_path)
    row = conn.execute(
        "SELECT 1 FROM collection_photos WHERE collection_id=? AND photo_id=?",
        (collection_id, photo_id),
    ).fetchone()
    return row is not None
