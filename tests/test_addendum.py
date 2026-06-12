"""Tests for addendum features: similarity grouping, GPS proximity clustering,
quality component scoring, and updated desktop launcher with icon."""
from __future__ import annotations

import math
import platform
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

import core.db.catalog as _cat_mod
from core.db.catalog import CatalogWriter, get_connection, open_catalog


# ── Shared fixture ────────────────────────────────────────────────────────────

@pytest.fixture()
def cat(tmp_path: Path) -> Path:
    _cat_mod._local.__dict__.clear()
    db = tmp_path / "test.db"
    open_catalog(db)
    return db


def _insert(
    conn,
    filename: str,
    phash: str | None = None,
    clip: str | None = None,
    quality: float | None = None,
    sharpness: float | None = None,
    exposure: float | None = None,
    date_taken: str | None = None,
    gps_lat: float | None = None,
    gps_lon: float | None = None,
    status: str = "ok",
) -> int:
    conn.execute(
        """INSERT INTO photos
           (filename, file_path, status, quick_sig,
            phash, clip_embedding, quality_score, sharpness_score, exposure_score,
            date_taken, gps_lat, gps_lon)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (filename, f"/p/{filename}", status, filename,
         phash, clip, quality, sharpness, exposure,
         date_taken, gps_lat, gps_lon),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── Schema migration check ────────────────────────────────────────────────────

class TestSchemaMigration4:
    def test_sharpness_score_column_exists(self, cat: Path) -> None:
        conn = get_connection(cat)
        conn.execute("UPDATE photos SET sharpness_score=0.5 WHERE 1=0")

    def test_exposure_score_column_exists(self, cat: Path) -> None:
        conn = get_connection(cat)
        conn.execute("UPDATE photos SET exposure_score=0.5 WHERE 1=0")

    def test_similarity_groups_table_exists(self, cat: Path) -> None:
        conn = get_connection(cat)
        conn.execute("SELECT id FROM similarity_groups LIMIT 0")

    def test_photo_similarity_group_table_exists(self, cat: Path) -> None:
        conn = get_connection(cat)
        conn.execute("SELECT photo_id FROM photo_similarity_group LIMIT 0")


# ── Quality component scoring ─────────────────────────────────────────────────

class TestQualityComponents:
    def test_returns_three_values(self, tmp_path: Path) -> None:
        from PIL import Image
        img = Image.new("RGB", (64, 64), (128, 128, 128))
        p = tmp_path / "test.jpg"
        img.save(p)
        from core.quality import compute_quality_components
        result = compute_quality_components(p)
        assert result is not None
        total, sharp, exp = result
        assert 0.0 <= total <= 1.0
        assert 0.0 <= sharp <= 1.0
        assert 0.0 <= exp <= 1.0

    def test_sharp_image_has_higher_sharpness(self, tmp_path: Path) -> None:
        from PIL import Image
        import numpy as np_local
        from core.quality import compute_quality_components

        # Solid grey → blurry (zero Laplacian variance)
        flat = Image.fromarray(np_local.full((64, 64), 128, dtype=np_local.uint8))
        flat.save(tmp_path / "flat.jpg")

        # Checkerboard → sharp (high Laplacian variance)
        chess = np_local.zeros((64, 64), dtype=np_local.uint8)
        chess[::2, ::2] = 255
        chess[1::2, 1::2] = 255
        Image.fromarray(chess).save(tmp_path / "chess.jpg")

        _, sharp_flat, _ = compute_quality_components(tmp_path / "flat.jpg")
        _, sharp_chess, _ = compute_quality_components(tmp_path / "chess.jpg")
        assert sharp_chess > sharp_flat

    def test_overexposed_image_has_lower_exposure(self, tmp_path: Path) -> None:
        from PIL import Image
        import numpy as np_local
        from core.quality import compute_quality_components

        normal = Image.fromarray(np_local.full((64, 64), 128, dtype=np_local.uint8))
        normal.save(tmp_path / "normal.jpg")

        blown = Image.fromarray(np_local.full((64, 64), 255, dtype=np_local.uint8))
        blown.save(tmp_path / "blown.jpg")

        _, _, exp_normal = compute_quality_components(tmp_path / "normal.jpg")
        _, _, exp_blown = compute_quality_components(tmp_path / "blown.jpg")
        assert exp_blown < exp_normal

    def test_returns_none_for_missing_file(self) -> None:
        from core.quality import compute_quality_components
        assert compute_quality_components("/nonexistent/file.jpg") is None

    def test_compute_quality_score_backward_compatible(self, tmp_path: Path) -> None:
        from PIL import Image
        img = Image.new("RGB", (64, 64), (100, 100, 100))
        p = tmp_path / "t.jpg"
        img.save(p)
        from core.quality import compute_quality_score
        score = compute_quality_score(p)
        assert score is not None
        assert 0.0 <= score <= 1.0


# ── Grouping: similarity functions ────────────────────────────────────────────

class TestSimilarityFunctions:
    def test_hamming_identical(self) -> None:
        from core.grouping import _hamming
        assert _hamming("0000000000000000", "0000000000000000") == 0

    def test_hamming_different(self) -> None:
        from core.grouping import _hamming
        # Each hex digit represents 4 bits; 'f' vs '0' = 4 bit differences
        assert _hamming("f000000000000000", "0000000000000000") == 4

    def test_phash_similarity_identical(self) -> None:
        from core.grouping import _phash_similarity
        assert _phash_similarity("aabbccdd00112233", "aabbccdd00112233") == 1.0

    def test_phash_similarity_different(self) -> None:
        from core.grouping import _phash_similarity
        s = _phash_similarity("ffffffffffffffff", "0000000000000000")
        assert s == 0.0

    def test_clip_similarity_identical(self) -> None:
        import json
        from core.grouping import _clip_similarity
        v = np.ones(512, dtype=np.float32)
        v /= np.linalg.norm(v)
        emb = json.dumps(v.tolist())
        assert abs(_clip_similarity(emb, emb) - 1.0) < 1e-5

    def test_clip_similarity_orthogonal(self) -> None:
        import json
        from core.grouping import _clip_similarity
        a = np.zeros(512, dtype=np.float32); a[0] = 1.0
        b = np.zeros(512, dtype=np.float32); b[1] = 1.0
        assert abs(_clip_similarity(json.dumps(a.tolist()), json.dumps(b.tolist())) - 0.5) < 1e-4

    def test_burst_bonus_within_window(self) -> None:
        from core.grouping import _burst_bonus
        assert _burst_bonus("2024:01:15 10:00:00", "2024:01:15 10:00:03", seconds=5) == 1.0

    def test_burst_bonus_outside_window(self) -> None:
        from core.grouping import _burst_bonus
        assert _burst_bonus("2024:01:15 10:00:00", "2024:01:15 10:01:00", seconds=5) == 0.0

    def test_burst_bonus_missing_date(self) -> None:
        from core.grouping import _burst_bonus
        assert _burst_bonus(None, "2024:01:15 10:00:00") == 0.0

    def test_combined_similarity_high_for_identical_phash(self, cat: Path) -> None:
        from core.grouping import compute_combined_similarity
        conn = get_connection(cat)
        with conn:
            id1 = _insert(conn, "a.jpg", phash="aabbccdd00112233")
            id2 = _insert(conn, "b.jpg", phash="aabbccdd00112233")
        rows = conn.execute("SELECT id, phash, clip_embedding, quality_score, date_taken FROM photos WHERE id IN (?,?)", (id1, id2)).fetchall()
        row_a, row_b = {r["id"]: r for r in rows}[id1], {r["id"]: r for r in rows}[id2]
        s = compute_combined_similarity(row_a, row_b)
        assert s > 0.7

    def test_combined_similarity_low_for_different_phash(self, cat: Path) -> None:
        from core.grouping import compute_combined_similarity
        conn = get_connection(cat)
        with conn:
            id1 = _insert(conn, "c.jpg", phash="0000000000000000")
            id2 = _insert(conn, "d.jpg", phash="ffffffffffffffff")
        rows = conn.execute("SELECT id, phash, clip_embedding, quality_score, date_taken FROM photos WHERE id IN (?,?)", (id1, id2)).fetchall()
        row_c, row_d = {r["id"]: r for r in rows}[id1], {r["id"]: r for r in rows}[id2]
        s = compute_combined_similarity(row_c, row_d)
        assert s < 0.15


# ── Grouping: group_photos / get_similarity_groups ────────────────────────────

class TestGroupPhotos:
    def test_groups_near_duplicate_phashes(self, cat: Path) -> None:
        from core.grouping import group_photos, get_similarity_groups
        conn = get_connection(cat)
        with conn:
            id1 = _insert(conn, "a.jpg", phash="aabb000000000000", quality=0.8)
            id2 = _insert(conn, "b.jpg", phash="aabb000000000001", quality=0.6)
            id3 = _insert(conn, "c.jpg", phash="0000ffffffffffff", quality=0.7)

        result = group_photos([id1, id2, id3], cat, threshold=0.6, scope="test1")
        assert result["groups_created"] >= 1

        groups = get_similarity_groups("test1", cat)
        assert len(groups) >= 1
        # The near-identical pair should share a group
        grouped_ids = {p["photo_id"] for g in groups for p in g["photos"]}
        assert id1 in grouped_ids
        assert id2 in grouped_ids

    def test_suggested_best_is_highest_quality(self, cat: Path) -> None:
        from core.grouping import group_photos, get_similarity_groups
        conn = get_connection(cat)
        with conn:
            id_low = _insert(conn, "low.jpg", phash="aabb000000000000", quality=0.3)
            id_high = _insert(conn, "high.jpg", phash="aabb000000000001", quality=0.9)

        group_photos([id_low, id_high], cat, threshold=0.5, scope="test2")
        groups = get_similarity_groups("test2", cat)
        assert len(groups) == 1
        best = next(p for p in groups[0]["photos"] if p["is_suggested_best"])
        assert best["photo_id"] == id_high

    def test_isolated_photo_not_grouped(self, cat: Path) -> None:
        from core.grouping import group_photos, get_similarity_groups
        conn = get_connection(cat)
        with conn:
            id1 = _insert(conn, "x.jpg", phash="0000000000000000", quality=0.5)
            id2 = _insert(conn, "y.jpg", phash="ffffffffffffffff", quality=0.5)

        result = group_photos([id1, id2], cat, threshold=0.9, scope="test3")
        # Very different phash → should not group
        groups = get_similarity_groups("test3", cat)
        assert all(len(g["photos"]) >= 2 for g in groups)
        # Both should remain ungrouped at threshold 0.9
        assert result["groups_created"] == 0

    def test_clear_groups_removes_them(self, cat: Path) -> None:
        from core.grouping import group_photos, get_similarity_groups, clear_groups_for_scope
        conn = get_connection(cat)
        with conn:
            id1 = _insert(conn, "p.jpg", phash="aabb000000000000", quality=0.8)
            id2 = _insert(conn, "q.jpg", phash="aabb000000000001", quality=0.5)

        group_photos([id1, id2], cat, threshold=0.5, scope="scopeX")
        assert len(get_similarity_groups("scopeX", cat)) >= 1
        clear_groups_for_scope("scopeX", cat)
        assert get_similarity_groups("scopeX", cat) == []

    def test_burst_photos_grouped_without_phash(self, cat: Path) -> None:
        from core.grouping import group_photos, get_similarity_groups
        conn = get_connection(cat)
        with conn:
            id1 = _insert(conn, "burst1.jpg", date_taken="2024:01:15 10:00:00", quality=0.8)
            id2 = _insert(conn, "burst2.jpg", date_taken="2024:01:15 10:00:02", quality=0.6)
            id3 = _insert(conn, "other.jpg",  date_taken="2024:01:20 10:00:00", quality=0.7)

        # threshold=0.1 so burst_bonus alone (0.15) passes
        result = group_photos([id1, id2, id3], cat, threshold=0.1, scope="burst_test")
        groups = get_similarity_groups("burst_test", cat)
        burst_group = next((g for g in groups if len(g["photos"]) == 2), None)
        assert burst_group is not None

    def test_get_photo_group_returns_correct_group(self, cat: Path) -> None:
        from core.grouping import group_photos, get_photo_group
        conn = get_connection(cat)
        with conn:
            id1 = _insert(conn, "r.jpg", phash="aabb000000000000", quality=0.8)
            id2 = _insert(conn, "s.jpg", phash="aabb000000000001", quality=0.5)

        group_photos([id1, id2], cat, threshold=0.5, scope="pg_test")
        g = get_photo_group(id1, cat)
        assert g is not None
        pids = [p["photo_id"] for p in g["photos"]]
        assert id1 in pids
        assert id2 in pids

    def test_auto_pick_best_adds_to_collection(self, cat: Path) -> None:
        from core.grouping import group_photos, auto_pick_best_of_groups
        from core.collections import create_collection
        conn = get_connection(cat)
        with conn:
            id1 = _insert(conn, "m.jpg", phash="aabb000000000000", quality=0.9)
            id2 = _insert(conn, "n.jpg", phash="aabb000000000001", quality=0.3)

        group_photos([id1, id2], cat, threshold=0.5, scope="pick_test")
        cid = create_collection("Keepers", catalog_path=cat)
        result = auto_pick_best_of_groups("pick_test", cid, cat)
        assert result["added"] == 1

        conn2 = get_connection(cat)
        member = conn2.execute(
            "SELECT photo_id FROM collection_photos WHERE collection_id=?", (cid,)
        ).fetchone()
        assert member is not None
        assert member["photo_id"] == id1  # id1 has quality 0.9


# ── GPS proximity clustering ──────────────────────────────────────────────────

class TestGPSProximityClustering:
    def test_haversine_formula(self) -> None:
        from core.places import _haversine_km
        # Paris → London ~340km
        d = _haversine_km(48.8566, 2.3522, 51.5074, -0.1278)
        assert 330 < d < 360

    def test_same_point_distance_zero(self) -> None:
        from core.places import _haversine_km
        assert _haversine_km(10.0, 20.0, 10.0, 20.0) == pytest.approx(0.0, abs=1e-6)

    def test_nearby_places_merged(self, cat: Path) -> None:
        from core.places import cluster_by_gps_proximity
        conn = get_connection(cat)
        with conn:
            # Two places 0.1 km apart → should be merged
            conn.execute(
                "INSERT INTO places (city, region, country, lat, lon) VALUES (?,?,?,?,?)",
                ("TestCity", "TestRegion", "TC", 48.8566, 2.3522),
            )
            place1_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO places (city, region, country, lat, lon) VALUES (?,?,?,?,?)",
                ("TestCity", "TestRegion", "TC", 48.8570, 2.3526),
            )
            place2_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Add one photo to each place
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig, place_id) VALUES (?,?,?,?,?)",
                ("a.jpg", "/p/a.jpg", "ok", "a", place1_id),
            )
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig, place_id) VALUES (?,?,?,?,?)",
                ("b.jpg", "/p/b.jpg", "ok", "b", place2_id),
            )

        result = cluster_by_gps_proximity(cat, radius_km=0.5)
        assert result["merges"] == 1
        assert result["places_removed"] == 1

        conn2 = get_connection(cat)
        remaining_places = conn2.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        assert remaining_places == 1

    def test_far_places_not_merged(self, cat: Path) -> None:
        from core.places import cluster_by_gps_proximity
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO places (city, region, country, lat, lon) VALUES (?,?,?,?,?)",
                ("Paris", "IDF", "FR", 48.8566, 2.3522),
            )
            conn.execute(
                "INSERT INTO places (city, region, country, lat, lon) VALUES (?,?,?,?,?)",
                ("London", "ENG", "GB", 51.5074, -0.1278),
            )

        result = cluster_by_gps_proximity(cat, radius_km=0.5)
        assert result["merges"] == 0
        conn2 = get_connection(cat)
        assert conn2.execute("SELECT COUNT(*) FROM places").fetchone()[0] == 2

    def test_photos_reassigned_after_merge(self, cat: Path) -> None:
        from core.places import cluster_by_gps_proximity
        conn = get_connection(cat)
        with conn:
            conn.execute(
                "INSERT INTO places (city, region, country, lat, lon) VALUES (?,?,?,?,?)",
                ("A", "R", "C", 10.0, 10.0),
            )
            pid1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO places (city, region, country, lat, lon) VALUES (?,?,?,?,?)",
                ("B", "R", "C", 10.001, 10.001),
            )
            pid2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # pid1 has 2 photos (will be canonical), pid2 has 1
            for fn in ("x.jpg", "y.jpg"):
                conn.execute(
                    "INSERT INTO photos (filename, file_path, status, quick_sig, place_id) VALUES (?,?,?,?,?)",
                    (fn, f"/p/{fn}", "ok", fn, pid1),
                )
            conn.execute(
                "INSERT INTO photos (filename, file_path, status, quick_sig, place_id) VALUES (?,?,?,?,?)",
                ("z.jpg", "/p/z.jpg", "ok", "z", pid2),
            )

        cluster_by_gps_proximity(cat, radius_km=1.0)

        conn2 = get_connection(cat)
        # z.jpg should now point to pid1 (more photos)
        row = conn2.execute("SELECT place_id FROM photos WHERE filename='z.jpg'").fetchone()
        assert row["place_id"] == pid1

    def test_empty_catalog_no_crash(self, cat: Path) -> None:
        from core.places import cluster_by_gps_proximity
        result = cluster_by_gps_proximity(cat, radius_km=0.5)
        assert result["merges"] == 0


# ── Desktop launcher: icon installation ───────────────────────────────────────

class TestDesktopLauncherWithIcon:
    def test_install_creates_icon_in_hicolor(self, tmp_path: Path) -> None:
        if platform.system() != "Linux":
            pytest.skip("Linux-only test")

        app_dir = tmp_path / "app"
        app_dir.mkdir()
        icon_dir = app_dir / "assets" / "icon"
        icon_dir.mkdir(parents=True)
        (icon_dir / "picurate.png").write_bytes(b"PNG_FAKE")

        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch("pathlib.Path.home", return_value=fake_home):
            from core.firstrun import install_desktop_launcher
            result = install_desktop_launcher(app_dir)

        assert result is True
        icon_dest = fake_home / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "picurate.png"
        assert icon_dest.exists()

    def test_desktop_file_references_picurate_icon(self, tmp_path: Path) -> None:
        if platform.system() != "Linux":
            pytest.skip("Linux-only test")

        app_dir = tmp_path / "app"
        app_dir.mkdir()
        icon_dir = app_dir / "assets" / "icon"
        icon_dir.mkdir(parents=True)
        (icon_dir / "picurate.png").write_bytes(b"PNG_FAKE")

        fake_home = tmp_path / "home2"
        fake_home.mkdir()

        with patch("pathlib.Path.home", return_value=fake_home):
            from core.firstrun import install_desktop_launcher
            install_desktop_launcher(app_dir)

        desktop = fake_home / ".local" / "share" / "applications" / "picurate.desktop"
        content = desktop.read_text()
        assert "Icon=picurate" in content
        assert "Categories=Graphics;Photography;" in content
        assert "StartupNotify=true" in content
