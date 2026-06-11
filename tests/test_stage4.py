"""Stage 4 headless tests: export engine, HTML gallery, contact sheet."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from core.db.catalog import get_connection, open_catalog
from core.collections import create_collection, add_photo
from core.export import (
    ExportOptions,
    LAYOUT_FLAT, LAYOUT_BY_YEAR, LAYOUT_BY_DATE,
    NAMING_ORIGINAL, NAMING_SEQUENTIAL, NAMING_DATE,
    export_collection,
    _hash_file,
    _unique_path,
    _dest_subdir,
    _dest_name,
)
from core.gallery import generate_gallery
from core.contact_sheet import generate_contact_sheet


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_jpeg(path: Path, w: int = 200, h: int = 150, color=(100, 150, 200)) -> Path:
    img = Image.new("RGB", (w, h), color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "JPEG", quality=80)
    return path


@pytest.fixture()
def env(tmp_path: Path):
    """Catalog + 3 real JPEG files + 1 collection containing them."""
    import core.db.catalog as _cat_mod
    _cat_mod._local.__dict__.clear()

    db = tmp_path / "test.db"
    photos_dir = tmp_path / "photos"
    thumb_dir = tmp_path / "thumbs"
    thumb_dir.mkdir()

    open_catalog(db)
    conn = get_connection(db)

    files = []
    for i, color in enumerate([(200, 100, 50), (50, 200, 100), (100, 50, 200)], start=1):
        fp = photos_dir / f"photo_{i:02d}.jpg"
        tp = thumb_dir / f"thumb_{i:02d}.jpg"
        _make_jpeg(fp, color=color)
        _make_jpeg(tp, w=128, h=96, color=color)
        files.append((fp, tp))

    ids = []
    with conn:
        for i, (fp, tp) in enumerate(files, start=1):
            conn.execute(
                """INSERT INTO photos
                   (filename, file_path, thumbnail_path, date_taken, status, quick_sig,
                    rating, flag)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (fp.name, str(fp), str(tp), f"2024-0{i}-15", "ok", fp.name, i, 0),
            )
            ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    cid = create_collection("Test Export", catalog_path=db)
    for pid in ids:
        add_photo(cid, pid, db)

    return {"db": db, "collection_id": cid, "photo_ids": ids, "files": files, "tmp": tmp_path}


# ── Hash utilities ────────────────────────────────────────────────────────────

class TestHashUtils:
    def test_hash_file_consistent(self, tmp_path: Path) -> None:
        f = tmp_path / "a.bin"
        f.write_bytes(b"hello world")
        assert _hash_file(f) == _hash_file(f)

    def test_different_files_differ(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"aaa")
        f2.write_bytes(b"bbb")
        assert _hash_file(f1) != _hash_file(f2)

    def test_unique_path_no_collision(self, tmp_path: Path) -> None:
        p = tmp_path / "x.jpg"
        assert _unique_path(p) == p

    def test_unique_path_collision(self, tmp_path: Path) -> None:
        p = tmp_path / "x.jpg"
        p.write_bytes(b"")
        result = _unique_path(p)
        assert result != p
        assert result.name == "x_2.jpg"

    def test_unique_path_double_collision(self, tmp_path: Path) -> None:
        p = tmp_path / "x.jpg"
        p.write_bytes(b"")
        (tmp_path / "x_2.jpg").write_bytes(b"")
        result = _unique_path(p)
        assert result.name == "x_3.jpg"


# ── Naming and layout helpers ─────────────────────────────────────────────────

