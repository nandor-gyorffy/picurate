"""Tag CRUD — create, query, and assign tags to photos."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.db.catalog import CatalogWriter, get_connection


def get_or_create_tag(name: str, tag_type: str = "auto", catalog_path: Path | None = None) -> int:
    """Return tag id, creating the tag if it doesn't exist."""
    conn = get_connection(catalog_path)
    row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    with CatalogWriter(catalog_path) as wconn:
        wconn.execute("INSERT INTO tags(name, type) VALUES(?,?)", (name, tag_type))
        return wconn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_tags(catalog_path: Path | None = None) -> list[dict]:
    """Return all tags with their photo counts, sorted by count desc."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT t.id, t.name, t.type, COUNT(pt.photo_id) AS photo_count
           FROM tags t
           LEFT JOIN photo_tags pt ON pt.tag_id = t.id
           GROUP BY t.id
           ORDER BY photo_count DESC, t.name"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_tags_for_photo(photo_id: int, catalog_path: Path | None = None) -> list[dict]:
    """Return [{name, type, confidence, source}] for a photo."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT t.name, t.type, pt.confidence, pt.source
           FROM photo_tags pt
           JOIN tags t ON t.id = pt.tag_id
           WHERE pt.photo_id = ?
           ORDER BY pt.confidence DESC""",
        (photo_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def add_photo_tag(
    photo_id: int,
    tag_id: int,
    confidence: float = 1.0,
    source: str = "manual",
    catalog_path: Path | None = None,
) -> None:
    with CatalogWriter(catalog_path) as conn:
        conn.execute(
            """INSERT INTO photo_tags(photo_id, tag_id, confidence, source)
               VALUES(?,?,?,?)
               ON CONFLICT(photo_id, tag_id) DO UPDATE SET confidence=excluded.confidence""",
            (photo_id, tag_id, confidence, source),
        )


def remove_photo_tag(photo_id: int, tag_id: int, catalog_path: Path | None = None) -> None:
    with CatalogWriter(catalog_path) as conn:
        conn.execute(
            "DELETE FROM photo_tags WHERE photo_id=? AND tag_id=?", (photo_id, tag_id)
        )


def delete_tag(tag_id: int, catalog_path: Path | None = None) -> None:
    """Delete a tag and all its photo associations."""
    with CatalogWriter(catalog_path) as conn:
        conn.execute("DELETE FROM photo_tags WHERE tag_id=?", (tag_id,))
        conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))


def get_photos_by_tag(tag_name: str, catalog_path: Path | None = None) -> list[sqlite3.Row]:
    conn = get_connection(catalog_path)
    return conn.execute(
        """SELECT p.* FROM photos p
           JOIN photo_tags pt ON pt.photo_id = p.id
           JOIN tags t ON t.id = pt.tag_id
           WHERE t.name = ? AND p.status NOT IN ('missing', 'duplicate')
           ORDER BY p.date_taken DESC, p.filename""",
        (tag_name,),
    ).fetchall()
