"""Regression tests for bugs found during the post-stage-11 audit."""
from __future__ import annotations

import configparser
from pathlib import Path

import pytest

import core.db.catalog as _cat_mod
from core.db.catalog import get_connection, open_catalog, CatalogWriter


# ── Shared fixture ────────────────────────────────────────────────────────────

@pytest.fixture()
def cat(tmp_path: Path) -> Path:
    _cat_mod._local.__dict__.clear()
    db = tmp_path / "test.db"
    open_catalog(db)
    return db


def _insert_photo(conn, filename, file_path, status="ok"):
    conn.execute(
        "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
        (filename, file_path, status, filename),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── Bug fix: mark_missing path matching (no sibling-folder bleed) ─────────────

class TestMarkMissingFix:
    def test_does_not_mark_sibling_folder(self, cat: Path, tmp_path: Path) -> None:
        """Scanning /photos/trips should NOT mark /photos/trips2/ as missing."""
        trips_dir = tmp_path / "trips"
        trips_dir.mkdir()
        trips2_dir = tmp_path / "trips2"
        trips2_dir.mkdir()

        # A file in trips2 that is NOT inside trips
        real_file = trips2_dir / "img.jpg"
        real_file.write_bytes(b"fake")

        conn = get_connection(cat)
        with conn:
            _insert_photo(conn, "img.jpg", str(real_file))

        from core.scanner import mark_missing
        count = mark_missing(trips_dir, cat)  # scan trips/, not trips2/

        conn2 = get_connection(cat)
        row = conn2.execute("SELECT status FROM photos WHERE filename='img.jpg'").fetchone()
        assert row["status"] == "ok", "File in trips2/ should NOT be marked missing when scanning trips/"
        assert count == 0

    def test_marks_file_inside_folder(self, cat: Path, tmp_path: Path) -> None:
        """Files strictly inside the scanned folder that don't exist should be marked missing."""
        folder = tmp_path / "pics"
        folder.mkdir()

        conn = get_connection(cat)
        with conn:
            _insert_photo(conn, "gone.jpg", str(folder / "gone.jpg"))

        from core.scanner import mark_missing
        count = mark_missing(folder, cat)
        assert count == 1

        conn2 = get_connection(cat)
        row = conn2.execute("SELECT status FROM photos WHERE filename='gone.jpg'").fetchone()
        assert row["status"] == "missing"


# ── Bug fix: get_collections excludes deleted photos from count ───────────────

class TestCollectionDeletedPhotoCount:
    def test_deleted_photo_not_counted(self, cat: Path) -> None:
        """A deleted photo in a collection should not inflate the collection count."""
        from core.collections import create_collection, add_photo, get_collections

        cid = create_collection("test-col", catalog_path=cat)

        conn = get_connection(cat)
        with conn:
            pid_ok = _insert_photo(conn, "ok.jpg", "/photos/ok.jpg")
            pid_del = _insert_photo(conn, "del.jpg", "/photos/del.jpg", status="deleted")

        add_photo(cid, pid_ok, cat)
        add_photo(cid, pid_del, cat)

        cols = get_collections(cat)
        col = next(c for c in cols if c["id"] == cid)
        assert col["photo_count"] == 1, "Deleted photo must not be counted"

    def test_missing_photo_not_counted(self, cat: Path) -> None:
        """A missing photo in a collection should not be counted."""
        from core.collections import create_collection, add_photo, get_collections

        cid = create_collection("miss-col", catalog_path=cat)

        conn = get_connection(cat)
        with conn:
            pid = _insert_photo(conn, "miss.jpg", "/photos/miss.jpg", status="missing")

        add_photo(cid, pid, cat)

        cols = get_collections(cat)
        col = next(c for c in cols if c["id"] == cid)
        assert col["photo_count"] == 0

    def test_ok_photo_counted(self, cat: Path) -> None:
        """Normal ok photos should still be counted."""
        from core.collections import create_collection, add_photo, get_collections

        cid = create_collection("ok-col", catalog_path=cat)

        conn = get_connection(cat)
        with conn:
            pid1 = _insert_photo(conn, "p1.jpg", "/photos/p1.jpg")
            pid2 = _insert_photo(conn, "p2.jpg", "/photos/p2.jpg")

        add_photo(cid, pid1, cat)
        add_photo(cid, pid2, cat)

        cols = get_collections(cat)
        col = next(c for c in cols if c["id"] == cid)
        assert col["photo_count"] == 2


# ── Picasa rect64 decoder ─────────────────────────────────────────────────────

class TestPicasaRect64:
    def test_decode_full_image(self) -> None:
        """rect64(0000000000000000) should be (0,0,0,0)."""
        from core.importers.picasa import _decode_rect64
        result = _decode_rect64("rect64(0000000000000000)")
        assert result is not None
        assert len(result) == 4

    def test_decode_with_parens(self) -> None:
        from core.importers.picasa import _decode_rect64
        # Full frame coords in Picasa: approximately (0,0,1,1)
        # hex for 0, 0, 65535, 65535: 0000 0000 ffff ffff
        result = _decode_rect64("rect64(0000000ffffffff)")
        assert result is not None

    def test_decode_invalid_returns_none(self) -> None:
        from core.importers.picasa import _decode_rect64
        assert _decode_rect64("garbage") is None

    def test_parse_faces_empty(self) -> None:
        from core.importers.picasa import _parse_faces
        assert _parse_faces("") == []

    def test_parse_faces_no_comma(self) -> None:
        from core.importers.picasa import _parse_faces
        assert _parse_faces("rect64(0000000000000000)") == []


# ── Picasa importer: .picasa.ini parsing ─────────────────────────────────────

class TestPicasaImporter:
    def test_reads_star_as_flag_pick(self, tmp_path: Path) -> None:
        folder = tmp_path / "pics"
        folder.mkdir()
        ini = folder / ".picasa.ini"
        ini.write_text("[photo.jpg]\nstar=yes\n", encoding="utf-8")
        (folder / "photo.jpg").write_bytes(b"fake")

        from core.importers.picasa import PicasaImporter
        recs = list(PicasaImporter().records(str(folder)))
        assert any(r.filename == "photo.jpg" and r.flag == 1 for r in recs)

    def test_reads_caption(self, tmp_path: Path) -> None:
        folder = tmp_path / "pics2"
        folder.mkdir()
        ini = folder / ".picasa.ini"
        ini.write_text("[img.jpg]\ncaption=Hello World\n", encoding="utf-8")
        (folder / "img.jpg").write_bytes(b"fake")

        from core.importers.picasa import PicasaImporter
        recs = list(PicasaImporter().records(str(folder)))
        assert any(r.filename == "img.jpg" and r.caption == "Hello World" for r in recs)

    def test_reads_album(self, tmp_path: Path) -> None:
        folder = tmp_path / "pics3"
        folder.mkdir()
        ini = folder / ".picasa.ini"
        ini.write_text(
            "[.album:abc]\nname=Vacation\n\n[trip.jpg]\nalbums=abc\n",
            encoding="utf-8",
        )
        (folder / "trip.jpg").write_bytes(b"fake")

        from core.importers.picasa import PicasaImporter
        recs = list(PicasaImporter().records(str(folder)))
        assert any(r.filename == "trip.jpg" and "Vacation" in r.album_names for r in recs)

    def test_skips_non_photo_sections(self, tmp_path: Path) -> None:
        folder = tmp_path / "pics4"
        folder.mkdir()
        ini = folder / ".picasa.ini"
        ini.write_text("[Picasa]\ncontact2=...\n\n[photo.jpg]\ncaption=x\n", encoding="utf-8")
        (folder / "photo.jpg").write_bytes(b"fake")

        from core.importers.picasa import PicasaImporter
        recs = list(PicasaImporter().records(str(folder)))
        filenames = [r.filename for r in recs]
        assert "Picasa" not in filenames
        assert "photo.jpg" in filenames


# ── Folder importer ───────────────────────────────────────────────────────────

class TestFolderImporter:
    def test_root_photos_use_root_name(self, tmp_path: Path) -> None:
        folder = tmp_path / "album"
        folder.mkdir()
        (folder / "a.jpg").write_bytes(b"x")
        (folder / "b.jpg").write_bytes(b"x")

        from core.importers.folder import FolderImporter
        recs = list(FolderImporter().records(str(folder)))
        for r in recs:
            assert "album" in r.album_names

    def test_subfolder_photos_use_subfolder_name(self, tmp_path: Path) -> None:
        folder = tmp_path / "root"
        folder.mkdir()
        sub = folder / "Wedding"
        sub.mkdir()
        (sub / "w.jpg").write_bytes(b"x")

        from core.importers.folder import FolderImporter
        recs = list(FolderImporter().records(str(folder)))
        assert any("Wedding" in r.album_names for r in recs)

    def test_ignores_non_photo_files(self, tmp_path: Path) -> None:
        folder = tmp_path / "mixed"
        folder.mkdir()
        (folder / "notes.txt").write_text("hello")
        (folder / "photo.jpg").write_bytes(b"x")

        from core.importers.folder import FolderImporter
        recs = list(FolderImporter().records(str(folder)))
        filenames = [r.filename for r in recs]
        assert "notes.txt" not in filenames
        assert "photo.jpg" in filenames


# ── Import engine: match_records ─────────────────────────────────────────────

class TestImportEngine:
    def test_match_by_exact_path(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("x.jpg", "/real/x.jpg", "ok", "x"),
            )

        from core.importers.base import ImportRecord
        from core.importers.engine import match_records
        rec = ImportRecord(filename="x.jpg", source_path="/real/x.jpg")
        results = match_records([rec], cat)
        assert results[0].matched_photo_id is not None

    def test_match_by_filename(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("unique.jpg", "/some/path/unique.jpg", "ok", "u"),
            )

        from core.importers.base import ImportRecord
        from core.importers.engine import match_records
        rec = ImportRecord(filename="unique.jpg", source_path="/other/unique.jpg")
        results = match_records([rec], cat)
        assert results[0].matched_photo_id is not None

    def test_ambiguous_filename_not_matched(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("dup.jpg", "/a/dup.jpg", "ok", "a"),
            )
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("dup.jpg", "/b/dup.jpg", "ok", "b"),
            )

        from core.importers.base import ImportRecord
        from core.importers.engine import match_records
        rec = ImportRecord(filename="dup.jpg", source_path="/other/dup.jpg")
        results = match_records([rec], cat)
        assert results[0].matched_photo_id is None

    def test_apply_records_creates_collection(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("a.jpg", "/photos/a.jpg", "ok", "a"),
            )
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        from core.importers.base import ImportRecord
        from core.importers.engine import apply_records, CONFLICT_PREFER

        rec = ImportRecord(
            filename="a.jpg",
            source_path="/photos/a.jpg",
            album_names=["Summer 2024"],
        )
        rec.matched_photo_id = pid

        stats = apply_records([rec], cat, "folder", "/photos", CONFLICT_PREFER)
        assert stats["albums_created"] == 1
        assert stats["applied"] == 1

        # Verify collection exists and photo is in it
        conn2 = get_connection(cat)
        col = conn2.execute("SELECT id FROM collections WHERE name='Summer 2024'").fetchone()
        assert col is not None
        member = conn2.execute(
            "SELECT 1 FROM collection_photos WHERE collection_id=? AND photo_id=?",
            (col["id"], pid),
        ).fetchone()
        assert member is not None

    def test_apply_conflict_keep_does_not_overwrite(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig, rating) VALUES (?,?,?,?,?)",
                ("b.jpg", "/photos/b.jpg", "ok", "b", 4),
            )
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        from core.importers.base import ImportRecord
        from core.importers.engine import apply_records, CONFLICT_KEEP

        rec = ImportRecord(filename="b.jpg", source_path="/photos/b.jpg", rating=1)
        rec.matched_photo_id = pid

        apply_records([rec], cat, "folder", "/photos", CONFLICT_KEEP)

        conn2 = get_connection(cat)
        row = conn2.execute("SELECT rating FROM photos WHERE id=?", (pid,)).fetchone()
        assert row["rating"] == 4  # unchanged

    def test_apply_conflict_prefer_overwrites(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig, rating) VALUES (?,?,?,?,?)",
                ("c.jpg", "/photos/c.jpg", "ok", "c", 2),
            )
            pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        from core.importers.base import ImportRecord
        from core.importers.engine import apply_records, CONFLICT_PREFER

        rec = ImportRecord(filename="c.jpg", source_path="/photos/c.jpg", rating=5)
        rec.matched_photo_id = pid

        apply_records([rec], cat, "folder", "/photos", CONFLICT_PREFER)

        conn2 = get_connection(cat)
        row = conn2.execute("SELECT rating FROM photos WHERE id=?", (pid,)).fetchone()
        assert row["rating"] == 5


# ── query.py: deleted photos excluded from get_photos ─────────────────────────

class TestQueryDeletedExclusion:
    def test_deleted_photo_not_in_get_photos(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("active.jpg", "/p/active.jpg", "ok", "a"),
            )
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("deleted.jpg", "/p/deleted.jpg", "deleted", "d"),
            )

        from core.query import get_photos
        rows = get_photos(get_connection(cat), limit=100)
        filenames = [r["filename"] for r in rows]
        assert "active.jpg" in filenames
        assert "deleted.jpg" not in filenames

    def test_count_excludes_deleted(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("ok.jpg", "/p/ok.jpg", "ok", "ok"),
            )
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("gone.jpg", "/p/gone.jpg", "deleted", "gn"),
            )

        from core.query import count_photos
        assert count_photos(get_connection(cat)) == 1
