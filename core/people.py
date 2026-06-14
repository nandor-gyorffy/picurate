"""People (person identity) CRUD and photo-by-person queries."""
from __future__ import annotations

from pathlib import Path

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger

log = get_logger("picurate.people")


def get_people(catalog_path: Path) -> list[dict]:
    """
    Return [{id, name, face_count, photo_count}] ordered by name.
    face_count  = total face records assigned to this person.
    photo_count = distinct photos with status='ok' that have a face for this person.
    """
    conn = get_connection(catalog_path)
    rows = conn.execute("""
        SELECT p.id, p.name,
               COUNT(DISTINCT f.id) AS face_count,
               COUNT(DISTINCT CASE WHEN ph.status = 'ok' THEN ph.id END) AS photo_count
        FROM people p
        LEFT JOIN faces f ON f.person_id = p.id
        LEFT JOIN photos ph ON ph.id = f.photo_id
        GROUP BY p.id ORDER BY p.name COLLATE NOCASE
    """).fetchall()
    return [dict(r) for r in rows]


def get_person(person_id: int, catalog_path: Path) -> dict | None:
    conn = get_connection(catalog_path)
    row = conn.execute("SELECT id, name FROM people WHERE id=?", (person_id,)).fetchone()
    return dict(row) if row else None


def create_person(name: str, catalog_path: Path) -> int:
    """Create a new person record and return its id."""
    with CatalogWriter(catalog_path) as conn:
        conn.execute("INSERT INTO people (name) VALUES (?)", (name.strip(),))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def rename_person(person_id: int, new_name: str, catalog_path: Path) -> None:
    with CatalogWriter(catalog_path) as conn:
        conn.execute("UPDATE people SET name=? WHERE id=?", (new_name.strip(), person_id))


def delete_person(person_id: int, catalog_path: Path) -> None:
    """Delete person and unassign all their faces (faces remain, person_id→NULL)."""
    with CatalogWriter(catalog_path) as conn:
        conn.execute("UPDATE faces SET person_id=NULL WHERE person_id=?", (person_id,))
        conn.execute("DELETE FROM people WHERE id=?", (person_id,))


def merge_people(source_id: int, target_id: int, catalog_path: Path) -> None:
    """Re-attribute all faces from source_id to target_id, then delete source."""
    with CatalogWriter(catalog_path) as conn:
        conn.execute(
            "UPDATE faces SET person_id=? WHERE person_id=?", (target_id, source_id)
        )
        conn.execute("DELETE FROM people WHERE id=?", (source_id,))


def get_photos_by_person(person_id: int, catalog_path: Path) -> list[dict]:
    """Return distinct photos that have a face attributed to person_id."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT DISTINCT p.id, p.filename, p.file_path, p.thumbnail_path,
                  p.date_taken, p.rating, p.flag
           FROM photos p
           JOIN faces f ON f.photo_id = p.id
           WHERE f.person_id = ? AND p.status = 'ok'
           ORDER BY p.date_taken""",
        (person_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def cleanup_empty_persons(catalog_path: Path) -> int:
    """
    Delete person records that have no faces assigned.
    Returns the number of persons deleted.
    """
    with CatalogWriter(catalog_path) as conn:
        result = conn.execute("""
            DELETE FROM people
            WHERE id NOT IN (
                SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL
            )
        """)
        return result.rowcount


def get_unassigned_face_count(catalog_path: Path) -> int:
    """Count faces with no person_id assigned."""
    conn = get_connection(catalog_path)
    return conn.execute(
        "SELECT COUNT(*) FROM faces WHERE person_id IS NULL"
    ).fetchone()[0]
