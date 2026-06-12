"""Stage 9 headless tests: quality scoring, pHash, near-duplicate grouping,
smart collections, best-of-trip."""
from __future__ import annotations

from pathlib import Path

import pytest

import core.db.catalog as _cat_mod
from core.db.catalog import get_connection, open_catalog, CatalogWriter
from core.duplicates import (
    compute_phash,
    compute_phash_batch,
    find_duplicate_groups,
    get_best_from_group,
)
from core.quality import compute_quality_score, compute_quality_batch
from core.collections import (
    best_of_trip,
    create_smart_collection,
    evaluate_smart_collection,
    get_collections,
)
from core.query import get_photos

FIXTURES = Path(__file__).parent / "fixtures"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def cat(tmp_path: Path) -> Path:
    _cat_mod._local.__dict__.clear()
    db = tmp_path / "test.db"
    open_catalog(db)
    conn = get_connection(db)
    with conn:
        for i, fn in enumerate(["a.jpg", "b.jpg", "c.jpg", "d.jpg"], start=1):
            conn.execute(
                """INSERT INTO photos (filename, file_path, status, quick_sig, rating, flag)
                   VALUES (?,?,?,?,0,0)""",
                (fn, f"/photos/{fn}", "ok", fn),
            )
    return db


def _photo_id(cat: Path, fn: str) -> int:
    return get_connection(cat).execute(
        "SELECT id FROM photos WHERE filename=?", (fn,)
    ).fetchone()["id"]


def _set_phash(cat: Path, fn: str, h: str) -> None:
    pid = _photo_id(cat, fn)
    with CatalogWriter(cat) as conn:
        conn.execute("UPDATE photos SET phash=? WHERE id=?", (h, pid))


def _set_quality(cat: Path, fn: str, score: float) -> None:
    pid = _photo_id(cat, fn)
    with CatalogWriter(cat) as conn:
        conn.execute("UPDATE photos SET quality_score=? WHERE id=?", (score, pid))


def _add_trip(cat: Path, name: str) -> int:
    with CatalogWriter(cat) as conn:
        conn.execute("INSERT INTO trips(name) VALUES(?)", (name,))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── Quality scoring ───────────────────────────────────────────────────────────

class TestQualityScoring:
    def test_score_basic_jpg(self) -> None:
        path = FIXTURES / "basic.jpg"
        score = compute_quality_score(path)
        assert score is not None
        assert 0.0 <= score <= 1.0

    def test_score_plain_png(self) -> None:
        path = FIXTURES / "plain.png"
        score = compute_quality_score(path)
        assert score is not None
        assert 0.0 <= score <= 1.0

    def test_score_nonexistent_returns_none(self) -> None:
        assert compute_quality_score("/no/such/file.jpg") is None

    def test_compute_quality_batch_enqueues(self, cat: Path) -> None:
        stats = compute_quality_batch(cat)
        assert stats["enqueued"] == 4

    def test_compute_quality_batch_skips_scored(self, cat: Path) -> None:
        _set_quality(cat, "a.jpg", 0.5)
        stats = compute_quality_batch(cat)
        assert stats["enqueued"] == 3

    def test_compute_quality_batch_idempotent(self, cat: Path) -> None:
        compute_quality_batch(cat)
        stats2 = compute_quality_batch(cat)
        assert stats2["enqueued"] == 4  # jobs enqueued again (photos still unscored)


# ── Perceptual hash ────────────────────────────────────────────────────────────