class TestNamingAndLayout:
    def _row(self, filename="img.jpg", date="2024-05-15"):
        class R(dict): pass
        return {"filename": filename, "date_taken": date}

    def test_naming_original(self) -> None:
        row = self._row("photo.jpg")
        opts = ExportOptions(naming=NAMING_ORIGINAL)
        assert _dest_name(row, opts, 1, ".jpg") == "photo.jpg"

    def test_naming_sequential(self) -> None:
        row = self._row("photo.jpg")
        opts = ExportOptions(naming=NAMING_SEQUENTIAL)
        name = _dest_name(row, opts, 7, ".jpg")
        assert name == "0007_photo.jpg"

    def test_naming_date(self) -> None:
        row = self._row("photo.jpg", date="2024-05-15")
        opts = ExportOptions(naming=NAMING_DATE)
        name = _dest_name(row, opts, 1, ".jpg")
        assert name == "20240515_photo.jpg"

    def test_layout_flat(self) -> None:
        row = self._row(date="2024-05-15")
        opts = ExportOptions(layout=LAYOUT_FLAT)
        assert _dest_subdir(row, opts) == ""

    def test_layout_by_year(self) -> None:
        row = self._row(date="2024-05-15")
        opts = ExportOptions(layout=LAYOUT_BY_YEAR)
        assert _dest_subdir(row, opts) == "2024"

    def test_layout_by_date(self) -> None:
        row = self._row(date="2024-05-15")
        opts = ExportOptions(layout=LAYOUT_BY_DATE)
        assert _dest_subdir(row, opts) == "2024/05"

    def test_layout_undated(self) -> None:
        row = self._row(date=None)
        opts = ExportOptions(layout=LAYOUT_BY_YEAR)
        assert _dest_subdir(row, opts) == "undated"


# ── Export: originals ─────────────────────────────────────────────────────────

