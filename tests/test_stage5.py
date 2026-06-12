"""Stage 5 headless tests: importers (folder, picasa, xmp) + engine."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.db.catalog import get_connection, open_catalog
from core.importers.base import ImportRecord
from core.importers.folder import FolderImporter
from core.importers.picasa import PicasaImporter, _decode_rect64, _parse_faces
from core.importers.xmp import XmpImporter
from core.importers.engine import (
    CONFLICT_KEEP, CONFLICT_MERGE, CONFLICT_PREFER,
    apply_records, match_records, undo_batch,
)
from core.query import get_photos


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
                   VALUES (?,?,?,?,?,?)""",
                (fn, f"/photos/{fn}", "ok", fn, 0, 0),
            )
    return db


@pytest.fixture()
def photo_tree(tmp_path: Path) -> Path:
    """Create a real folder tree with JPEG files."""
    from PIL import Image
    root = tmp_path / "library"
    for sub, names in [
        ("Vacation", ["beach.jpg", "mountain.jpg"]),
        ("Family",   ["dinner.jpg"]),
        ("",         ["misc.jpg"]),  # root-level
    ]:
        folder = root / sub if sub else root
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            img = Image.new("RGB", (10, 10), (100, 100, 100))
            img.save(str(folder / name), "JPEG")
    return root


# ── Picasa helpers ────────────────────────────────────────────────────────────

class TestPicasaHelpers:
    def test_decode_rect64_known_value(self) -> None:
        # rect64 of (0, 0, 65535, 65535) → (0.0, 0.0, 1.0, 1.0)
        val = hex(0 * (2**48) + 0 * (2**32) + 65535 * (2**16) + 65535)[2:]
        val = val.zfill(16)
        coords = _decode_rect64(val)
        assert coords is not None
        l, t, r, b = coords
        assert abs(l) < 0.001
        assert abs(t) < 0.001
        assert abs(r - 1.0) < 0.001
        assert abs(b - 1.0) < 0.001

    def test_decode_rect64_with_prefix(self) -> None:
        coords = _decode_rect64("rect64(0000000000010001)")
        # 0x0001 / 65535 ≈ 0.0000152
        assert coords is not None

    def test_decode_rect64_invalid(self) -> None:
        assert _decode_rect64("garbage") is None

    def test_parse_faces_single(self) -> None:
        faces_str = "rect64(0000000000010001),Alice"
        faces = _parse_faces(faces_str)
        assert len(faces) == 1
        assert faces[0][0] == "Alice"

    def test_parse_faces_multiple(self) -> None:
        faces_str = "rect64(0000000000010001),Alice;rect64(0001000100020002),Bob"
        faces = _parse_faces(faces_str)
        assert len(faces) == 2
        assert {f[0] for f in faces} == {"Alice", "Bob"}

    def test_parse_faces_empty(self) -> None:
        assert _parse_faces("") == []


# ── Picasa importer ───────────────────────────────────────────────────────────

class TestPicasaImporter:
    def _write_ini(self, folder: Path, content: str) -> None:
        (folder / ".picasa.ini").write_text(content, encoding="utf-8")

    def test_reads_star(self, tmp_path: Path) -> None:
        self._write_ini(tmp_path, "[photo.jpg]\nstar=yes\n")
        recs = PicasaImporter().preview(str(tmp_path))
        assert len(recs) == 1
        assert recs[0].flag == 1

    def test_reads_caption(self, tmp_path: Path) -> None:
        self._write_ini(tmp_path, "[photo.jpg]\ncaption=Sunset view\n")
        recs = PicasaImporter().preview(str(tmp_path))
        assert recs[0].caption == "Sunset view"

    def test_reads_album_name(self, tmp_path: Path) -> None:
        ini = (
            "[.album:abc123]\nname=Holiday 2023\ntoken=abc123\n"
            "[photo.jpg]\nalbums=abc123\n"
        )
        self._write_ini(tmp_path, ini)
        recs = PicasaImporter().preview(str(tmp_path))
        assert recs[0].album_names == ["Holiday 2023"]

    def test_reads_faces(self, tmp_path: Path) -> None:
        self._write_ini(tmp_path, "[photo.jpg]\nfaces=rect64(0000000000010001),Alice\n")
        recs = PicasaImporter().preview(str(tmp_path))
        assert len(recs[0].faces) == 1
        assert recs[0].faces[0][0] == "Alice"

    def test_skips_non_photo_sections(self, tmp_path: Path) -> None:
        ini = "[Picasa]\nname=Test\n[.album:x]\nname=X\n[photo.jpg]\nstar=yes\n"
        self._write_ini(tmp_path, ini)
        recs = PicasaImporter().preview(str(tmp_path))
        # Only photo.jpg should be returned
        assert len(recs) == 1
        assert recs[0].filename == "photo.jpg"

    def test_multiple_photos_in_ini(self, tmp_path: Path) -> None:
        ini = "[a.jpg]\nstar=yes\n[b.jpg]\ncaption=Hello\n"
        self._write_ini(tmp_path, ini)
        recs = PicasaImporter().preview(str(tmp_path))
        assert len(recs) == 2

    def test_empty_folder(self, tmp_path: Path) -> None:
        recs = PicasaImporter().preview(str(tmp_path))
        assert recs == []

    def test_subdirectory_ini(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / ".picasa.ini").write_text("[img.jpg]\nstar=yes\n", encoding="utf-8")
        recs = PicasaImporter().preview(str(tmp_path))
        assert len(recs) == 1


