"""Perceptual-hash near-duplicate detection.

Uses imagehash (pHash) to fingerprint photos and group visually similar
ones by Hamming distance.  The `phash` column in the photos table stores
the hex string produced by imagehash.PHash(image).
"""
from __future__ import annotations

import json
from pathlib import Path

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger

log = get_logger("picurate.duplicates")

_DEFAULT_THRESHOLD = 10   # Hamming bits out of 64; ~15% difference


def compute_phash(file_path: str | Path) -> str | None:
    """Return a 64-bit hex pHash string, or None on failure."""
    try:
        import imagehash
        from PIL import Image
        h = imagehash.phash(Image.open(file_path))
        return str(h)
    except Exception as exc:
        log.warning("pHash failed for %s: %s", file_path, exc)
        return None


def compute_phash_batch(catalog_path: Path | None = None) -> dict:
    """Enqueue phash jobs for photos that don't have one yet."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT id, file_path FROM photos
           WHERE status NOT IN ('missing', 'duplicate') AND phash IS NULL"""
    ).fetchall()
    with CatalogWriter(catalog_path) as wconn:
        for row in rows:
            wconn.execute(
                "INSERT INTO jobs(job_type, payload, status) VALUES('phash',?,?)",
                (json.dumps({"photo_id": row["id"], "path": row["file_path"]}), "pending"),
            )
    return {"enqueued": len(rows)}


def _hamming(a: str, b: str) -> int:
    """Hamming distance between two hex pHash strings."""
    try:
        import imagehash
        return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
    except Exception:
        return 64


def find_duplicate_groups(
    catalog_path: Path | None = None,
    threshold: int = _DEFAULT_THRESHOLD,
) -> list[list[dict]]:
    """Return groups of near-duplicate photos (Hamming ≤ threshold).

    Each group is a list of dicts: {id, filename, phash, quality_score}.
    Groups are sorted by descending size.  Singletons are excluded.
    """
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT id, filename, phash, quality_score FROM photos
           WHERE status NOT IN ('missing', 'duplicate') AND phash IS NOT NULL"""
    ).fetchall()

    if not rows:
        return []

    # Union-find
    parent = list(range(len(rows)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if _hamming(rows[i]["phash"], rows[j]["phash"]) <= threshold:
                union(i, j)

    clusters: dict[int, list[dict]] = {}
    for i, row in enumerate(rows):
        root = find(i)
        clusters.setdefault(root, []).append(dict(row))

    groups = [g for g in clusters.values() if len(g) > 1]
    groups.sort(key=len, reverse=True)
    return groups


def get_best_from_group(photo_ids: list[int], catalog_path: Path | None = None) -> int | None:
    """Return the photo_id with the highest quality_score in the group.

    Falls back to the first id when quality scores are absent.
    """
    if not photo_ids:
        return None
    conn = get_connection(catalog_path)
    placeholders = ",".join("?" * len(photo_ids))
    rows = conn.execute(
        f"""SELECT id, quality_score FROM photos WHERE id IN ({placeholders})
            ORDER BY COALESCE(quality_score, 0) DESC""",
        photo_ids,
    ).fetchall()
    return rows[0]["id"] if rows else photo_ids[0]