class TestExportOriginals:
    def test_basic_export(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        opts = ExportOptions()
        stats = export_collection(env["collection_id"], dest, opts, env["db"])
        assert stats["exported"] == 3
        assert stats["errors"] == 0
        assert stats["verify_failures"] == 0

    def test_files_exist_in_dest(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        opts = ExportOptions()
        export_collection(env["collection_id"], dest, opts, env["db"])
        jpegs = list(dest.glob("*.jpg"))
        assert len(jpegs) == 3

    def test_originals_hash_match(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        opts = ExportOptions()
        export_collection(env["collection_id"], dest, opts, env["db"])
        src_files = [fp for fp, _ in env["files"]]
        dest_files = sorted(dest.glob("*.jpg"))
        # Hashes should all be accounted for
        src_hashes = {_hash_file(f) for f in src_files}
        dest_hashes = {_hash_file(f) for f in dest_files}
        assert src_hashes == dest_hashes

    def test_empty_collection_exports_zero(self, env: dict, tmp_path: Path) -> None:
        import core.db.catalog as _cat_mod
        _cat_mod._local.__dict__.clear()
        from core.db.catalog import get_connection, open_catalog
        empty_cid = create_collection("Empty", catalog_path=env["db"])
        dest = tmp_path / "empty_out"
        opts = ExportOptions()
        stats = export_collection(empty_cid, dest, opts, env["db"])
        assert stats["exported"] == 0

    def test_collision_handling(self, env: dict, tmp_path: Path) -> None:
        """Exporting same collection twice to same dest should not overwrite."""
        dest = tmp_path / "out"
        opts = ExportOptions()
        export_collection(env["collection_id"], dest, opts, env["db"])
        # Export again — collision resolution should create _2 suffixed files
        stats2 = export_collection(env["collection_id"], dest, opts, env["db"])
        assert stats2["exported"] == 3
        all_jpegs = list(dest.glob("*.jpg"))
        assert len(all_jpegs) == 6  # 3 original + 3 with _2 suffix


# ── Export: resize ────────────────────────────────────────────────────────────

class TestExportResize:
    def test_resize_reduces_dimensions(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        opts = ExportOptions(resize=True, max_dim=100)
        export_collection(env["collection_id"], dest, opts, env["db"])
        for f in dest.glob("*.jpg"):
            img = Image.open(f)
            assert max(img.size) <= 100

    def test_resize_stats_ok(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        opts = ExportOptions(resize=True, max_dim=100)
        stats = export_collection(env["collection_id"], dest, opts, env["db"])
        assert stats["exported"] == 3
        assert stats["errors"] == 0


# ── Export: layout ────────────────────────────────────────────────────────────

class TestExportLayout:
    def test_by_year_creates_subfolders(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        opts = ExportOptions(layout=LAYOUT_BY_YEAR)
        export_collection(env["collection_id"], dest, opts, env["db"])
        subdirs = [d for d in dest.iterdir() if d.is_dir()]
        assert len(subdirs) >= 1
        assert any(d.name == "2024" for d in subdirs)

    def test_by_date_creates_year_month(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        opts = ExportOptions(layout=LAYOUT_BY_DATE)
        export_collection(env["collection_id"], dest, opts, env["db"])
        # Should have 2024/01, 2024/02, 2024/03
        month_dirs = list((dest / "2024").glob("*"))
        assert len(month_dirs) >= 1


# ── Export: GPS strip ─────────────────────────────────────────────────────────

class TestExportGpsStrip:
    def test_strip_gps_no_crash(self, env: dict, tmp_path: Path) -> None:
        """GPS strip should succeed even if source has no GPS EXIF."""
        dest = tmp_path / "out"
        opts = ExportOptions(strip_gps=True)
        stats = export_collection(env["collection_id"], dest, opts, env["db"])
        assert stats["exported"] == 3
        assert stats["errors"] == 0


# ── Export: HTML gallery ──────────────────────────────────────────────────────

class TestHTMLGallery:
    def test_generates_index_html(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "gallery"
        conn = get_connection(env["db"])
        from core.query import get_photos
        rows = get_photos(conn, collection_id=env["collection_id"])
        result = generate_gallery(rows, dest, title="My Gallery")
        assert result.exists()
        assert result.name == "index.html"

    def test_html_contains_title(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "gallery"
        conn = get_connection(env["db"])
        from core.query import get_photos
        rows = get_photos(conn, collection_id=env["collection_id"])
        result = generate_gallery(rows, dest, title="Vacation 2024")
        content = result.read_text(encoding="utf-8")
        assert "Vacation 2024" in content

    def test_html_is_self_contained(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "gallery"
        conn = get_connection(env["db"])
        from core.query import get_photos
        rows = get_photos(conn, collection_id=env["collection_id"])
        generate_gallery(rows, dest)
        index = dest / "index.html"
        content = index.read_text(encoding="utf-8")
        # Must not reference external CDNs
        assert "cdn." not in content
        assert "https://fonts" not in content

    def test_images_folder_created(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "gallery"
        conn = get_connection(env["db"])
        from core.query import get_photos
        rows = get_photos(conn, collection_id=env["collection_id"])
        generate_gallery(rows, dest)
        assert (dest / "images").is_dir()
        thumbs = list((dest / "images").glob("*.jpg"))
        assert len(thumbs) == 3

    def test_html_via_export_option(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        opts = ExportOptions(html_gallery=True)
        stats = export_collection(env["collection_id"], dest, opts, env["db"])
        assert stats["exported"] == 3
        assert (dest / "index.html").exists()


# ── Export: contact sheet ─────────────────────────────────────────────────────

class TestContactSheet:
    def test_generates_pdf(self, env: dict, tmp_path: Path) -> None:
        conn = get_connection(env["db"])
        from core.query import get_photos
        rows = get_photos(conn, collection_id=env["collection_id"])
        out = tmp_path / "sheet.pdf"
        result = generate_contact_sheet(rows, out)
        assert result.exists()
        assert result.stat().st_size > 0

    def test_pdf_magic_bytes(self, env: dict, tmp_path: Path) -> None:
        conn = get_connection(env["db"])
        from core.query import get_photos
        rows = get_photos(conn, collection_id=env["collection_id"])
        out = tmp_path / "sheet.pdf"
        generate_contact_sheet(rows, out)
        assert out.read_bytes()[:4] == b"%PDF"

    def test_contact_sheet_via_export_option(self, env: dict, tmp_path: Path) -> None:
        dest = tmp_path / "out"
        opts = ExportOptions(contact_sheet=True)
        stats = export_collection(env["collection_id"], dest, opts, env["db"])
        assert stats["exported"] == 3
        assert (dest / "contact_sheet.pdf").exists()


# ── Progress callback ─────────────────────────────────────────────────────────

class TestExportProgress:
    def test_progress_called(self, env: dict, tmp_path: Path) -> None:
        calls: list[tuple[int, int]] = []
        opts = ExportOptions()
        export_collection(
            env["collection_id"], tmp_path / "out", opts, env["db"],
            progress_cb=lambda done, total: calls.append((done, total)),
        )
        assert len(calls) > 0
        # Final call should be (total, total)
        assert calls[-1][0] == calls[-1][1] == 3
