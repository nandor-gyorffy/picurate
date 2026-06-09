"""Stage 1 tests: hashing, EXIF, thumbnails, scanning, move-relink, error isolation."""
import shutil
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


# ── Hashing ──────────────────────────────────────────────────────────────────

def test_quick_signature_stable(tmp_path):
    from core.hashing import quick_signature
    f = tmp_path / "a.jpg"
    f.write_bytes(b"hello world")
    s1 = quick_signature(f)
    s2 = quick_signature(f)
    assert s1 == s2


def test_quick_signature_changes_on_write(tmp_path):
    from core.hashing import quick_signature
    f = tmp_path / "a.jpg"
    f.write_bytes(b"hello world")
    s1 = quick_signature(f)
    time.sleep(0.01)
    f.write_bytes(b"hello world!")
    s2 = quick_signature(f)
    assert s1 != s2


def test_partial_hash_stable(tmp_path):
    from core.hashing import partial_hash
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x" * 1000)
    assert partial_hash(f) == partial_hash(f)


def test_full_hash_matches_known(tmp_path):
    import hashlib
    from core.hashing import full_hash
    data = b"picurate test content"
    f = tmp_path / "a.jpg"
    f.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert full_hash(f) == expected


def test_partial_vs_full_hash_different_for_large(tmp_path):
    from core.hashing import partial_hash, full_hash
    # Make a file > 64KB so partial != full (different algorithm)
    f = tmp_path / "big.jpg"
    f.write_bytes(b"A" * (200 * 1024))
    # They should not be equal since partial_hash is size+first+last, full is sha256 of whole
    ph = partial_hash(f)
    fh = full_hash(f)
    assert ph != fh  # different algorithms


# ── EXIF extraction ───────────────────────────────────────────────────────────

def test_exif_date_extracted():
    from core.exif import extract
    result = extract(FIXTURES / "basic.jpg")
    assert "date_taken" in result
    assert "2023" in result["date_taken"]


def test_exif_camera_extracted():
    from core.exif import extract
    result = extract(FIXTURES / "basic.jpg")
    assert result.get("camera_make") == "Canon"
    assert result.get("camera_model") == "EOS R5"


def test_exif_gps_extracted():
    from core.exif import extract
    result = extract(FIXTURES / "gps.jpg")
    assert result.get("gps_lat") is not None
    assert abs(result["gps_lat"] - 47.5) < 0.1
    assert abs(result["gps_lon"] - 19.0) < 0.1


def test_exif_mtime_fallback(tmp_path):
    from core.exif import extract
    # PNG has no EXIF date
    src = FIXTURES / "plain.png"
    result = extract(src)
    assert "date_taken" in result


def test_exif_dimensions():
    from core.exif import extract
    result = extract(FIXTURES / "basic.jpg")
    assert result["width"] == 64
    assert result["height"] == 48


# ── Thumbnails ────────────────────────────────────────────────────────────────

def test_thumbnail_generated(tmp_path, monkeypatch):
    from core import thumbnails, paths
    monkeypatch.setattr(paths, "thumbnail_dir", lambda: tmp_path)
    thumb = thumbnails.get_thumbnail(FIXTURES / "basic.jpg")
    assert thumb is not None
    assert thumb.exists()


def test_thumbnail_cached(tmp_path, monkeypatch):
    from core import thumbnails, paths
    monkeypatch.setattr(paths, "thumbnail_dir", lambda: tmp_path)
    t1 = thumbnails.get_thumbnail(FIXTURES / "basic.jpg")
    t2 = thumbnails.get_thumbnail(FIXTURES / "basic.jpg")
    assert t1 == t2


def test_thumbnail_orientation_applied(tmp_path, monkeypatch):
    """Rotated JPEG (orientation=6) should produce a portrait thumbnail."""
    from PIL import Image
    from core import thumbnails, paths
    monkeypatch.setattr(paths, "thumbnail_dir", lambda: tmp_path)
    thumb = thumbnails.get_thumbnail(FIXTURES / "rotated.jpg")
    assert thumb is not None
    img = Image.open(thumb)
    # orientation 6 = 90° CW → physical 80×40 becomes 40×80 (portrait)
    assert img.height > img.width


