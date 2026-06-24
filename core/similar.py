"""Find visually similar photos.

Two strategies (used in order of availability):
1. CLIP embeddings — cosine similarity (requires stored clip_embedding)
2. pHash — Hamming distance (requires stored phash)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.db.catalog import get_connection
from core.logger import get_logger

log = get_logger("picurate.similar")


def find_similar_by_phash(
    photo_id: int,
    catalog_path: Path | None = None,
    threshold: int = 10,
    limit: int = 20,
) -> list[dict]:
    """Return photos near-duplicate to photo_id by pHash Hamming distance.

    Returns [{id, filename, distance}] sorted by ascending distance, excluding photo_id.
    """
    conn = get_connection(catalog_path)
    row = conn.execute("SELECT phash FROM photos WHERE id=?", (photo_id,)).fetchone()
    if row is None or row["phash"] is None:
        return []

    try:
        import imagehash
        target = imagehash.hex_to_hash(row["phash"])
    except Exception:
        return []

    rows = conn.execute(
        """SELECT id, filename, phash FROM photos
           WHERE status NOT IN ('missing','duplicate','deleted')
             AND phash IS NOT NULL AND id != ?""",
        (photo_id,),
    ).fetchall()

    results = []
    for r in rows:
        try:
            dist = imagehash.hex_to_hash(r["phash"]) - target
            if dist <= threshold:
                results.append({"id": r["id"], "filename": r["filename"], "distance": dist})
        except Exception:
            continue

    results.sort(key=lambda x: x["distance"])
    return results[:limit]


def find_similar_by_clip(
    photo_id: int,
    catalog_path: Path | None = None,
    limit: int = 20,
) -> list[dict]:
    """Return photos most similar to photo_id by CLIP cosine similarity.

    Returns [{id, filename, score}] sorted by descending score, excluding photo_id.
    Requires clip_embedding to be stored on photos (run Tag Topics first).
    """
    conn = get_connection(catalog_path)
    row = conn.execute("SELECT clip_embedding FROM photos WHERE id=?", (photo_id,)).fetchone()
    if row is None or row["clip_embedding"] is None:
        return []

    try:
        target = np.array(json.loads(row["clip_embedding"]), dtype=np.float32)
        norm = np.linalg.norm(target)
        if norm > 0:
            target /= norm
    except Exception:
        return []

    rows = conn.execute(
        """SELECT id, filename, clip_embedding FROM photos
           WHERE status NOT IN ('missing','duplicate','deleted')
             AND clip_embedding IS NOT NULL AND id != ?""",
        (photo_id,),
    ).fetchall()

    if not rows:
        return []

    ids = [r["id"] for r in rows]
    filenames = [r["filename"] for r in rows]
    mat = np.array([json.loads(r["clip_embedding"]) for r in rows], dtype=np.float32)
    # Normalise stored embeddings so cosine similarity is correct
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat = mat / np.where(norms > 0, norms, 1.0)
    scores = (mat @ target).tolist()

    results = [
        {"id": ids[i], "filename": filenames[i], "score": scores[i]}
        for i in range(len(ids))
    ]
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def find_similar(
    photo_id: int,
    catalog_path: Path | None = None,
    limit: int = 20,
) -> list[dict]:
    """Find similar photos using the best available method.

    Reads thresholds from settings so changes in the Settings dialog take effect
    immediately on the next search (no app restart required).

    Tries CLIP first (if embeddings exist), then falls back to pHash.
    """
    from core import settings as _s
    phash_thresh = int(_s.get("phash_similarity_threshold", catalog_path) or 10)
    clip_min_score = float(_s.get("clip_similarity_min_score", catalog_path) or 0.60)

    clip_results = find_similar_by_clip(photo_id, catalog_path, limit)
    if clip_results:
        # Filter out low-confidence CLIP results
        filtered = [r for r in clip_results if r["score"] >= clip_min_score]
        if filtered:
            return filtered

    return find_similar_by_phash(photo_id, catalog_path, threshold=phash_thresh, limit=limit)
