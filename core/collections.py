"""Collection CRUD operations (no UI imports)."""
from __future__ import annotations

import json
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
    """Return all collections with photo counts (excluding deleted/missing/duplicate photos)."""
    conn = get_connection(catalog_path)
    return conn.execute(
        """SELECT c.id, c.name, c.type,
                  COUNT(p.id) AS photo_count
           FROM collections c
           LEFT JOIN collection_photos cp ON cp.collection_id = c.id
           LEFT JOIN photos p ON p.id = cp.photo_id
               AND p.status NOT IN ('missing', 'duplicate', 'deleted')
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


# ── Smart collections ──────────────────────────────────────────────────────────

def create_smart_collection(
    name: str,
    rules: dict,
    catalog_path: Path | None = None,
) -> int:
    """Create a smart collection with JSON rules. Returns the new id."""
    with CatalogWriter(catalog_path) as conn:
        cur = conn.execute(
            "INSERT INTO collections(name, type, rules) VALUES(?,?,?)",
            (name.strip(), "smart", json.dumps(rules)),
        )
        return cur.lastrowid


def evaluate_smart_collection(
    collection_id: int,
    catalog_path: Path | None = None,
) -> list[int]:
    """Re-evaluate a smart collection and return matching photo ids.

    Supported rule keys (all optional, AND-combined):
        rating_min, flag, tag, trip_id, person_id, year, month, search
    After evaluation, collection_photos is refreshed to match.
    """
    conn = get_connection(catalog_path)
    row = conn.execute(
        "SELECT rules FROM collections WHERE id=? AND type='smart'", (collection_id,)
    ).fetchone()
    if row is None:
        return []

    rules: dict = json.loads(row["rules"] or "{}")

    from core.query import get_photos
    photo_rows = get_photos(
        conn,
        rating_min=rules.get("rating_min"),
        flag=rules.get("flag"),
        tag=rules.get("tag"),
        trip_id=rules.get("trip_id"),
        person_id=rules.get("person_id"),
        year=rules.get("year"),
        month=rules.get("month"),
        search=rules.get("search"),
        limit=10000,
    )

    photo_ids = [r["id"] for r in photo_rows]

    # Refresh collection membership
    with CatalogWriter(catalog_path) as wconn:
        wconn.execute("DELETE FROM collection_photos WHERE collection_id=?", (collection_id,))
        for pid in photo_ids:
            wconn.execute(
                "INSERT OR IGNORE INTO collection_photos(collection_id, photo_id) VALUES(?,?)",
                (collection_id, pid),
            )

    return photo_ids


def best_of_trip(trip_id: int, catalog_path: Path | None = None) -> dict:
    """Create (or refresh) a 'Best of <trip>' collection with one photo per
    near-duplicate group, keeping the highest-quality shot from each group,
    plus all photos that have no duplicates.

    Returns {collection_id, photos_added, groups_processed}.
    """
    from core.duplicates import find_duplicate_groups, get_best_from_group

    conn = get_connection(catalog_path)
    trip_row = conn.execute("SELECT name FROM trips WHERE id=?", (trip_id,)).fetchone()
    if trip_row is None:
        return {"collection_id": None, "photos_added": 0, "groups_processed": 0}

    trip_name = trip_row["name"]
    coll_name = f"Best of {trip_name}"

    # Get or create the collection
    existing = conn.execute(
        "SELECT id FROM collections WHERE name=? AND type='smart'", (coll_name,)
    ).fetchone()
    if existing:
        cid = existing["id"]
    else:
        cid = create_smart_collection(
            coll_name,
            {"trip_id": trip_id, "best_of": True},
            catalog_path,
        )

    # All photo ids in this trip
    rows = conn.execute(
        """SELECT id FROM photos
           WHERE trip_id=? AND status NOT IN ('missing','duplicate')""",
        (trip_id,),
    ).fetchall()
    all_ids = [r["id"] for r in rows]

    # Find duplicate groups restricted to this trip
    groups = find_duplicate_groups(catalog_path)
    trip_id_set = set(all_ids)
    trip_groups = [
        [p for p in g if p["id"] in trip_id_set]
        for g in groups
    ]
    trip_groups = [g for g in trip_groups if len(g) > 1]

    grouped_ids: set[int] = set()
    best_ids: list[int] = []
    for group in trip_groups:
        ids = [p["id"] for p in group]
        best = get_best_from_group(ids, catalog_path)
        if best is not None:
            best_ids.append(best)
        grouped_ids.update(ids)

    # Include ungrouped photos as-is
    for pid in all_ids:
        if pid not in grouped_ids:
            best_ids.append(pid)

    with CatalogWriter(catalog_path) as wconn:
        wconn.execute("DELETE FROM collection_photos WHERE collection_id=?", (cid,))
        for pid in best_ids:
            wconn.execute(
                "INSERT OR IGNORE INTO collection_photos(collection_id, photo_id) VALUES(?,?)",
                (cid, pid),
            )

    return {
        "collection_id": cid,
        "photos_added": len(best_ids),
        "groups_processed": len(trip_groups),
    }
