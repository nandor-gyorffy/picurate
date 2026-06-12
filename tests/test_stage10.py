"""Stage 10 headless tests: stats, soft-delete, find-similar, write-back no-op."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.db.catalog as _cat_mod
from core.db.catalog import get_connection, open_catalog, CatalogWriter
from core.metadata import (
    empty_trash,
    get_trash,
    restore_photo,
    set_rating,
    soft_delete_photo,
)
from core.similar import find_similar_by_clip, find_similar_by_phash, find_similar
from core.stats import get_camera_summary, get_catalog_stats, get_top_tags
from core.tags import add_photo_tag, get_or_create_tag
from core.writeback import exiftool_available, write_back_photo

FIXTURES = Path(__file__).parent / "fixtures"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def cat(tmp_path: Path) -> Path:
    _cat_mod._local.__dict__.clear()
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


def _pid(cat: Path, fn: str) -> int:
    return get_connection(cat).execute(
        "SELECT id FROM photos WHERE filename=?", (fn,)
    ).fetchone()["id"]


# ── Stats ──────────────────────────────────────────────────────────────────────

class TestStats:
    def test_total_photos(self, cat: Path) -> None:
        stats = get_catalog_stats(cat)
        assert stats["total"] == 3

    def test_picked_count(self, cat: Path) -> None:
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET flag=1 WHERE filename='a.jpg'")
        stats = get_catalog_stats(cat)
        assert stats["picked"] == 1

    def test_rejected_count(self, cat: Path) -> None:
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET flag=2 WHERE filename='b.jpg'")
        stats = get_catalog_stats(cat)
        assert stats["rejected"] == 1

    def test_rated_count(self, cat: Path) -> None:
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET rating=3 WHERE filename='a.jpg'")
        stats = get_catalog_stats(cat)
        assert stats["rated"] == 1

    def test_deleted_count(self, cat: Path) -> None:
        soft_delete_photo(_pid(cat, "a.jpg"), cat)
        stats = get_catalog_stats(cat)
        assert stats["deleted"] == 1
        assert stats["total"] == 2  # deleted excluded from total

    def test_storage_bytes(self, cat: Path) -> None:
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET file_size=1024 WHERE filename='a.jpg'")
        stats = get_catalog_stats(cat)
        assert stats["storage_bytes"] == 1024

    def test_with_tags_count(self, cat: Path) -> None:
        pid_a = _pid(cat, "a.jpg")
        tid = get_or_create_tag("landscape", "auto", cat)
        add_photo_tag(pid_a, tid, catalog_path=cat)
        stats = get_catalog_stats(cat)
        assert stats["with_tags"] == 1

    def test_empty_catalog_safe(self, tmp_path: Path) -> None:
        _cat_mod._local.__dict__.clear()
        db = tmp_path / "empty.db"
        open_catalog(db)
        stats = get_catalog_stats(db)
        assert stats["total"] == 0
        assert stats["storage_bytes"] == 0

    def test_get_top_tags(self, cat: Path) -> None:
        pid_a = _pid(cat, "a.jpg")
        pid_b = _pid(cat, "b.jpg")
        t1 = get_or_create_tag("beach", "auto", cat)
        t2 = get_or_create_tag("forest", "auto", cat)
        add_photo_tag(pid_a, t1, catalog_path=cat)
        add_photo_tag(pid_b, t1, catalog_path=cat)
        add_photo_tag(pid_a, t2, catalog_path=cat)
        top = get_top_tags(cat, limit=5)
        assert top[0]["name"] == "beach"
        assert top[0]["photo_count"] == 2

    def test_get_camera_summary(self, cat: Path) -> None:
        with CatalogWriter(cat) as conn:
            conn.execute(
                "UPDATE photos SET camera_make='Canon', camera_model='EOS R' WHERE filename='a.jpg'"
            )
        summary = get_camera_summary(cat)
        assert any(s["camera"] == "Canon EOS R" for s in summary)


# ── Soft-delete / Trash ────────────────────────────────────────────────────────

class TestSoftDelete:
    def test_soft_delete_sets_status(self, cat: Path) -> None:
        pid = _pid(cat, "a.jpg")
        soft_delete_photo(pid, cat)
        row = get_connection(cat).execute(
            "SELECT status FROM photos WHERE id=?", (pid,)
        ).fetchone()
        assert row["status"] == "deleted"

    def test_deleted_excluded_from_get_photos(self, cat: Path) -> None:
        from core.query import get_photos
        pid = _pid(cat, "a.jpg")
        soft_delete_photo(pid, cat)
        conn = get_connection(cat)
        rows = get_photos(conn)
        ids = [r["id"] for r in rows]
        assert pid not in ids

    def test_restore_photo(self, cat: Path) -> None:
        pid = _pid(cat, "a.jpg")
        soft_delete_photo(pid, cat)
        restore_photo(pid, cat)
        row = get_connection(cat).execute(
            "SELECT status FROM photos WHERE id=?", (pid,)
        ).fetchone()
        assert row["status"] == "ok"

    def test_restore_noop_on_non_deleted(self, cat: Path) -> None:
        pid = _pid(cat, "a.jpg")
        restore_photo(pid, cat)  # not deleted — should not raise
        row = get_connection(cat).execute(
            "SELECT status FROM photos WHERE id=?", (pid,)
        ).fetchone()
        assert row["status"] == "ok"

    def test_get_trash(self, cat: Path) -> None:
        pid = _pid(cat, "a.jpg")
        soft_delete_photo(pid, cat)
        trash = get_trash(cat)
        assert any(r["id"] == pid for r in trash)

    def test_empty_trash(self, cat: Path) -> None:
        pid = _pid(cat, "a.jpg")
        soft_delete_photo(pid, cat)
        count = empty_trash(cat)
        assert count == 1
        row = get_connection(cat).execute(
            "SELECT id FROM photos WHERE id=?", (pid,)
        ).fetchone()
        assert row is None

    def test_empty_trash_does_not_remove_ok_photos(self, cat: Path) -> None:
        pid_ok = _pid(cat, "b.jpg")
        pid_del = _pid(cat, "a.jpg")
        soft_delete_photo(pid_del, cat)
        empty_trash(cat)
        row = get_connection(cat).execute(
            "SELECT id FROM photos WHERE id=?", (pid_ok,)
        ).fetchone()
        assert row is not None

    def test_empty_empty_trash_returns_zero(self, cat: Path) -> None:
        assert empty_trash(cat) == 0


# ── Find similar ──────────────────────────────────────────────────────────────

class TestFindSimilar:
    def test_similar_by_phash_no_phash_returns_empty(self, cat: Path) -> None:
        pid = _pid(cat, "a.jpg")
        assert find_similar_by_phash(pid, cat) == []

    def test_similar_by_phash_finds_identical(self, cat: Path) -> None:
        pid_a = _pid(cat, "a.jpg")
        pid_b = _pid(cat, "b.jpg")
        same = "aaaaaaaaaaaaaaaa"
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET phash=? WHERE id=?", (same, pid_a))
            conn.execute("UPDATE photos SET phash=? WHERE id=?", (same, pid_b))
        results = find_similar_by_phash(pid_a, cat, threshold=0)
        ids = [r["id"] for r in results]
        assert pid_b in ids
        assert pid_a not in ids

    def test_similar_by_clip_no_embedding_returns_empty(self, cat: Path) -> None:
        pid = _pid(cat, "a.jpg")
        assert find_similar_by_clip(pid, cat) == []

    def test_similar_by_clip_finds_close(self, cat: Path) -> None:
        import numpy as np
        pid_a = _pid(cat, "a.jpg")
        pid_b = _pid(cat, "b.jpg")
        pid_c = _pid(cat, "c.jpg")
        # a and b point in same direction; c points orthogonally
        emb_a = [1.0, 0.0, 0.0]
        emb_b = [0.99, 0.1, 0.0]
        emb_c = [0.0, 0.0, 1.0]
        with CatalogWriter(cat) as conn:
            for pid, emb in [(pid_a, emb_a), (pid_b, emb_b), (pid_c, emb_c)]:
                conn.execute(
                    "UPDATE photos SET clip_embedding=? WHERE id=?",
                    (json.dumps(emb), pid),
                )
        results = find_similar_by_clip(pid_a, cat, limit=5)
        assert results[0]["id"] == pid_b  # most similar should be b
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_find_similar_falls_back_to_phash(self, cat: Path) -> None:
        pid_a = _pid(cat, "a.jpg")
        pid_b = _pid(cat, "b.jpg")
        same = "aaaaaaaaaaaaaaaa"
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET phash=? WHERE id=?", (same, pid_a))
            conn.execute("UPDATE photos SET phash=? WHERE id=?", (same, pid_b))
        # no clip embeddings → should use phash
        results = find_similar(pid_a, cat)
        assert any(r["id"] == pid_b for r in results)

    def test_find_similar_nonexistent_returns_empty(self, cat: Path) -> None:
        assert find_similar(99999, cat) == []


# ── Write-back graceful no-op ─────────────────────────────────────────────────

class TestWriteBack:
    def test_available_returns_bool(self) -> None:
        result = exiftool_available()
        assert isinstance(result, bool)

    def test_write_back_without_exiftool(self, cat: Path) -> None:
        if exiftool_available():
            pytest.skip("exiftool is present — testing no-op only")
        pid = _pid(cat, "a.jpg")
        set_rating(pid, 3, cat)
        result = write_back_photo(pid, cat)
        assert result is False  # graceful no-op
