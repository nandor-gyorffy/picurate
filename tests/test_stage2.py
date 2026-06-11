"""Stage 2 tests: query module (filters, timeline, folder tree, navigation)."""
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _seed_db(tmp_path: Path) -> Path:
    """Create a catalog seeded with fixture photos and return the db path."""
    from core.db.catalog import open_catalog
    from core.scanner import scan_folder

    src = tmp_path / "photos"
    shutil.copytree(FIXTURES, src)
    db = tmp_path / "catalog.db"
    open_catalog(db)
    scan_folder(src, db)
    return db


# ── count_photos ─────────────────────────────────────────────────────────────

def test_count_photos_all(tmp_path):
    from core.db.catalog import get_connection
    from core.query import count_photos
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    assert count_photos(conn) >= 3


def test_count_photos_folder_filter(tmp_path):
    from core.db.catalog import get_connection
    from core.query import count_photos
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    src = tmp_path / "photos"
    # Filter to the fixture folder itself — should find photos
    total = count_photos(conn)
    filtered = count_photos(conn, folder=str(src))
    assert filtered <= total
    assert filtered >= 1


# ── get_photos ────────────────────────────────────────────────────────────────

def test_get_photos_returns_rows(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_photos
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    rows = get_photos(conn)
    assert len(rows) >= 3
    # Each row has the expected columns
    row = rows[0]
    assert "id" in row.keys()
    assert "file_path" in row.keys()
    assert "filename" in row.keys()
    assert "date_taken" in row.keys()
    assert "thumbnail_path" in row.keys()


def test_get_photos_year_filter(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_photos
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    # The basic.jpg fixture has date 2023
    rows_2023 = get_photos(conn, year=2023)
    rows_9999 = get_photos(conn, year=9999)
    assert len(rows_9999) == 0
    # 2023 rows must be a subset of all rows
    all_rows = get_photos(conn)
    assert len(rows_2023) <= len(all_rows)


def test_get_photos_folder_filter(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_photos
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    src = tmp_path / "photos"
    rows = get_photos(conn, folder=str(src))
    assert len(rows) >= 1
    for row in rows:
        assert row["file_path"].startswith(str(src))


def test_get_photos_limit(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_photos
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    rows = get_photos(conn, limit=2)
    assert len(rows) <= 2


def test_get_photos_excludes_missing(tmp_path):
    from core.db.catalog import get_connection, CatalogWriter
    from core.query import get_photos
    db = _seed_db(tmp_path)
    # Mark all as missing
    with CatalogWriter(db) as conn:
        conn.execute("UPDATE photos SET status='missing'")
    conn = get_connection(db)
    rows = get_photos(conn)
    assert len(rows) == 0


# ── get_photo_by_id ───────────────────────────────────────────────────────────

def test_get_photo_by_id(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_photos, get_photo_by_id
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    all_rows = get_photos(conn)
    first_id = all_rows[0]["id"]
    row = get_photo_by_id(conn, first_id)
    assert row is not None
    assert row["id"] == first_id


def test_get_photo_by_id_missing(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_photo_by_id
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    assert get_photo_by_id(conn, 99999) is None


# ── get_timeline ─────────────────────────────────────────────────────────────

def test_get_timeline_returns_tuples(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_timeline
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    tl = get_timeline(conn)
    assert isinstance(tl, list)
    for item in tl:
        assert len(item) == 3
        year, month, count = item
        assert 1900 <= year <= 2100
        assert 1 <= month <= 12
        assert count >= 1


def test_get_timeline_year_2023_present(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_timeline
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    tl = get_timeline(conn)
    years = [y for y, m, c in tl]
    assert 2023 in years


def test_get_timeline_ordered_newest_first(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_timeline
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    tl = get_timeline(conn)
    if len(tl) >= 2:
        # Verify descending order
        for i in range(len(tl) - 1):
            y1, m1, _ = tl[i]
            y2, m2, _ = tl[i + 1]
            assert (y1, m1) >= (y2, m2)


# ── get_unique_folders ────────────────────────────────────────────────────────

def test_get_unique_folders(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_unique_folders
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    folders = get_unique_folders(conn)
    assert isinstance(folders, dict)
    assert len(folders) >= 1
    for folder, cnt in folders.items():
        assert cnt >= 1


# ── get_adjacent_photo_ids ────────────────────────────────────────────────────

def test_adjacent_photo_ids(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_photos, get_adjacent_photo_ids
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    rows = get_photos(conn)
    assert len(rows) >= 2

    # Middle item should have both prev and next
    mid_id = rows[len(rows) // 2]["id"]
    prev_id, next_id = get_adjacent_photo_ids(conn, mid_id)
    assert prev_id is not None
    assert next_id is not None
    assert prev_id != mid_id
    assert next_id != mid_id


def test_adjacent_first_has_no_prev(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_photos, get_adjacent_photo_ids
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    rows = get_photos(conn)
    first_id = rows[0]["id"]
    prev_id, _ = get_adjacent_photo_ids(conn, first_id)
    assert prev_id is None


def test_adjacent_last_has_no_next(tmp_path):
    from core.db.catalog import get_connection
    from core.query import get_photos, get_adjacent_photo_ids
    db = _seed_db(tmp_path)
    conn = get_connection(db)
    rows = get_photos(conn)
    last_id = rows[-1]["id"]
    _, next_id = get_adjacent_photo_ids(conn, last_id)
    assert next_id is None
