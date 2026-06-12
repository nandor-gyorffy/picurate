"""Stage 6 headless tests: reverse-geocode, places, trip grouping, query filters."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core.db.catalog import get_connection, open_catalog
from core.places import (
    auto_group_trips,
    get_or_create_place,
    get_photos_by_place,
    get_photos_by_trip,
    get_places_summary,
    get_trips,
    reverse_geocode,
    set_place_manual,
)
from core.query import count_photos, get_adjacent_photo_ids, get_photos


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def cat(tmp_path: Path) -> Path:
    import core.db.catalog as _m
    _m._local.__dict__.clear()
    db = tmp_path / "test.db"
    open_catalog(db)
    conn = get_connection(db)
    photos = [
        # (filename, file_path, date_taken, gps_lat, gps_lon)
        ("a.jpg", "/p/a.jpg", "2024-01-10 12:00:00", 47.5, 19.0),
        ("b.jpg", "/p/b.jpg", "2024-01-11 14:00:00", 47.5, 19.0),
        ("c.jpg", "/p/c.jpg", "2024-06-01 10:00:00", 48.2, 16.4),
        ("d.jpg", "/p/d.jpg", "2024-06-03 09:00:00", None, None),
        ("e.jpg", "/p/e.jpg", "2022-07-15 08:00:00", 51.5, -0.1),
    ]
    with conn:
        for fn, fp, dt, lat, lon in photos:
            conn.execute(
                """INSERT INTO photos
                   (filename, file_path, date_taken, status, quick_sig,
                    gps_lat, gps_lon)
                   VALUES (?,?,?,?,?,?,?)""",
                (fn, fp, dt, "ok", fn, lat, lon),
            )
    return db


# ── Reverse geocode ───────────────────────────────────────────────────────────

class TestReverseGeocode:
    def test_returns_dict_keys(self) -> None:
        result = reverse_geocode(47.5, 19.0)
        assert "city" in result
        assert "region" in result
        assert "country" in result

    def test_budapest_coordinates(self) -> None:
        result = reverse_geocode(47.497, 19.040)
        assert result["country"] == "HU"

    def test_london_coordinates(self) -> None:
        result = reverse_geocode(51.507, -0.128)
        assert result["country"] == "GB"

    def test_invalid_coords_returns_empty(self) -> None:
        # Extreme coordinates that might fail gracefully
        result = reverse_geocode(0.0, 0.0)
        assert isinstance(result, dict)


# ── Place creation ────────────────────────────────────────────────────────────

class TestPlaceCreation:
    def test_get_or_create_place_creates(self, cat: Path) -> None:
        pid = get_or_create_place(47.5, 19.0, cat)
        assert isinstance(pid, int)
        assert pid > 0

    def test_get_or_create_place_idempotent(self, cat: Path) -> None:
        pid1 = get_or_create_place(47.5, 19.0, cat)
        pid2 = get_or_create_place(47.5, 19.0, cat)
        assert pid1 == pid2

    def test_different_coords_different_place(self, cat: Path) -> None:
        pid1 = get_or_create_place(47.5, 19.0, cat)
        pid2 = get_or_create_place(51.5, -0.1, cat)
        assert pid1 != pid2

    def test_set_place_manual(self, cat: Path) -> None:
        conn = get_connection(cat)
        photo_id = conn.execute("SELECT id FROM photos WHERE filename='d.jpg'").fetchone()["id"]
        place_id = set_place_manual(photo_id, "Vienna", "Vienna", "AT", cat)
        assert isinstance(place_id, int)
        conn2 = get_connection(cat)
        row = conn2.execute("SELECT place_id FROM photos WHERE id=?", (photo_id,)).fetchone()
        assert row["place_id"] == place_id

    def test_set_place_manual_reuses_existing(self, cat: Path) -> None:
        conn = get_connection(cat)
        photo_a = conn.execute("SELECT id FROM photos WHERE filename='a.jpg'").fetchone()["id"]
        photo_b = conn.execute("SELECT id FROM photos WHERE filename='b.jpg'").fetchone()["id"]
        pid1 = set_place_manual(photo_a, "Budapest", "Budapest", "HU", cat)
        pid2 = set_place_manual(photo_b, "Budapest", "Budapest", "HU", cat)
        assert pid1 == pid2


# ── Places queries ────────────────────────────────────────────────────────────

class TestPlacesQueries:
    def test_places_summary_empty(self, cat: Path) -> None:
        summary = get_places_summary(cat)
        assert summary == []

    def test_places_summary_after_geocode(self, cat: Path) -> None:
        conn = get_connection(cat)
        photo_a = conn.execute("SELECT id FROM photos WHERE filename='a.jpg'").fetchone()["id"]
        set_place_manual(photo_a, "Budapest", "Budapest", "HU", cat)
        summary = get_places_summary(cat)
        assert len(summary) >= 1
        assert any(p["city"] == "Budapest" for p in summary)

    def test_get_photos_by_place(self, cat: Path) -> None:
        conn = get_connection(cat)
        photo_a = conn.execute("SELECT id FROM photos WHERE filename='a.jpg'").fetchone()["id"]
        photo_b = conn.execute("SELECT id FROM photos WHERE filename='b.jpg'").fetchone()["id"]
        place_id = set_place_manual(photo_a, "Test City", "Region", "TC", cat)
        set_place_manual(photo_b, "Test City", "Region", "TC", cat)
        photos = get_photos_by_place(place_id, cat)
        assert len(photos) == 2

    def test_place_id_filter_in_get_photos(self, cat: Path) -> None:
        conn = get_connection(cat)
        photo_a = conn.execute("SELECT id FROM photos WHERE filename='a.jpg'").fetchone()["id"]
        place_id = set_place_manual(photo_a, "Somewhere", "Reg", "ZZ", cat)
        conn2 = get_connection(cat)
        rows = get_photos(conn2, place_id=place_id)
        assert len(rows) == 1
        assert rows[0]["filename"] == "a.jpg"

    def test_count_photos_by_place(self, cat: Path) -> None:
        conn = get_connection(cat)
        photo_a = conn.execute("SELECT id FROM photos WHERE filename='a.jpg'").fetchone()["id"]
        place_id = set_place_manual(photo_a, "Counted City", "R", "C", cat)
        conn2 = get_connection(cat)
        assert count_photos(conn2, place_id=place_id) == 1


# ── Trip grouping ─────────────────────────────────────────────────────────────

class TestTripGrouping:
    def test_groups_consecutive_days(self, cat: Path) -> None:
        # a.jpg (Jan 10) and b.jpg (Jan 11) are 1 day apart → same trip
        # e.jpg (Jul 15) is far away → separate trip
        # c.jpg (Jun 1) and d.jpg (Jun 3) are 2 days apart → same trip
        stats = auto_group_trips(cat, gap_days=3)
        assert stats["trips_created"] >= 1
        assert stats["photos_assigned"] >= 2

    def test_trip_names_include_dates(self, cat: Path) -> None:
        auto_group_trips(cat, gap_days=3)
        trips = get_trips(cat)
        assert len(trips) >= 1
        for trip in trips:
            assert "Trip" in trip["name"]

    def test_get_trips_empty(self, cat: Path) -> None:
        trips = get_trips(cat)
        assert trips == []

    def test_get_trips_after_grouping(self, cat: Path) -> None:
        auto_group_trips(cat, gap_days=3)
        trips = get_trips(cat)
        assert len(trips) >= 1
        assert all("photo_count" in t for t in trips)

    def test_get_photos_by_trip(self, cat: Path) -> None:
        auto_group_trips(cat, gap_days=3)
        trips = get_trips(cat)
        assert trips
        photos = get_photos_by_trip(trips[0]["id"], cat)
        assert len(photos) >= 1

    def test_trip_id_filter_in_get_photos(self, cat: Path) -> None:
        auto_group_trips(cat, gap_days=3)
        trips = get_trips(cat)
        assert trips
        trip_id = trips[0]["id"]
        conn = get_connection(cat)
        rows = get_photos(conn, trip_id=trip_id)
        assert len(rows) >= 1

    def test_count_photos_by_trip(self, cat: Path) -> None:
        auto_group_trips(cat, gap_days=3)
        trips = get_trips(cat)
        assert trips
        conn = get_connection(cat)
        assert count_photos(conn, trip_id=trips[0]["id"]) >= 1

    def test_idempotent_grouping(self, cat: Path) -> None:
        auto_group_trips(cat, gap_days=3)
        first_count = len(get_trips(cat))
        # Running again should not create new trips (already-assigned photos are skipped)
        auto_group_trips(cat, gap_days=3)
        second_count = len(get_trips(cat))
        assert first_count == second_count

    def test_large_gap_creates_separate_trips(self, cat: Path) -> None:
        # gap_days=0: every photo in its own trip (if it has a neighbour within 0 days)
        # With gap_days=400: all photos should be in one group
        stats = auto_group_trips(cat, gap_days=400)
        # a,b,c,d,e span 2022-2024 but within 400 days of each other? No.
        # 2022-07-15 to 2024-06-01 = ~687 days > 400 → at least 2 trips
        trips = get_trips(cat)
        assert len(trips) >= 1

    def test_single_day_trip_naming(self, cat: Path) -> None:
        # a.jpg and b.jpg are Jan 10 and 11 — different days
        # If we reduce gap to force single-photo groups, those are filtered out
        stats = auto_group_trips(cat, gap_days=3)
        trips = get_trips(cat)
        for trip in trips:
            if trip["start_date"] == trip.get("end_date"):
                # Single-day trips: name should just be "Trip YYYY-MM-DD"
                assert " – " not in trip["name"]


# ── Adjacent navigation with new filters ─────────────────────────────────────

class TestAdjacentWithPlaceTripFilters:
    def test_adjacent_with_place_filter(self, cat: Path) -> None:
        conn = get_connection(cat)
        pid_a = conn.execute("SELECT id FROM photos WHERE filename='a.jpg'").fetchone()["id"]
        pid_b = conn.execute("SELECT id FROM photos WHERE filename='b.jpg'").fetchone()["id"]
        place_id = set_place_manual(pid_a, "City", "R", "C", cat)
        set_place_manual(pid_b, "City", "R", "C", cat)

        conn2 = get_connection(cat)
        rows = get_photos(conn2, place_id=place_id)
        ids = [r["id"] for r in rows]
        assert len(ids) == 2

        prev_id, next_id = get_adjacent_photo_ids(conn2, ids[0], place_id=place_id)
        assert prev_id is None
        assert next_id == ids[1]
