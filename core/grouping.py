"""Similarity grouping engine: combine pHash, CLIP embeddings, and burst timing.

Groups photos into "similar sets" (burst shots, near-duplicates, same-scene variations),
scores quality within each group, and flags the suggested best photo to keep.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger

log = get_logger("picurate.grouping")

# ── Similarity computation ────────────────────────────────────────────────────

def _hamming(h1: str, h2: str) -> int:
    """Hamming distance between two hex pHash strings."""
    try:
        v1 = int(h1, 16)
        v2 = int(h2, 16)
        diff = v1 ^ v2
        return bin(diff).count("1")
    except Exception:
        return 64  # treat as completely different


def _phash_similarity(h1: str, h2: str) -> float:
    """pHash similarity 0-1 (1 = identical)."""
    d = _hamming(h1, h2)
    return max(0.0, 1.0 - d / 64.0) ** 2  # quadratic to emphasise near-identical


def _clip_similarity(emb1_json: str, emb2_json: str) -> float:
    """Cosine similarity of CLIP embeddings, normalised to 0-1."""
    try:
        a = np.array(json.loads(emb1_json), dtype=np.float32)
        b = np.array(json.loads(emb2_json), dtype=np.float32)
        cos = float(np.dot(a, b))  # both are already unit-normalised
        return (cos + 1.0) / 2.0
    except Exception:
        return 0.0


def _parse_dt(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(dt_str[:19], fmt)
        except ValueError:
            continue
    return None


def _burst_bonus(dt1: str | None, dt2: str | None, seconds: int = 5) -> float:
    """Return 1.0 if photos were taken within `seconds` of each other, else 0.0."""
    a = _parse_dt(dt1)
    b = _parse_dt(dt2)
    if a is None or b is None:
        return 0.0
    return 1.0 if abs((a - b).total_seconds()) <= seconds else 0.0


def compute_combined_similarity(
    row1: Any,
    row2: Any,
    burst_seconds: int = 5,
) -> float:
    """Return a combined similarity score [0, 1] between two photo rows.

    Combines pHash, CLIP embedding cosine, and burst timing.
    """
    phash1 = row1["phash"] if row1["phash"] else None
    phash2 = row2["phash"] if row2["phash"] else None
    clip1  = row1["clip_embedding"] if row1["clip_embedding"] else None
    clip2  = row2["clip_embedding"] if row2["clip_embedding"] else None

    has_phash = phash1 and phash2
    has_clip  = clip1 and clip2

    burst = _burst_bonus(row1["date_taken"], row2["date_taken"], burst_seconds)

    if has_phash and has_clip:
        ps = _phash_similarity(phash1, phash2)
        cs = _clip_similarity(clip1, clip2)
        return 0.40 * ps + 0.45 * cs + 0.15 * burst
    if has_phash:
        return 0.85 * _phash_similarity(phash1, phash2) + 0.15 * burst
    if has_clip:
        return 0.85 * _clip_similarity(clip1, clip2) + 0.15 * burst
    return burst  # timing only — weak signal


# ── Union-Find ────────────────────────────────────────────────────────────────

class _UF:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)

    def components(self):
        groups: dict[Any, list] = {}
        for x in self.parent:
            root = self.find(x)
            groups.setdefault(root, []).append(x)
        return list(groups.values())


# ── Grouping engine ────────────────────────────────────────────────────────────

def group_photos(
    photo_ids: list[int],
    catalog_path: Path | None = None,
    threshold: float = 0.65,
    scope: str = "",
    burst_seconds: int = 5,
) -> dict:
    """
    Group photos by combined similarity and persist the result.

    Returns {
        "groups_created": N,
        "photos_grouped": M,
        "scope": scope,
        "group_ids": [id, ...],
    }.

    Only groups with ≥2 photos are stored.
    """
    if not photo_ids:
        return {"groups_created": 0, "photos_grouped": 0, "scope": scope, "group_ids": []}

    conn = get_connection(catalog_path)
    rows = conn.execute(
        f"""SELECT id, phash, clip_embedding, quality_score, date_taken
            FROM photos
            WHERE id IN ({','.join('?' * len(photo_ids))})""",
        photo_ids,
    ).fetchall()
    rows_by_id = {r["id"]: r for r in rows}

    ids = list(rows_by_id.keys())
    uf = _UF(ids)

    # O(n²) pairwise — acceptable for folder-scope (typically <2000 photos)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            sim = compute_combined_similarity(rows_by_id[a], rows_by_id[b], burst_seconds)
            if sim >= threshold:
                uf.union(a, b)

    components = uf.components()
    multi = [c for c in components if len(c) > 1]

    # Clear existing groups for this scope before writing new ones
    clear_groups_for_scope(scope, catalog_path)

    created_ids: list[int] = []
    photos_grouped = 0

    with CatalogWriter(catalog_path) as wconn:
        for group in multi:
            cur = wconn.execute(
                "INSERT INTO similarity_groups (scope, threshold) VALUES (?,?)",
                (scope, threshold),
            )
            gid = cur.lastrowid
            created_ids.append(gid)

            # Find suggested best (highest quality_score)
            best_id = max(
                group,
                key=lambda pid: (rows_by_id[pid]["quality_score"] or 0.0),
            )
            best_quality = rows_by_id[best_id]["quality_score"] or 0.0

            for pid in group:
                q = rows_by_id[pid]["quality_score"] or 0.0
                sim_to_best = float(q / best_quality) if best_quality > 0 else 0.0
                wconn.execute(
                    """INSERT OR REPLACE INTO photo_similarity_group
                       (photo_id, group_id, similarity_to_best, is_suggested_best)
                       VALUES (?,?,?,?)""",
                    (pid, gid, round(sim_to_best, 4), 1 if pid == best_id else 0),
                )
            photos_grouped += len(group)

    log.info(
        "Grouping scope=%r: %d groups, %d photos (threshold=%.2f)",
        scope, len(created_ids), photos_grouped, threshold,
    )
    return {
        "groups_created": len(created_ids),
        "photos_grouped": photos_grouped,
        "scope": scope,
        "group_ids": created_ids,
    }


def get_similarity_groups(
    scope: str,
    catalog_path: Path | None = None,
) -> list[dict]:
    """Return all stored similarity groups for the given scope.

    Each group dict has keys: id, scope, threshold, photos (list of dicts).
    """
    conn = get_connection(catalog_path)
    groups = conn.execute(
        "SELECT id, scope, threshold, created_at FROM similarity_groups WHERE scope=? ORDER BY id",
        (scope,),
    ).fetchall()

    result = []
    for g in groups:
        members = conn.execute(
            """SELECT psg.photo_id, psg.similarity_to_best, psg.is_suggested_best,
                      p.filename, p.thumbnail_path, p.quality_score,
                      p.sharpness_score, p.exposure_score, p.date_taken
               FROM photo_similarity_group psg
               JOIN photos p ON p.id = psg.photo_id
               WHERE psg.group_id=?
               ORDER BY psg.is_suggested_best DESC, psg.similarity_to_best DESC""",
            (g["id"],),
        ).fetchall()
        result.append({
            "id": g["id"],
            "scope": g["scope"],
            "threshold": g["threshold"],
            "created_at": g["created_at"],
            "photos": [dict(m) for m in members],
        })
    return result


def clear_groups_for_scope(scope: str, catalog_path: Path | None = None) -> int:
    """Delete all similarity groups for the given scope. Returns count removed."""
    conn = get_connection(catalog_path)
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM similarity_groups WHERE scope=?", (scope,)
    ).fetchall()]
    if not ids:
        return 0
    with CatalogWriter(catalog_path) as wconn:
        for gid in ids:
            wconn.execute("DELETE FROM photo_similarity_group WHERE group_id=?", (gid,))
        wconn.execute(
            f"DELETE FROM similarity_groups WHERE id IN ({','.join('?'*len(ids))})",
            ids,
        )
    return len(ids)


def get_photo_group(
    photo_id: int,
    catalog_path: Path | None = None,
) -> dict | None:
    """Return the similarity group that contains photo_id (any scope), or None."""
    conn = get_connection(catalog_path)
    row = conn.execute(
        """SELECT sg.id, sg.scope, sg.threshold
           FROM photo_similarity_group psg
           JOIN similarity_groups sg ON sg.id = psg.group_id
           WHERE psg.photo_id=?
           LIMIT 1""",
        (photo_id,),
    ).fetchone()
    if row is None:
        return None
    members = conn.execute(
        """SELECT psg.photo_id, psg.similarity_to_best, psg.is_suggested_best,
                  p.filename, p.thumbnail_path, p.quality_score,
                  p.sharpness_score, p.exposure_score
           FROM photo_similarity_group psg
           JOIN photos p ON p.id = psg.photo_id
           WHERE psg.group_id=?
           ORDER BY psg.is_suggested_best DESC, psg.similarity_to_best DESC""",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "scope": row["scope"],
        "threshold": row["threshold"],
        "photos": [dict(m) for m in members],
    }


def auto_pick_best_of_groups(
    scope: str,
    collection_id: int,
    catalog_path: Path | None = None,
) -> dict:
    """Add the suggested-best photo from each group in scope to collection_id.

    Returns {"added": N, "groups": G}.
    """
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT psg.photo_id
           FROM photo_similarity_group psg
           JOIN similarity_groups sg ON sg.id = psg.group_id
           WHERE sg.scope=? AND psg.is_suggested_best=1""",
        (scope,),
    ).fetchall()
    added = 0
    with CatalogWriter(catalog_path) as wconn:
        for r in rows:
            wconn.execute(
                "INSERT OR IGNORE INTO collection_photos (collection_id, photo_id) VALUES (?,?)",
                (collection_id, r["photo_id"]),
            )
            added += 1

    groups = conn.execute(
        "SELECT COUNT(*) FROM similarity_groups WHERE scope=?", (scope,)
    ).fetchone()[0]
    return {"added": added, "groups": groups}
