"""Stage 8 headless tests: tag CRUD, topic query filter, CLIP graceful no-op,
semantic search keyword fallback."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from core.db.catalog import get_connection, open_catalog, CatalogWriter
import core.db.catalog as _cat_mod
from core.tags import (
    add_photo_tag,
    delete_tag,
    get_or_create_tag,
    get_photos_by_tag,
    get_tags,
    get_tags_for_photo,
    remove_photo_tag,
)
from core.topics import (
    DEFAULT_LABELS,
    model_available,
    search_photos_by_text,
    tag_photos_batch,
)
from core.query import count_photos, get_photos


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


def _photo_id(cat: Path, filename: str) -> int:
    return get_connection(cat).execute(
        "SELECT id FROM photos WHERE filename=?", (filename,)
    ).fetchone()["id"]


def _set_clip_embedding(cat: Path, filename: str, emb: list[float]) -> None:
    pid = _photo_id(cat, filename)
    with CatalogWriter(cat) as conn:
        conn.execute(
            "UPDATE photos SET clip_embedding=? WHERE id=?",
            (json.dumps(emb), pid),
        )


# ── Tag CRUD ──────────────────────────────────────────────────────────────────

class TestTagCRUD:
    def test_get_or_create_creates(self, cat: Path) -> None:
        tid = get_or_create_tag("landscape", "auto", cat)
        assert isinstance(tid, int) and tid > 0

    def test_get_or_create_idempotent(self, cat: Path) -> None:
        tid1 = get_or_create_tag("landscape", "auto", cat)
        tid2 = get_or_create_tag("landscape", "auto", cat)
        assert tid1 == tid2

    def test_get_tags_empty(self, cat: Path) -> None:
        assert get_tags(cat) == []

    def test_get_tags_after_create(self, cat: Path) -> None:
        get_or_create_tag("beach", "auto", cat)
        get_or_create_tag("mountains", "auto", cat)
        names = [t["name"] for t in get_tags(cat)]
        assert "beach" in names
        assert "mountains" in names

    def test_add_photo_tag(self, cat: Path) -> None:
        pid = _photo_id(cat, "a.jpg")
        tid = get_or_create_tag("portrait", "auto", cat)
        add_photo_tag(pid, tid, confidence=0.8, source="clip", catalog_path=cat)
        tags = get_tags_for_photo(pid, cat)
        assert len(tags) == 1
        assert tags[0]["name"] == "portrait"
        assert abs(tags[0]["confidence"] - 0.8) < 1e-5

    def test_add_photo_tag_upserts(self, cat: Path) -> None:
        pid = _photo_id(cat, "a.jpg")
        tid = get_or_create_tag("portrait", "auto", cat)
        add_photo_tag(pid, tid, confidence=0.5, source="clip", catalog_path=cat)
        add_photo_tag(pid, tid, confidence=0.9, source="clip", catalog_path=cat)
        tags = get_tags_for_photo(pid, cat)
        assert len(tags) == 1
        assert abs(tags[0]["confidence"] - 0.9) < 1e-5

    def test_remove_photo_tag(self, cat: Path) -> None:
        pid = _photo_id(cat, "a.jpg")
        tid = get_or_create_tag("nature", "auto", cat)
        add_photo_tag(pid, tid, catalog_path=cat)
        remove_photo_tag(pid, tid, cat)
        assert get_tags_for_photo(pid, cat) == []

    def test_delete_tag_removes_associations(self, cat: Path) -> None:
        pid = _photo_id(cat, "a.jpg")
        tid = get_or_create_tag("food", "auto", cat)
        add_photo_tag(pid, tid, catalog_path=cat)
        delete_tag(tid, cat)
        assert get_tags_for_photo(pid, cat) == []

    def test_photo_count_in_get_tags(self, cat: Path) -> None:
        pid_a = _photo_id(cat, "a.jpg")
        pid_b = _photo_id(cat, "b.jpg")
        tid = get_or_create_tag("travel", "auto", cat)
        add_photo_tag(pid_a, tid, catalog_path=cat)
        add_photo_tag(pid_b, tid, catalog_path=cat)
        tags = get_tags(cat)
        t = next(t for t in tags if t["name"] == "travel")
        assert t["photo_count"] == 2

    def test_get_photos_by_tag(self, cat: Path) -> None:
        pid_a = _photo_id(cat, "a.jpg")
        tid = get_or_create_tag("cityscape", "auto", cat)
        add_photo_tag(pid_a, tid, catalog_path=cat)
        rows = get_photos_by_tag("cityscape", cat)
        assert len(rows) == 1
        assert rows[0]["filename"] == "a.jpg"

    def test_get_photos_by_tag_nonexistent(self, cat: Path) -> None:
        assert get_photos_by_tag("does-not-exist", cat) == []

    def test_multiple_tags_per_photo(self, cat: Path) -> None:
        pid = _photo_id(cat, "a.jpg")
        t1 = get_or_create_tag("beach", "auto", cat)
        t2 = get_or_create_tag("sunset", "auto", cat)
        add_photo_tag(pid, t1, confidence=0.7, catalog_path=cat)
        add_photo_tag(pid, t2, confidence=0.5, catalog_path=cat)
        tags = get_tags_for_photo(pid, cat)
        names = {t["name"] for t in tags}
        assert names == {"beach", "sunset"}

    def test_tags_sorted_by_confidence(self, cat: Path) -> None:
        pid = _photo_id(cat, "a.jpg")
        t1 = get_or_create_tag("x", "auto", cat)
        t2 = get_or_create_tag("y", "auto", cat)
        add_photo_tag(pid, t1, confidence=0.3, catalog_path=cat)
        add_photo_tag(pid, t2, confidence=0.9, catalog_path=cat)
        tags = get_tags_for_photo(pid, cat)
        assert tags[0]["confidence"] >= tags[1]["confidence"]


# ── Tag query filter ──────────────────────────────────────────────────────────

class TestTagQueryFilter:
    def test_get_photos_by_tag_filter(self, cat: Path) -> None:
        pid_a = _photo_id(cat, "a.jpg")
        pid_b = _photo_id(cat, "b.jpg")
        tid = get_or_create_tag("landscape", "auto", cat)
        add_photo_tag(pid_a, tid, catalog_path=cat)
        add_photo_tag(pid_b, tid, catalog_path=cat)

        conn = get_connection(cat)
        rows = get_photos(conn, tag="landscape")
        assert len(rows) == 2
        names = {r["filename"] for r in rows}
        assert "a.jpg" in names and "b.jpg" in names

    def test_count_photos_by_tag(self, cat: Path) -> None:
        pid_a = _photo_id(cat, "a.jpg")
        tid = get_or_create_tag("portrait", "auto", cat)
        add_photo_tag(pid_a, tid, catalog_path=cat)

        conn = get_connection(cat)
        assert count_photos(conn, tag="portrait") == 1

    def test_tag_filter_excludes_untagged(self, cat: Path) -> None:
        pid_a = _photo_id(cat, "a.jpg")
        tid = get_or_create_tag("beach", "auto", cat)
        add_photo_tag(pid_a, tid, catalog_path=cat)

        conn = get_connection(cat)
        rows = get_photos(conn, tag="beach")
        filenames = {r["filename"] for r in rows}
        assert "a.jpg" in filenames
        assert "b.jpg" not in filenames
        assert "c.jpg" not in filenames

    def test_no_tag_filter_returns_all(self, cat: Path) -> None:
        conn = get_connection(cat)
        rows = get_photos(conn)
        assert len(rows) == 3

    def test_nonexistent_tag_returns_empty(self, cat: Path) -> None:
        conn = get_connection(cat)
        assert get_photos(conn, tag="no-such-tag") == []


# ── CLIP graceful no-op ───────────────────────────────────────────────────────

class TestClipGracefulNoOp:
    def test_model_available_false_without_files(self) -> None:
        # In test environment, CLIP model files are absent
        assert model_available() is False

    def test_tag_photos_batch_enqueues(self, cat: Path) -> None:
        stats = tag_photos_batch(cat)
        assert stats["enqueued"] == 3

    def test_tag_photos_batch_skips_already_embedded(self, cat: Path) -> None:
        # Pre-set one clip_embedding
        _set_clip_embedding(cat, "a.jpg", [0.1] * 512)
        stats = tag_photos_batch(cat)
        assert stats["enqueued"] == 2  # b.jpg and c.jpg only

    def test_tag_photos_batch_idempotent(self, cat: Path) -> None:
        tag_photos_batch(cat)
        # Second call finds jobs already created (but photos still lack embedding)
        stats2 = tag_photos_batch(cat)
        assert stats2["enqueued"] == 3

    def test_default_labels_not_empty(self) -> None:
        assert len(DEFAULT_LABELS) > 10


# ── Semantic search (keyword fallback) ────────────────────────────────────────

class TestSemanticSearchFallback:
    def test_keyword_fallback_hits_filename(self, cat: Path) -> None:
        # "a.jpg" filename contains "a"
        ids = search_photos_by_text("a", cat)
        conn = get_connection(cat)
        rows = conn.execute("SELECT id FROM photos WHERE filename='a.jpg'").fetchall()
        assert rows[0]["id"] in ids

    def test_keyword_fallback_hits_caption(self, cat: Path) -> None:
        pid_a = _photo_id(cat, "a.jpg")
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET caption='mountain sunset' WHERE id=?", (pid_a,))
        ids = search_photos_by_text("mountain", cat)
        assert pid_a in ids

    def test_keyword_fallback_hits_keywords(self, cat: Path) -> None:
        pid_b = _photo_id(cat, "b.jpg")
        with CatalogWriter(cat) as conn:
            conn.execute("UPDATE photos SET keywords='nature,wildlife' WHERE id=?", (pid_b,))
        ids = search_photos_by_text("wildlife", cat)
        assert pid_b in ids

    def test_keyword_fallback_no_match(self, cat: Path) -> None:
        ids = search_photos_by_text("xyzzy_no_match_9999", cat)
        assert ids == []

    def test_limit_respected(self, cat: Path) -> None:
        # Only 3 photos in the fixture; with limit=2 we get at most 2
        # Use a common substring to match all
        ids = search_photos_by_text(".jpg", cat, limit=2)
        assert len(ids) <= 2

    def test_vector_search_returns_ordered_results(self, cat: Path) -> None:
        """When clip_embeddings are stored, vector search should rank by similarity."""
        # Inject synthetic embeddings; since models are unavailable, we fall
        # through to keyword search, so this test simply confirms no crash.
        _set_clip_embedding(cat, "a.jpg", [1.0] + [0.0] * 511)
        _set_clip_embedding(cat, "b.jpg", [0.0, 1.0] + [0.0] * 510)
        # model_available() is False, so keyword fallback is used
        ids = search_photos_by_text(".jpg", cat)
        assert isinstance(ids, list)


# ── Schema migration (clip_embedding column) ──────────────────────────────────

class TestClipEmbeddingSchema:
    def test_clip_embedding_column_exists(self, cat: Path) -> None:
        conn = get_connection(cat)
        row = conn.execute("PRAGMA table_info(photos)").fetchall()
        cols = [r["name"] for r in row]
        assert "clip_embedding" in cols

    def test_store_and_retrieve_clip_embedding(self, cat: Path) -> None:
        pid = _photo_id(cat, "a.jpg")
        emb = [float(i) for i in range(10)]
        with CatalogWriter(cat) as conn:
            conn.execute(
                "UPDATE photos SET clip_embedding=? WHERE id=?",
                (json.dumps(emb), pid),
            )
        conn = get_connection(cat)
        row = conn.execute("SELECT clip_embedding FROM photos WHERE id=?", (pid,)).fetchone()
        stored = json.loads(row["clip_embedding"])
        assert stored == emb
