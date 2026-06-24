"""Cluster unassigned face embeddings into person groups using numpy."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger
from core.people import create_person

log = get_logger("picurate.clustering")


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _cluster_embeddings(
    embeddings: list[np.ndarray],
    threshold: float,
) -> list[list[int]]:
    """
    Cluster face embeddings using scipy agglomerative average-linkage.
    Falls back to union-find (single-linkage) if scipy is unavailable.

    Returns list of groups (each group is a list of indices into *embeddings*).
    Average linkage prevents "bridging": two different people can only be merged
    if the average distance between all pairs across both groups is below the
    threshold, not just a single borderline pair.
    """
    n = len(embeddings)
    if n == 1:
        return [[0]]

    try:
        from scipy.cluster.hierarchy import linkage, fcluster
        from scipy.spatial.distance import squareform

        # Build cosine distance matrix: distance = 1 - cosine_similarity
        dist_matrix = np.zeros((n, n), dtype=np.float64)
        for i in range(n):
            for j in range(i + 1, n):
                sim = _cosine_similarity(embeddings[i], embeddings[j])
                dist_matrix[i, j] = 1.0 - sim
                dist_matrix[j, i] = 1.0 - sim

        condensed = squareform(dist_matrix, checks=False)
        Z = linkage(condensed, method="average")
        # fcluster with criterion='distance' and t = 1 - threshold:
        # faces within distance (1 - threshold) of each other's cluster average are merged
        labels = fcluster(Z, t=1.0 - threshold, criterion="distance")

        groups: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            groups.setdefault(int(label), []).append(idx)
        log.debug("scipy average-linkage: %d faces → %d clusters", n, len(groups))
        return list(groups.values())

    except ImportError:
        log.warning("scipy not available — falling back to union-find (single-linkage) clustering")
        return _connected_components_fallback(embeddings, threshold)


def _connected_components(
    embeddings: list[np.ndarray],
    threshold: float,
) -> list[list[int]]:
    """
    Union-find (single-linkage) clustering — kept as public alias for backwards
    compatibility (test_stage7 imports it by name) and as the scipy fallback.
    """
    return _connected_components_fallback(embeddings, threshold)


def _connected_components_fallback(
    embeddings: list[np.ndarray],
    threshold: float,
) -> list[list[int]]:
    """
    Fallback union-find (single-linkage) clustering when scipy is unavailable.
    """
    n = len(embeddings)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i in range(n):
        for j in range(i + 1, n):
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            if sim >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)
    return list(groups.values())


def cluster_unassigned_faces(
    catalog_path: Path,
    threshold: float | None = None,
    min_group_size: int = 1,
) -> dict:
    """
    Cluster all faces with no person_id assignment.

    - Groups are merged if cosine similarity of ArcFace embeddings ≥ threshold.
    - Uses scipy average-linkage (falls back to union-find if scipy unavailable).
    - Each new group becomes an unnamed person ("Person 1", "Person 2", …).
    - Already-named people are used for matching: if an unassigned face is close
      enough to a named person's centroid it is assigned to them.
    - Calls cleanup_empty_persons() after creating persons to remove orphan shells.

    Returns stats: {faces_loaded, groups_found, people_created, faces_assigned}.
    """
    if threshold is None:
        from core import settings as _s
        threshold = float(_s.get("face_cluster_threshold", catalog_path) or 0.42)

    conn = get_connection(catalog_path)

    # Load unassigned faces that have an embedding
    rows = conn.execute(
        "SELECT id, embedding FROM faces WHERE person_id IS NULL AND embedding IS NOT NULL"
    ).fetchall()

    if not rows:
        return {"faces_loaded": 0, "groups_found": 0, "people_created": 0, "faces_assigned": 0}

    face_ids: list[int] = []
    embeddings: list[np.ndarray] = []
    for row in rows:
        try:
            emb = np.array(json.loads(row["embedding"]), dtype=np.float32)
            face_ids.append(row["id"])
            embeddings.append(emb)
        except Exception:
            pass

    if not embeddings:
        return {"faces_loaded": len(rows), "groups_found": 0, "people_created": 0, "faces_assigned": 0}

    # --- try to match against existing named people first ---
    named = conn.execute(
        """SELECT p.id, f.embedding
           FROM people p JOIN faces f ON f.person_id = p.id
           WHERE f.embedding IS NOT NULL"""
    ).fetchall()

    person_emb_lists: dict[int, list[np.ndarray]] = {}
    for named_row in named:
        pid = named_row["id"]
        try:
            emb = np.array(json.loads(named_row["embedding"]), dtype=np.float32)
            person_emb_lists.setdefault(pid, []).append(emb)
        except Exception:
            pass
    person_centroids: dict[int, np.ndarray] = {
        pid: np.mean(embs, axis=0) for pid, embs in person_emb_lists.items()
    }

    pre_assigned: dict[int, int] = {}  # face_id → person_id (matched to named)
    remaining_indices: list[int] = []

    for idx, (fid, emb) in enumerate(zip(face_ids, embeddings)):
        best_pid, best_sim = None, threshold
        for pid, centroid in person_centroids.items():
            sim = _cosine_similarity(emb, centroid)
            if sim > best_sim:
                best_sim = sim
                best_pid = pid
        if best_pid is not None:
            pre_assigned[fid] = best_pid
        else:
            remaining_indices.append(idx)

    # --- cluster the truly unassigned ones ---
    rem_embeddings = [embeddings[i] for i in remaining_indices]
    rem_face_ids   = [face_ids[i]   for i in remaining_indices]

    groups = _cluster_embeddings(rem_embeddings, threshold) if rem_embeddings else []

    meaningful = [g for g in groups if len(g) >= min_group_size]

    # Count existing unnamed people to number new ones
    existing_count = conn.execute(
        "SELECT COUNT(*) FROM people WHERE name LIKE 'Person %'"
    ).fetchone()[0]

    people_created = 0
    faces_assigned = len(pre_assigned)

    with CatalogWriter(catalog_path) as wconn:
        # Apply pre-assigned (matched to existing named people)
        for fid, pid in pre_assigned.items():
            wconn.execute("UPDATE faces SET person_id=? WHERE id=?", (pid, fid))

        # Create new person for each cluster
        for gi, group in enumerate(meaningful):
            person_name = f"Person {existing_count + gi + 1}"
            cur = wconn.execute("INSERT INTO people (name) VALUES (?)", (person_name,))
            person_id = cur.lastrowid
            for idx in group:
                wconn.execute(
                    "UPDATE faces SET person_id=? WHERE id=?",
                    (person_id, rem_face_ids[idx]),
                )
            people_created += 1
            faces_assigned += len(group)

    # Clean up orphan person shells (persons with no faces left)
    from core.people import cleanup_empty_persons
    cleanup_empty_persons(catalog_path)

    return {
        "faces_loaded": len(face_ids),
        "groups_found": len(meaningful),
        "people_created": people_created,
        "faces_assigned": faces_assigned,
    }