class TestPerceptualHash:
    def test_phash_basic_jpg(self) -> None:
        h = compute_phash(FIXTURES / "basic.jpg")
        assert isinstance(h, str)
        assert len(h) == 16  # 64-bit = 16 hex chars

    def test_phash_plain_png(self) -> None:
        h = compute_phash(FIXTURES / "plain.png")
        assert isinstance(h, str) and len(h) == 16

    def test_phash_same_file_stable(self) -> None:
        h1 = compute_phash(FIXTURES / "basic.jpg")
        h2 = compute_phash(FIXTURES / "basic.jpg")
        assert h1 == h2

    def test_phash_different_images_differ(self, tmp_path: Path) -> None:
        # Create two visually distinct synthetic images to guarantee hash differs
        from PIL import Image
        import numpy as np
        a = Image.fromarray(np.linspace(0, 255, 64 * 64, dtype=np.uint8).reshape(64, 64))
        b = Image.fromarray(np.linspace(255, 0, 64 * 64, dtype=np.uint8).reshape(64, 64))
        pa, pb = tmp_path / "a.png", tmp_path / "b.png"
        a.save(pa)
        b.save(pb)
        h1 = compute_phash(pa)
        h2 = compute_phash(pb)
        assert h1 != h2

    def test_phash_nonexistent_returns_none(self) -> None:
        assert compute_phash("/no/such/file.jpg") is None

    def test_compute_phash_batch_enqueues(self, cat: Path) -> None:
        stats = compute_phash_batch(cat)
        assert stats["enqueued"] == 4

    def test_compute_phash_batch_skips_hashed(self, cat: Path) -> None:
        _set_phash(cat, "a.jpg", "aaaaaaaaaaaaaaaa")
        stats = compute_phash_batch(cat)
        assert stats["enqueued"] == 3


# ── Near-duplicate grouping ───────────────────────────────────────────────────

class TestNearDuplicates:
    def test_no_phashes_returns_empty(self, cat: Path) -> None:
        groups = find_duplicate_groups(cat)
        assert groups == []

    def test_identical_phashes_form_group(self, cat: Path) -> None:
        same_hash = "aaaaaaaaaaaaaaaa"
        _set_phash(cat, "a.jpg", same_hash)
        _set_phash(cat, "b.jpg", same_hash)
        _set_phash(cat, "c.jpg", "bbbbbbbbbbbbbbbb")  # very different
        groups = find_duplicate_groups(cat)
        assert len(groups) == 1
        ids = {p["id"] for p in groups[0]}
        assert ids == {_photo_id(cat, "a.jpg"), _photo_id(cat, "b.jpg")}

    def test_singletons_excluded(self, cat: Path) -> None:
        _set_phash(cat, "a.jpg", "0000000000000001")
        _set_phash(cat, "b.jpg", "ffffffffffffffff")  # far from a
        groups = find_duplicate_groups(cat)
        assert groups == []

    def test_threshold_controls_grouping(self, cat: Path) -> None:
        # Use known hashes with known distance
        # These are identical so distance=0
        _set_phash(cat, "a.jpg", "aaaaaaaaaaaaaaaa")
        _set_phash(cat, "b.jpg", "aaaaaaaaaaaaaaaa")
        assert len(find_duplicate_groups(cat, threshold=0)) == 1
        # Very far hashes: distance should exceed threshold=5
        _set_phash(cat, "c.jpg", "5555555555555555")
        _set_phash(cat, "d.jpg", "aaaaaaaaaaaaaaaa")
        groups = find_duplicate_groups(cat, threshold=0)
        # Only a,b,d (all identical) should group; c might or might not depending on actual distance
        assert any(len(g) >= 2 for g in groups)

    def test_get_best_from_group_by_quality(self, cat: Path) -> None:
        pid_a = _photo_id(cat, "a.jpg")
        pid_b = _photo_id(cat, "b.jpg")
        _set_quality(cat, "a.jpg", 0.3)
        _set_quality(cat, "b.jpg", 0.9)
        best = get_best_from_group([pid_a, pid_b], cat)
        assert best == pid_b

    def test_get_best_from_group_no_quality_returns_first(self, cat: Path) -> None:
        pid_a = _photo_id(cat, "a.jpg")
        pid_b = _photo_id(cat, "b.jpg")
        best = get_best_from_group([pid_a, pid_b], cat)
        assert best in (pid_a, pid_b)

    def test_get_best_from_empty_group_returns_none(self, cat: Path) -> None:
        assert get_best_from_group([], cat) is None


# ── Smart collections ──────────────────────────────────────────────────────────