# ── Folder importer ───────────────────────────────────────────────────────────

class TestFolderImporter:
    def test_subfolder_becomes_album(self, photo_tree: Path) -> None:
        recs = FolderImporter().preview(str(photo_tree))
        album_names = {name for r in recs for name in r.album_names}
        assert "Vacation" in album_names
        assert "Family" in album_names

    def test_root_photos_in_root_album(self, photo_tree: Path) -> None:
        recs = FolderImporter().preview(str(photo_tree))
        root_recs = [r for r in recs if r.filename == "misc.jpg"]
        assert len(root_recs) == 1
        assert photo_tree.name in root_recs[0].album_names

    def test_yields_correct_filenames(self, photo_tree: Path) -> None:
        recs = FolderImporter().preview(str(photo_tree))
        names = {r.filename for r in recs}
        assert "beach.jpg" in names
        assert "mountain.jpg" in names
        assert "dinner.jpg" in names

    def test_empty_folder(self, tmp_path: Path) -> None:
        recs = FolderImporter().preview(str(tmp_path))
        assert recs == []

    def test_nonexistent_path(self, tmp_path: Path) -> None:
        recs = FolderImporter().preview(str(tmp_path / "nope"))
        assert recs == []


# ── Matching engine ───────────────────────────────────────────────────────────

class TestMatchingEngine:
    def test_match_by_filename(self, cat: Path) -> None:
        recs = [ImportRecord(filename="a.jpg", source_path="/other/a.jpg", rating=3)]
        matched = match_records(recs, cat)
        assert matched[0].matched_photo_id is not None

    def test_match_by_source_path(self, cat: Path) -> None:
        recs = [ImportRecord(filename="a.jpg", source_path="/photos/a.jpg", rating=5)]
        matched = match_records(recs, cat)
        assert matched[0].matched_photo_id is not None

    def test_no_match_unknown_file(self, cat: Path) -> None:
        recs = [ImportRecord(filename="unknown.jpg", source_path="/other/unknown.jpg")]
        matched = match_records(recs, cat)
        assert matched[0].matched_photo_id is None

    def test_ambiguous_filename_not_matched(self, cat: Path) -> None:
        # Add a duplicate filename
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig) VALUES (?,?,?,?)",
                ("a.jpg", "/other/a.jpg", "ok", "other_a"),
            )
        recs = [ImportRecord(filename="a.jpg", source_path="/nowhere/a.jpg")]
        matched = match_records(recs, cat)
        assert matched[0].matched_photo_id is None


# ── Apply engine ──────────────────────────────────────────────────────────────

