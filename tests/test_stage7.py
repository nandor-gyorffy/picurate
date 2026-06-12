"""Stage 7 headless tests: people CRUD, face storage, clustering, person_id filter."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from core.db.catalog import get_connection, open_catalog
from core.clustering import cluster_unassigned_faces, _cosine_similarity, _connected_components
from core.faces import (
    assign_person,
    get_faces_for_photo,
    process_photo_faces,
)
from core.people import (
    create_person,
    delete_person,
    get_people,
    get_person,
    get_photos_by_person,
    get_unassigned_face_count,
    merge_people,
    rename_person,
)
from core.query import count_photos, get_adjacent_photo_ids, get_photos


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def cat(tmp_path: Path) -> Path:
    import core.db.catalog as _m
    _m._local.__dict__.clear()
    db = tmp_path / "test.db"
    open_catalog(db)
    conn = get_connection(db)
    with conn:
        for i, fn in enumerate(["a.jpg", "b.jpg", "c.jpg"], start=1):
            conn.execute(
                """INSERT INTO photos (filename, file_path, status, quick_sig, rating, flag)
                   VALUES (?,?,?,?,0,0)""",
                (fn, f"/photos/{fn}", "ok", fn),
            )
    return db


def _insert_face(cat: Path, photo_filename: str, embedding: list[float],
                 person_id: int | None = None) -> int:
    """Insert a synthetic face record. Returns face id."""
    import core.db.catalog as _m
    conn = get_connection(cat)
    pid = conn.execute(
        "SELECT id FROM photos WHERE filename=?", (photo_filename,)
    ).fetchone()["id"]
    with __import__("core.db.catalog", fromlist=["CatalogWriter"]).CatalogWriter(cat) as wconn:
        wconn.execute(
            """INSERT INTO faces (photo_id, bounding_box, embedding, person_id, source)
               VALUES (?,?,?,?,?)""",
            (pid, json.dumps([0, 0, 100, 100]), json.dumps(embedding), person_id, "test"),
        )
        return wconn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _unit(v: list[float]) -> list[float]:
    arr = np.array(v, dtype=np.float32)
    return (arr / np.linalg.norm(arr)).tolist()


# ── Cosine similarity helper ──────────────────────────────────────────────────

class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        v = np.array([1.0, 0.0, 0.0])
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self) -> None:
        v1 = np.array([1.0, 0.0])
        v2 = np.array([0.0, 1.0])
        assert abs(_cosine_similarity(v1, v2)) < 1e-6

    def test_opposite_vectors(self) -> None:
        v = np.array([1.0, 0.0])
        assert abs(_cosine_similarity(v, -v) + 1.0) < 1e-6

    def test_zero_vector(self) -> None:
        v1 = np.array([1.0, 0.0])
        v2 = np.array([0.0, 0.0])
        assert _cosine_similarity(v1, v2) == 0.0


# ── Connected components ──────────────────────────────────────────────────────

class TestConnectedComponents:
    def test_all_similar_one_group(self) -> None:
        # Three nearly identical vectors
        v = np.array([1.0, 0.0, 0.0])
        groups = _connected_components([v, v, v], threshold=0.9)
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_all_different_separate_groups(self) -> None:
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.0, 1.0, 0.0])
        v3 = np.array([0.0, 0.0, 1.0])
        groups = _connected_components([v1, v2, v3], threshold=0.9)
        assert len(groups) == 3

    def test_two_clusters(self) -> None:
        # Two tight clusters far from each other
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        # a+noise stays close to a
        a2 = np.array([0.999, 0.001])
        b2 = np.array([0.001, 0.999])
        groups = _connected_components([a, a2, b, b2], threshold=0.99)
        assert len(groups) == 2

    def test_single_vector(self) -> None:
        v = np.array([1.0, 0.0])
        groups = _connected_components([v], threshold=0.9)
        assert len(groups) == 1


# ── People CRUD ───────────────────────────────────────────────────────────────

class TestPeopleCRUD:
    def test_create_person(self, cat: Path) -> None:
        pid = create_person("Alice", cat)
        assert isinstance(pid, int)
        assert pid > 0

    def test_get_people_empty(self, cat: Path) -> None:
        assert get_people(cat) == []

    def test_get_people_after_create(self, cat: Path) -> None:
        create_person("Alice", cat)
        create_person("Bob", cat)
        people = get_people(cat)
        names = [p["name"] for p in people]
        assert "Alice" in names
        assert "Bob" in names

    def test_get_person_by_id(self, cat: Path) -> None:
        pid = create_person("Charlie", cat)
        person = get_person(pid, cat)
        assert person["name"] == "Charlie"

    def test_get_person_not_found(self, cat: Path) -> None:
        assert get_person(99999, cat) is None

    def test_rename_person(self, cat: Path) -> None:
        pid = create_person("Old Name", cat)
        rename_person(pid, "New Name", cat)
        assert get_person(pid, cat)["name"] == "New Name"

    def test_delete_person(self, cat: Path) -> None:
        pid = create_person("To Delete", cat)
        delete_person(pid, cat)
        assert get_person(pid, cat) is None

    def test_delete_unassigns_faces(self, cat: Path) -> None:
        pid = create_person("Alice", cat)
        fid = _insert_face(cat, "a.jpg", _unit([1.0, 0.0]), person_id=pid)
        delete_person(pid, cat)
        conn = get_connection(cat)
        row = conn.execute("SELECT person_id FROM faces WHERE id=?", (fid,)).fetchone()
        assert row["person_id"] is None

    def test_merge_people(self, cat: Path) -> None:
        pid1 = create_person("Alice", cat)
        pid2 = create_person("Alice Duplicate", cat)
        fid = _insert_face(cat, "a.jpg", _unit([1.0, 0.0]), person_id=pid1)
        merge_people(pid1, pid2, cat)
        # pid1 should be deleted
        assert get_person(pid1, cat) is None
        # face should now belong to pid2
        conn = get_connection(cat)
        row = conn.execute("SELECT person_id FROM faces WHERE id=?", (fid,)).fetchone()
        assert row["person_id"] == pid2

    def test_people_photo_count(self, cat: Path) -> None:
        pid = create_person("Alice", cat)
        _insert_face(cat, "a.jpg", _unit([1.0, 0.0]), person_id=pid)
        _insert_face(cat, "b.jpg", _unit([0.9, 0.1]), person_id=pid)
        people = get_people(cat)
        alice = next(p for p in people if p["name"] == "Alice")
        assert alice["photo_count"] == 2
        assert alice["face_count"] == 2


# ── Face storage ──────────────────────────────────────────────────────────────

class TestFaceStorage:
    def test_insert_and_retrieve_face(self, cat: Path) -> None:
        emb = _unit([1.0, 0.5, 0.25])
        fid = _insert_face(cat, "a.jpg", emb)
        faces = get_faces_for_photo(
            get_connection(cat).execute(
                "SELECT id FROM photos WHERE filename='a.jpg'"
            ).fetchone()["id"],
            cat,
        )
        assert len(faces) == 1
        assert abs(faces[0]["embedding"][0] - emb[0]) < 1e-4

    def test_assign_person(self, cat: Path) -> None:
        pid = create_person("Dave", cat)
        fid = _insert_face(cat, "a.jpg", _unit([1.0, 0.0]))
        assign_person(fid, pid, cat)
        conn = get_connection(cat)
        row = conn.execute("SELECT person_id FROM faces WHERE id=?", (fid,)).fetchone()
        assert row["person_id"] == pid

    def test_unassigned_face_count(self, cat: Path) -> None:
        _insert_face(cat, "a.jpg", _unit([1.0, 0.0]))
        _insert_face(cat, "b.jpg", _unit([0.0, 1.0]))
        assert get_unassigned_face_count(cat) == 2

    def test_unassigned_count_decreases_after_assign(self, cat: Path) -> None:
        pid = create_person("Eve", cat)
        fid = _insert_face(cat, "a.jpg", _unit([1.0, 0.0]))
        assert get_unassigned_face_count(cat) == 1
        assign_person(fid, pid, cat)
        assert get_unassigned_face_count(cat) == 0


# ── Clustering ────────────────────────────────────────────────────────────────

class TestClustering:
    def test_clusters_similar_faces(self, cat: Path) -> None:
        # Insert 2 very similar faces and 2 very different ones
        emb_a = _unit([1.0, 0.01, 0.0])
        emb_b = _unit([0.99, 0.02, 0.0])   # similar to a
        emb_c = _unit([0.0, 1.0, 0.0])      # different
        emb_d = _unit([0.0, 0.0, 1.0])      # different

        _insert_face(cat, "a.jpg", emb_a)
        _insert_face(cat, "a.jpg", emb_b)
        _insert_face(cat, "b.jpg", emb_c)
        _insert_face(cat, "c.jpg", emb_d)

        stats = cluster_unassigned_faces(cat, threshold=0.98, min_group_size=1)
        assert stats["faces_loaded"] == 4
        assert stats["faces_assigned"] == 4

    def test_no_faces_returns_zeros(self, cat: Path) -> None:
        stats = cluster_unassigned_faces(cat)
        assert stats["faces_loaded"] == 0
        assert stats["people_created"] == 0

    def test_already_assigned_not_reclustered(self, cat: Path) -> None:
        pid = create_person("Named", cat)
        _insert_face(cat, "a.jpg", _unit([1.0, 0.0]), person_id=pid)
        _insert_face(cat, "b.jpg", _unit([0.0, 1.0]))  # unassigned

        stats = cluster_unassigned_faces(cat, threshold=0.5)
        # Only the unassigned face should be processed
        assert stats["faces_loaded"] == 1

    def test_creates_person_records(self, cat: Path) -> None:
        _insert_face(cat, "a.jpg", _unit([1.0, 0.0]))
        _insert_face(cat, "b.jpg", _unit([0.0, 1.0]))
        cluster_unassigned_faces(cat, threshold=0.9, min_group_size=1)
        people = get_people(cat)
        assert len(people) >= 1

    def test_idempotent(self, cat: Path) -> None:
        _insert_face(cat, "a.jpg", _unit([1.0, 0.0]))
        cluster_unassigned_faces(cat, threshold=0.9, min_group_size=1)
        first_count = len(get_people(cat))
        cluster_unassigned_faces(cat, threshold=0.9, min_group_size=1)
        second_count = len(get_people(cat))
        assert first_count == second_count


# ── person_id query filter ────────────────────────────────────────────────────

class TestPersonIdFilter:
    def test_get_photos_by_person_via_query(self, cat: Path) -> None:
        pid = create_person("Alice", cat)
        _insert_face(cat, "a.jpg", _unit([1.0, 0.0]), person_id=pid)
        _insert_face(cat, "b.jpg", _unit([0.9, 0.1]), person_id=pid)

        conn = get_connection(cat)
        rows = get_photos(conn, person_id=pid)
        assert len(rows) == 2
        filenames = {r["filename"] for r in rows}
        assert "a.jpg" in filenames
        assert "b.jpg" in filenames

    def test_count_photos_by_person(self, cat: Path) -> None:
        pid = create_person("Bob", cat)
        _insert_face(cat, "a.jpg", _unit([1.0, 0.0]), person_id=pid)
        conn = get_connection(cat)
        assert count_photos(conn, person_id=pid) == 1

    def test_get_photos_by_person_utility(self, cat: Path) -> None:
        pid = create_person("Carol", cat)
        _insert_face(cat, "c.jpg", _unit([0.5, 0.5]), person_id=pid)
        rows = get_photos_by_person(pid, cat)
        assert len(rows) == 1
        assert rows[0]["filename"] == "c.jpg"

    def test_adjacent_with_person_filter(self, cat: Path) -> None:
        pid = create_person("Dan", cat)
        _insert_face(cat, "a.jpg", _unit([1.0, 0.0]), person_id=pid)
        _insert_face(cat, "b.jpg", _unit([0.9, 0.1]), person_id=pid)
        conn = get_connection(cat)
        rows = get_photos(conn, person_id=pid)
        ids = [r["id"] for r in rows]
        assert len(ids) == 2
        prev_id, next_id = get_adjacent_photo_ids(conn, ids[0], person_id=pid)
        assert prev_id is None
        assert next_id == ids[1]

    def test_no_person_no_results(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, person_id=99999)
        assert rows == []