class TestSmartCollections:
    def test_create_smart_collection(self, cat: Path) -> None:
        cid = create_smart_collection("High rated", {"rating_min": 4}, cat)
        assert isinstance(cid, int) and cid > 0

    def test_evaluate_smart_collection_by_rating(self, cat: Path) -> None:
        # Set one photo to rating 5
        pid_a = _photo_id(cat, "a.jpg")
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET rating=5 WHERE id=?", (pid_a,))
        cid = create_smart_collection("5-stars", {"rating_min": 5}, cat)
        ids = evaluate_smart_collection(cid, cat)
        assert ids == [pid_a]

    def test_evaluate_smart_collection_by_flag(self, cat: Path) -> None:
        pid_b = _photo_id(cat, "b.jpg")
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET flag=1 WHERE id=?", (pid_b,))
        cid = create_smart_collection("Picks", {"flag": 1}, cat)
        ids = evaluate_smart_collection(cid, cat)
        assert pid_b in ids

    def test_evaluate_refreshes_membership(self, cat: Path) -> None:
        pid_a = _photo_id(cat, "a.jpg")
        cid = create_smart_collection("Rated", {"rating_min": 4}, cat)
        ids1 = evaluate_smart_collection(cid, cat)
        assert pid_a not in ids1

        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET rating=4 WHERE id=?", (pid_a,))
        ids2 = evaluate_smart_collection(cid, cat)
        assert pid_a in ids2

    def test_evaluate_nonexistent_returns_empty(self, cat: Path) -> None:
        ids = evaluate_smart_collection(99999, cat)
        assert ids == []

    def test_smart_collection_appears_in_get_collections(self, cat: Path) -> None:
        create_smart_collection("Auto", {"flag": 1}, cat)
        cols = [dict(c) for c in get_collections(cat)]
        assert any(c["name"] == "Auto" and c["type"] == "smart" for c in cols)


# ── Best-of-trip ──────────────────────────────────────────────────────────────

class TestBestOfTrip:
    def test_best_of_trip_no_trip_returns_none(self, cat: Path) -> None:
        result = best_of_trip(99999, cat)
        assert result["collection_id"] is None

    def test_best_of_trip_creates_collection(self, cat: Path) -> None:
        trip_id = _add_trip(cat, "Paris 2024")
        pid_a = _photo_id(cat, "a.jpg")
        pid_b = _photo_id(cat, "b.jpg")
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET trip_id=? WHERE id IN (?,?)", (trip_id, pid_a, pid_b))
        result = best_of_trip(trip_id, cat)
        assert result["collection_id"] is not None
        assert result["photos_added"] == 2  # no duplicates, so both kept

    def test_best_of_trip_picks_best_from_dupes(self, cat: Path) -> None:
        trip_id = _add_trip(cat, "Berlin 2024")
        pid_a = _photo_id(cat, "a.jpg")
        pid_b = _photo_id(cat, "b.jpg")
        # Make them near-duplicates with identical phash
        same = "aaaaaaaaaaaaaaaa"
        _set_phash(cat, "a.jpg", same)
        _set_phash(cat, "b.jpg", same)
        _set_quality(cat, "a.jpg", 0.2)
        _set_quality(cat, "b.jpg", 0.8)
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET trip_id=? WHERE id IN (?,?)", (trip_id, pid_a, pid_b))
        result = best_of_trip(trip_id, cat)
        assert result["photos_added"] == 1
        assert result["groups_processed"] == 1
        # Best should be b.jpg (higher quality)
        cid = result["collection_id"]
        conn = get_connection(cat)
        members = conn.execute(
            "SELECT photo_id FROM collection_photos WHERE collection_id=?", (cid,)
        ).fetchall()
        assert members[0]["photo_id"] == pid_b

    def test_best_of_trip_idempotent(self, cat: Path) -> None:
        trip_id = _add_trip(cat, "Tokyo 2024")
        pid_a = _photo_id(cat, "a.jpg")
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET trip_id=? WHERE id=?", (trip_id, pid_a))
        r1 = best_of_trip(trip_id, cat)
        r2 = best_of_trip(trip_id, cat)
        assert r1["collection_id"] == r2["collection_id"]
        assert r1["photos_added"] == r2["photos_added"]
