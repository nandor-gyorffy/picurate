"""Stage 3 headless tests: metadata, collections, query filters, navigation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.db.catalog import get_connection, open_catalog
from core import metadata as _meta
from core import collections as _col
from core.query import (
    count_photos,
    get_adjacent_photo_ids,
    get_photo_by_id,
    get_photos,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def cat(tmp_path: Path) -> Path:
    """Fresh catalog with several photos pre-inserted."""
    import core.db.catalog as _cat_mod
    # Reset thread-local connection cache so this test's get_connection()
    # opens a fresh connection to the new tmp catalog, not the previous test's.
    _cat_mod._local.__dict__.clear()

    p = tmp_path / "test.db"
    open_catalog(p)
    conn = get_connection(p)

    photos = [
        # (filename, file_path, date_taken, rating, flag)
        ("a.jpg", "/photos/2023/a.jpg", "2023-01-15", 3, 1),
        ("b.jpg", "/photos/2023/b.jpg", "2023-01-20", 0, 0),
        ("c.jpg", "/photos/2024/c.jpg", "2024-05-10", 5, 1),
        ("d.jpg", "/photos/2024/d.jpg", "2024-06-01", 2, 2),
        ("e.jpg", "/photos/other/e.jpg", "2022-12-01", 1, 0),
    ]
    with conn:
        for fn, fp, dt, rat, fl in photos:
            conn.execute(
                """INSERT INTO photos
                   (filename, file_path, date_taken, status, quick_sig,
                    rating, flag)
                   VALUES (?,?,?,?,?,?,?)""",
                (fn, fp, dt, "ok", fn, rat, fl),
            )
    return p


# ── Metadata: rating ──────────────────────────────────────────────────────────

class TestRating:
    def test_set_and_get_rating(self, cat: Path) -> None:
        conn = get_connection(cat)
        photos = get_photos(conn)
        pid = photos[0]["id"]

        _meta.set_rating(pid, 4, cat)
        assert _meta.get_rating(pid, cat) == 4

    def test_clear_rating(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        _meta.set_rating(pid, 5, cat)
        _meta.set_rating(pid, 0, cat)
        assert _meta.get_rating(pid, cat) == 0

    def test_invalid_rating_raises(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        with pytest.raises(ValueError):
            _meta.set_rating(pid, 6, cat)
        with pytest.raises(ValueError):
            _meta.set_rating(pid, -1, cat)

    def test_rating_persists_across_connections(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        _meta.set_rating(pid, 3, cat)
        # open fresh connection
        conn2 = sqlite3.connect(str(cat))
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT rating FROM photos WHERE id=?", (pid,)).fetchone()
        assert row["rating"] == 3


# ── Metadata: flag ────────────────────────────────────────────────────────────

class TestFlag:
    def test_set_pick(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[1]["id"]
        _meta.set_flag(pid, _meta.FLAG_PICK, cat)
        assert _meta.get_flag(pid, cat) == _meta.FLAG_PICK

    def test_set_reject(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[1]["id"]
        _meta.set_flag(pid, _meta.FLAG_REJECT, cat)
        assert _meta.get_flag(pid, cat) == _meta.FLAG_REJECT

    def test_unflag(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        _meta.set_flag(pid, _meta.FLAG_PICK, cat)
        _meta.set_flag(pid, _meta.FLAG_NONE, cat)
        assert _meta.get_flag(pid, cat) == _meta.FLAG_NONE

    def test_invalid_flag_raises(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        with pytest.raises(ValueError):
            _meta.set_flag(pid, 3, cat)
        with pytest.raises(ValueError):
            _meta.set_flag(pid, -1, cat)

    def test_constants(self) -> None:
        assert _meta.FLAG_NONE == 0
        assert _meta.FLAG_PICK == 1
        assert _meta.FLAG_REJECT == 2


# ── Collections CRUD ──────────────────────────────────────────────────────────

class TestCollections:
    def test_create_collection(self, cat: Path) -> None:
        cid = _col.create_collection("Favourites", catalog_path=cat)
        assert isinstance(cid, int)
        assert cid > 0

    def test_get_collections_empty(self, cat: Path) -> None:
        assert _col.get_collections(cat) == []

    def test_get_collections_after_create(self, cat: Path) -> None:
        _col.create_collection("Trip A", catalog_path=cat)
        _col.create_collection("Trip B", catalog_path=cat)
        cols = _col.get_collections(cat)
        names = [c["name"] for c in cols]
        assert "Trip A" in names
        assert "Trip B" in names

    def test_photo_count_in_collection(self, cat: Path) -> None:
        conn = get_connection(cat)
        photos = get_photos(conn)
        cid = _col.create_collection("Test", catalog_path=cat)
        _col.add_photo(cid, photos[0]["id"], cat)
        _col.add_photo(cid, photos[1]["id"], cat)
        cols = _col.get_collections(cat)
        col = next(c for c in cols if c["id"] == cid)
        assert col["photo_count"] == 2

    def test_rename_collection(self, cat: Path) -> None:
        cid = _col.create_collection("Old Name", catalog_path=cat)
        _col.rename_collection(cid, "New Name", cat)
        col = _col.get_collection(cid, cat)
        assert col["name"] == "New Name"

    def test_delete_collection(self, cat: Path) -> None:
        cid = _col.create_collection("To Delete", catalog_path=cat)
        _col.delete_collection(cid, cat)
        assert _col.get_collection(cid, cat) is None

    def test_delete_removes_photos_from_collection(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        cid = _col.create_collection("Temp", catalog_path=cat)
        _col.add_photo(cid, pid, cat)
        _col.delete_collection(cid, cat)
        conn2 = get_connection(cat)
        row = conn2.execute(
            "SELECT count(*) AS n FROM collection_photos WHERE collection_id=?", (cid,)
        ).fetchone()
        assert row["n"] == 0

    def test_add_photo_idempotent(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        cid = _col.create_collection("Dup", catalog_path=cat)
        _col.add_photo(cid, pid, cat)
        _col.add_photo(cid, pid, cat)  # second add should not raise or duplicate
        cols = _col.get_collections(cat)
        col = next(c for c in cols if c["id"] == cid)
        assert col["photo_count"] == 1

    def test_remove_photo_from_collection(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        cid = _col.create_collection("X", catalog_path=cat)
        _col.add_photo(cid, pid, cat)
        _col.remove_photo(cid, pid, cat)
        cols = _col.get_collections(cat)
        col = next(c for c in cols if c["id"] == cid)
        assert col["photo_count"] == 0

    def test_photo_in_collection(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        cid = _col.create_collection("Check", catalog_path=cat)
        assert not _col.photo_in_collection(cid, pid, cat)
        _col.add_photo(cid, pid, cat)
        assert _col.photo_in_collection(cid, pid, cat)

    def test_get_photo_collection_ids(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid = get_photos(conn)[0]["id"]
        cid1 = _col.create_collection("C1", catalog_path=cat)
        cid2 = _col.create_collection("C2", catalog_path=cat)
        _col.add_photo(cid1, pid, cat)
        _col.add_photo(cid2, pid, cat)
        ids = _col.get_photo_collection_ids(pid, cat)
        assert cid1 in ids
        assert cid2 in ids


# ── Query: rating_min filter ──────────────────────────────────────────────────

class TestQueryRatingFilter:
    def test_rating_min_1(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, rating_min=1)
        # fixture has rating 3,0,5,2,1 → 4 photos have rating>=1
        assert len(rows) == 4

    def test_rating_min_3(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, rating_min=3)
        # ratings 3,5 → 2 photos
        assert len(rows) == 2

    def test_rating_min_5(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, rating_min=5)
        assert len(rows) == 1
        assert rows[0]["filename"] == "c.jpg"

    def test_count_photos_rating_min(self, cat: Path) -> None:
        conn = get_connection(cat)
        assert count_photos(conn, rating_min=1) == 4

    def test_count_photos_rating_min_5(self, cat: Path) -> None:
        conn = get_connection(cat)
        assert count_photos(conn, rating_min=5) == 1


# ── Query: flag filter ────────────────────────────────────────────────────────

class TestQueryFlagFilter:
    def test_flag_pick(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, flag=1)
        # fixture: a.jpg (flag=1), c.jpg (flag=1) → 2
        assert len(rows) == 2

    def test_flag_reject(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, flag=2)
        # d.jpg (flag=2)
        assert len(rows) == 1
        assert rows[0]["filename"] == "d.jpg"

    def test_flag_unflagged(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, flag=0)
        # b.jpg, e.jpg
        assert len(rows) == 2

    def test_count_photos_flag(self, cat: Path) -> None:
        conn = get_connection(cat)
        assert count_photos(conn, flag=1) == 2


# ── Query: search filter ──────────────────────────────────────────────────────

class TestQuerySearch:
    def test_search_by_filename(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, search="b.jpg")
        assert len(rows) == 1
        assert rows[0]["filename"] == "b.jpg"

    def test_search_partial(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, search=".jpg")
        assert len(rows) == 5

    def test_search_no_match(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, search="zzz_nomatch")
        assert len(rows) == 0

    def test_search_case_insensitive(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, search="A.JPG")
        assert len(rows) == 1


# ── Query: collection_id filter ───────────────────────────────────────────────

class TestQueryCollectionFilter:
    def test_filter_by_collection(self, cat: Path) -> None:
        conn = get_connection(cat)
        photos = get_photos(conn)
        # get_photos orders by date_taken DESC: photos[0]=d.jpg, photos[2]=b.jpg
        cid = _col.create_collection("MyCol", catalog_path=cat)
        _col.add_photo(cid, photos[0]["id"], cat)
        _col.add_photo(cid, photos[2]["id"], cat)
        expected = {photos[0]["filename"], photos[2]["filename"]}

        conn2 = get_connection(cat)
        rows = get_photos(conn2, collection_id=cid)
        assert len(rows) == 2
        filenames = {r["filename"] for r in rows}
        assert filenames == expected

    def test_empty_collection(self, cat: Path) -> None:
        cid = _col.create_collection("Empty", catalog_path=cat)
        conn = get_connection(cat)
        rows = get_photos(conn, collection_id=cid)
        assert len(rows) == 0

    def test_count_photos_collection(self, cat: Path) -> None:
        conn = get_connection(cat)
        photos = get_photos(conn)
        cid = _col.create_collection("Cnt", catalog_path=cat)
        _col.add_photo(cid, photos[0]["id"], cat)
        conn2 = get_connection(cat)
        assert count_photos(conn2, collection_id=cid) == 1


# ── Query: combined filters ───────────────────────────────────────────────────

class TestQueryCombinedFilters:
    def test_rating_and_flag(self, cat: Path) -> None:
        conn = get_connection(cat)
        # rating>=3 AND flag=1 → a.jpg (3,1) and c.jpg (5,1)
        rows = get_photos(conn, rating_min=3, flag=1)
        assert len(rows) == 2

    def test_folder_filter(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, folder="/photos/2023")
        assert len(rows) == 2
        filenames = {r["filename"] for r in rows}
        assert filenames == {"a.jpg", "b.jpg"}

    def test_year_filter(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, year=2024)
        assert len(rows) == 2

    def test_year_month_filter(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, year=2024, month=5)
        assert len(rows) == 1
        assert rows[0]["filename"] == "c.jpg"


# ── Adjacent navigation ───────────────────────────────────────────────────────

class TestAdjacentNavigation:
    def test_adjacent_no_filter(self, cat: Path) -> None:
        conn = get_connection(cat)
        all_photos = get_photos(conn)
        ids = [p["id"] for p in all_photos]
        # Middle photo should have both prev and next
        mid = ids[2]
        prev_id, next_id = get_adjacent_photo_ids(conn, mid)
        assert prev_id == ids[1]
        assert next_id == ids[3]

    def test_adjacent_first_has_no_prev(self, cat: Path) -> None:
        conn = get_connection(cat)
        all_photos = get_photos(conn)
        first = all_photos[0]["id"]
        prev_id, _ = get_adjacent_photo_ids(conn, first)
        assert prev_id is None

    def test_adjacent_last_has_no_next(self, cat: Path) -> None:
        conn = get_connection(cat)
        all_photos = get_photos(conn)
        last = all_photos[-1]["id"]
        _, next_id = get_adjacent_photo_ids(conn, last)
        assert next_id is None

    def test_adjacent_with_rating_filter(self, cat: Path) -> None:
        conn = get_connection(cat)
        # rating>=3 picks a.jpg (3), c.jpg (5) — only 2 photos
        rows = get_photos(conn, rating_min=3)
        ids = [r["id"] for r in rows]
        assert len(ids) == 2

        prev_id, next_id = get_adjacent_photo_ids(conn, ids[0], rating_min=3)
        assert prev_id is None
        assert next_id == ids[1]

        prev_id, next_id = get_adjacent_photo_ids(conn, ids[1], rating_min=3)
        assert prev_id == ids[0]
        assert next_id is None

    def test_adjacent_with_flag_filter(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn, flag=1)  # a.jpg, c.jpg
        ids = [r["id"] for r in rows]
        assert len(ids) == 2

        prev_id, next_id = get_adjacent_photo_ids(conn, ids[0], flag=1)
        assert prev_id is None
        assert next_id == ids[1]

    def test_adjacent_with_collection_filter(self, cat: Path) -> None:
        conn = get_connection(cat)
        photos = get_photos(conn)
        cid = _col.create_collection("NavTest", catalog_path=cat)
        _col.add_photo(cid, photos[0]["id"], cat)
        _col.add_photo(cid, photos[2]["id"], cat)

        conn2 = get_connection(cat)
        rows = get_photos(conn2, collection_id=cid)
        ids = [r["id"] for r in rows]
        assert len(ids) == 2

        prev_id, next_id = get_adjacent_photo_ids(conn2, ids[0], collection_id=cid)
        assert prev_id is None
        assert next_id == ids[1]

    def test_adjacent_photo_not_in_filter_set(self, cat: Path) -> None:
        conn = get_connection(cat)
        # filter to year=2022 (only e.jpg), check navigation for a photo not in set
        all_photos = get_photos(conn)
        other_pid = all_photos[0]["id"]  # a.jpg (2023), not in 2022 filter
        prev_id, next_id = get_adjacent_photo_ids(conn, other_pid, year=2022)
        assert prev_id is None
        assert next_id is None


# ── get_photo_by_id ───────────────────────────────────────────────────────────

class TestGetPhotoById:
    def test_exists(self, cat: Path) -> None:
        conn = get_connection(cat)
        photos = get_photos(conn)
        pid = photos[0]["id"]
        row = get_photo_by_id(conn, pid)
        assert row is not None
        assert row["id"] == pid

    def test_not_exists(self, cat: Path) -> None:
        conn = get_connection(cat)
        row = get_photo_by_id(conn, 99999)
        assert row is None
