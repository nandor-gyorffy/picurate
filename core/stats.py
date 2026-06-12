"""Catalog statistics — headless, pure SQL, no UI imports."""
from __future__ import annotations

from pathlib import Path

from core.db.catalog import get_connection


def get_catalog_stats(catalog_path: Path | None = None) -> dict:
    """Return a summary dict of catalog statistics."""
    conn = get_connection(catalog_path)

    total = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE status NOT IN ('missing','duplicate','deleted')"
    ).fetchone()[0]

    missing = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE status='missing'"
    ).fetchone()[0]

    duplicate = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE status='duplicate'"
    ).fetchone()[0]

    deleted = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE status='deleted'"
    ).fetchone()[0]

    rated = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE rating > 0 AND status NOT IN ('missing','duplicate','deleted')"
    ).fetchone()[0]

    picked = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE flag=1 AND status NOT IN ('missing','duplicate','deleted')"
    ).fetchone()[0]

    rejected = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE flag=2 AND status NOT IN ('missing','duplicate','deleted')"
    ).fetchone()[0]

    with_location = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE gps_lat IS NOT NULL AND status NOT IN ('missing','duplicate','deleted')"
    ).fetchone()[0]

    with_faces = conn.execute(
        "SELECT COUNT(DISTINCT photo_id) FROM faces"
    ).fetchone()[0]

    with_tags = conn.execute(
        "SELECT COUNT(DISTINCT photo_id) FROM photo_tags"
    ).fetchone()[0]

    people_count = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]

    storage_bytes = conn.execute(
        "SELECT COALESCE(SUM(file_size),0) FROM photos WHERE status NOT IN ('missing','duplicate','deleted')"
    ).fetchone()[0]

    collections_count = conn.execute(
        "SELECT COUNT(*) FROM collections WHERE type='manual'"
    ).fetchone()[0]

    trips_count = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]

    tags_count = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

    # Rating distribution
    rating_dist = {}
    rows = conn.execute(
        """SELECT rating, COUNT(*) AS cnt FROM photos
           WHERE status NOT IN ('missing','duplicate','deleted')
           GROUP BY rating ORDER BY rating"""
    ).fetchall()
    for row in rows:
        rating_dist[row["rating"]] = row["cnt"]

    return {
        "total": total,
        "missing": missing,
        "duplicate": duplicate,
        "deleted": deleted,
        "rated": rated,
        "picked": picked,
        "rejected": rejected,
        "with_location": with_location,
        "with_faces": with_faces,
        "with_tags": with_tags,
        "people": people_count,
        "collections": collections_count,
        "trips": trips_count,
        "tags": tags_count,
        "storage_bytes": storage_bytes,
        "rating_distribution": rating_dist,
    }


def get_top_tags(catalog_path: Path | None = None, limit: int = 10) -> list[dict]:
    """Return the top N tags by photo count."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT t.name, COUNT(pt.photo_id) AS photo_count
           FROM tags t
           JOIN photo_tags pt ON pt.tag_id = t.id
           GROUP BY t.id
           ORDER BY photo_count DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_camera_summary(catalog_path: Path | None = None) -> list[dict]:
    """Return photo counts grouped by camera model."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT COALESCE(camera_make || ' ' || camera_model, 'Unknown') AS camera,
                  COUNT(*) AS photo_count
           FROM photos
           WHERE status NOT IN ('missing','duplicate','deleted')
           GROUP BY camera
           ORDER BY photo_count DESC
           LIMIT 20""",
    ).fetchall()
    return [dict(r) for r in rows]