class TestApplyEngine:
    def _matched_rec(self, cat: Path, filename: str, **kwargs) -> ImportRecord:
        conn = get_connection(cat)
        row = conn.execute("SELECT id FROM photos WHERE filename=?", (filename,)).fetchone()
        rec = ImportRecord(filename=filename, source_path=f"/photos/{filename}", **kwargs)
        rec.matched_photo_id = row["id"]
        return rec

    def test_applies_rating(self, cat: Path) -> None:
        rec = self._matched_rec(cat, "a.jpg", rating=4)
        apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        conn = get_connection(cat)
        row = conn.execute("SELECT rating FROM photos WHERE filename='a.jpg'").fetchone()
        assert row["rating"] == 4

    def test_applies_flag(self, cat: Path) -> None:
        rec = self._matched_rec(cat, "b.jpg", flag=1)
        apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        conn = get_connection(cat)
        row = conn.execute("SELECT flag FROM photos WHERE filename='b.jpg'").fetchone()
        assert row["flag"] == 1

    def test_applies_caption(self, cat: Path) -> None:
        rec = self._matched_rec(cat, "a.jpg", caption="A beautiful sunset")
        apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        conn = get_connection(cat)
        row = conn.execute("SELECT caption FROM photos WHERE filename='a.jpg'").fetchone()
        assert row["caption"] == "A beautiful sunset"

    def test_applies_keywords(self, cat: Path) -> None:
        rec = self._matched_rec(cat, "a.jpg", keywords=["sunset", "beach"])
        apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        conn = get_connection(cat)
        row = conn.execute("SELECT keywords FROM photos WHERE filename='a.jpg'").fetchone()
        assert "sunset" in row["keywords"]
        assert "beach" in row["keywords"]

    def test_creates_collection(self, cat: Path) -> None:
        rec = self._matched_rec(cat, "a.jpg", album_names=["Holiday 2023"])
        stats = apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        assert stats["albums_created"] >= 1
        from core.collections import get_collections
        names = [c["name"] for c in get_collections(cat)]
        assert "Holiday 2023" in names

    def test_adds_photo_to_collection(self, cat: Path) -> None:
        rec = self._matched_rec(cat, "a.jpg", album_names=["MyAlbum"])
        apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        from core.collections import get_collections, photo_in_collection
        cols = get_collections(cat)
        cid = next(c["id"] for c in cols if c["name"] == "MyAlbum")
        conn = get_connection(cat)
        pid = conn.execute("SELECT id FROM photos WHERE filename='a.jpg'").fetchone()["id"]
        assert photo_in_collection(cid, pid, cat)

    def test_conflict_keep_preserves_existing(self, cat: Path) -> None:
        # Set rating to 2 first
        conn = get_connection(cat)
        with conn:
            conn.execute("UPDATE photos SET rating=2 WHERE filename='a.jpg'")
        rec = self._matched_rec(cat, "a.jpg", rating=5)
        apply_records([rec], cat, "test", "/test", CONFLICT_KEEP)
        conn2 = get_connection(cat)
        row = conn2.execute("SELECT rating FROM photos WHERE filename='a.jpg'").fetchone()
        assert row["rating"] == 2  # kept

    def test_conflict_prefer_overwrites(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute("UPDATE photos SET rating=2 WHERE filename='a.jpg'")
        rec = self._matched_rec(cat, "a.jpg", rating=5)
        apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        conn2 = get_connection(cat)
        row = conn2.execute("SELECT rating FROM photos WHERE filename='a.jpg'").fetchone()
        assert row["rating"] == 5

    def test_conflict_merge_keywords(self, cat: Path) -> None:
        conn = get_connection(cat)
        with conn:
            conn.execute("UPDATE photos SET keywords='nature,travel' WHERE filename='a.jpg'")
        rec = self._matched_rec(cat, "a.jpg", keywords=["travel", "sunset"])
        apply_records([rec], cat, "test", "/test", CONFLICT_MERGE)
        conn2 = get_connection(cat)
        row = conn2.execute("SELECT keywords FROM photos WHERE filename='a.jpg'").fetchone()
        kws = set(row["keywords"].split(","))
        assert "nature" in kws
        assert "travel" in kws
        assert "sunset" in kws

    def test_unmatched_records_skipped(self, cat: Path) -> None:
        rec = ImportRecord(filename="ghost.jpg", source_path="/photos/ghost.jpg", rating=3)
        rec.matched_photo_id = None
        stats = apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        assert stats["applied"] == 0

    def test_batch_id_returned(self, cat: Path) -> None:
        rec = self._matched_rec(cat, "a.jpg", rating=3)
        stats = apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        assert stats["batch_id"] is not None
        assert isinstance(stats["batch_id"], int)

    def test_undo_batch(self, cat: Path) -> None:
        # Set up original state
        conn = get_connection(cat)
        with conn:
            conn.execute("UPDATE photos SET rating=2, caption='old' WHERE filename='a.jpg'")
        # Apply
        rec = self._matched_rec(cat, "a.jpg", rating=5, caption="new")
        stats = apply_records([rec], cat, "test", "/test", CONFLICT_PREFER)
        # Verify applied
        conn2 = get_connection(cat)
        row = conn2.execute("SELECT rating, caption FROM photos WHERE filename='a.jpg'").fetchone()
        assert row["rating"] == 5
        # Undo
        result = undo_batch(stats["batch_id"], cat)
        assert result is True
        conn3 = get_connection(cat)
        row2 = conn3.execute("SELECT rating, caption FROM photos WHERE filename='a.jpg'").fetchone()
        assert row2["rating"] == 2
        assert row2["caption"] == "old"

    def test_stats_counts(self, cat: Path) -> None:
        recs = [self._matched_rec(cat, fn, rating=3) for fn in ("a.jpg", "b.jpg")]
        stats = apply_records(recs, cat, "test", "/test", CONFLICT_PREFER)
        assert stats["total"] == 2
        assert stats["matched"] == 2
        assert stats["applied"] == 2


# ── Schema migration: caption/keywords columns ────────────────────────────────

class TestSchemaMigration:
    def test_caption_column_exists(self, cat: Path) -> None:
        conn = get_connection(cat)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()]
        assert "caption" in cols

    def test_keywords_column_exists(self, cat: Path) -> None:
        conn = get_connection(cat)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()]
        assert "keywords" in cols
