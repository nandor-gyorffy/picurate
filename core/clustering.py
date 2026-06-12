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


def _connected_components(
    embeddings: list[np.ndarray],
    threshold: float,
) -> list[list[int]]:
    """
    Simple union-find clustering: merge faces whose cosine similarity ≥ threshold.
    Returns list of groups (each group is a list of indices into *embeddings*).
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
    threshold: float = 0.45,
    min_group_size: int = 1,
) -> dict:
    """
    Cluster all faces with no person_id assignment.

    - Groups are merged if cosine similarity of ArcFace embeddings ≥ threshold.
    - Each new group becomes an unnamed person ("Person 1", "Person 2", …).
    - Already-named people are used for matching: if an unassigned face is close
      enough to a named person's centroid it is assigned to them.

    Returns stats: {faces_loaded, groups_found, people_created, faces_assigned}.
    """
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

    person_centroids: dict[int, np.ndarray] = {}
    for named_row in named:
        pid = named_row["id"]
        try:
            emb = np.array(json.loads(named_row["embedding"]), dtype=np.float32)
            if pid not in person_centroids:
                person_centroids[pid] = emb
            else:
                person_centroids[pid] = (person_centroids[pid] + emb) / 2.0
        except Exception:
            pass

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

    groups = _connected_components(rem_embeddings, threshold) if rem_embeddings else []

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
            wconn.execute("INSERT INTO people (name) VALUES (?)", (person_name,))
            person_id = wconn.execute("SELECT last_insert_rowid()").fetchone()[0]
            for idx in group:
                wconn.execute(
                    "UPDATE faces SET person_id=? WHERE id=?",
                    (person_id, rem_face_ids[idx]),
                )
            people_created += 1
            faces_assigned += len(group)

    return {
        "faces_loaded": len(face_ids),
        "groups_found": len(meaningful),
        "people_created": people_created,
        "faces_assigned": faces_assigned,
    }