def test_thumbnail_bad_file_returns_none(tmp_path, monkeypatch):
    from core import thumbnails, paths
    monkeypatch.setattr(paths, "thumbnail_dir", lambda: tmp_path)
    bad = tmp_path / "not_an_image.jpg"
    bad.write_bytes(b"not an image at all")
    result = thumbnails.get_thumbnail(bad)
    assert result is None


# ── Scanner ───────────────────────────────────────────────────────────────────

def test_scan_inserts_photos(tmp_path):
    from core.db.catalog import open_catalog, get_connection
    from core.scanner import scan_folder

    # Copy fixtures to a temp folder
    src = tmp_path / "photos"
    shutil.copytree(FIXTURES, src)

    db = tmp_path / "catalog.db"
    open_catalog(db)

    stats = scan_folder(src, db)
    assert stats["inserted"] >= 3  # basic.jpg, gps.jpg, rotated.jpg, plain.png

    conn = get_connection(db)
    count = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    assert count >= 3


def test_scan_skips_unchanged(tmp_path):
    from core.db.catalog import open_catalog
    from core.scanner import scan_folder

    src = tmp_path / "photos"
    shutil.copytree(FIXTURES, src)
    db = tmp_path / "catalog.db"
    open_catalog(db)

    stats1 = scan_folder(src, db)
    stats2 = scan_folder(src, db)
    assert stats2["inserted"] == 0
    assert stats2["updated"] == 0


def test_scan_error_isolation(tmp_path):
    """One corrupt file must not abort the scan."""
    from core.db.catalog import open_catalog, get_connection
    from core.scanner import scan_folder

    src = tmp_path / "photos"
    src.mkdir()
    (src / "good.jpg").write_bytes((FIXTURES / "basic.jpg").read_bytes())
    (src / "corrupt.jpg").write_bytes(b"NOT AN IMAGE AT ALL!!!")

    db = tmp_path / "catalog.db"
    open_catalog(db)

    # corrupt file may raise during EXIF extraction but scan must complete
    stats = scan_folder(src, db)
    conn = get_connection(db)
    # The good file should be in the catalog regardless
    count = conn.execute("SELECT COUNT(*) FROM photos WHERE filename='good.jpg'").fetchone()[0]
    assert count == 1


# ── Move/rename relink ────────────────────────────────────────────────────────

def test_move_relink(tmp_path):
    """Moving a file should relink it, not insert a duplicate."""
    from core.db.catalog import open_catalog, get_connection
    from core.scanner import scan_folder, mark_missing

    src = tmp_path / "photos"
    src.mkdir()
    orig = src / "original.jpg"
    orig.write_bytes((FIXTURES / "basic.jpg").read_bytes())

    db = tmp_path / "catalog.db"
    open_catalog(db)

    stats1 = scan_folder(src, db)
    assert stats1["inserted"] == 1

    # Simulate move: copy to new name, then delete original
    moved = src / "renamed.jpg"
    shutil.copy2(orig, moved)
    orig.unlink()

    # Mark the (now-gone) original as missing
    mark_missing(src, db)
    conn = get_connection(db)
    row = conn.execute("SELECT status FROM photos WHERE filename='original.jpg'").fetchone()
    assert row["status"] == "missing"

    stats2 = scan_folder(src, db)
    assert stats2["relinked"] == 1

    conn2 = get_connection(db)
    total = conn2.execute("SELECT COUNT(*) FROM photos WHERE filename IN ('original.jpg','renamed.jpg')").fetchone()[0]
    # Should be 1 row (relinked), not 2
    assert total == 1


# ── Backup & integrity ────────────────────────────────────────────────────────

def test_backup_creates_file(tmp_path, monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "backup_dir", lambda: tmp_path / "backups")
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)

    db = tmp_path / "catalog.db"
    from core.db.catalog import open_catalog, backup
    open_catalog(db)
    result = backup(db)
    assert result is not None
    assert result.exists()


def test_integrity_check_on_new_db(tmp_path):
    from core.db.catalog import open_catalog, integrity_check
    db = tmp_path / "catalog.db"
    open_catalog(db)
    assert integrity_check(db)
