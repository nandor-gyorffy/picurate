"""Offline reverse-geocoding and place/trip management."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger

log = get_logger("picurate.places")


# ── Reverse geocode ───────────────────────────────────────────────────────────

def reverse_geocode(lat: float, lon: float) -> dict:
    """Return {'city', 'region', 'country'} for (lat, lon). Offline."""
    try:
        import reverse_geocoder as rg
        results = rg.search([(lat, lon)], verbose=False)
        if results:
            r = results[0]
            return {
                "city":    r.get("name", ""),
                "region":  r.get("admin1", ""),
                "country": r.get("cc", ""),
            }
    except Exception as exc:
        log.debug("reverse_geocode failed: %s", exc)
    return {"city": "", "region": "", "country": ""}


def get_or_create_place(
    lat: float,
    lon: float,
    catalog_path: Path,
) -> int:
    """
    Find or create a place record for (lat, lon).
    Rounds to 2 decimal places (~1 km grid) before matching.
    Returns place_id.
    """
    lat_r = round(lat, 2)
    lon_r = round(lon, 2)

    conn = get_connection(catalog_path)
    row = conn.execute(
        "SELECT id FROM places WHERE round(lat,2)=? AND round(lon,2)=?",
        (lat_r, lon_r),
    ).fetchone()
    if row:
        return row["id"]

    geo = reverse_geocode(lat, lon)
    with CatalogWriter(catalog_path) as wconn:
        wconn.execute(
            "INSERT INTO places (city, region, country, lat, lon) VALUES (?,?,?,?,?)",
            (geo["city"], geo["region"], geo["country"], lat, lon),
        )
        place_id = wconn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return place_id


def geocode_photos(
    catalog_path: Path,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
    """
    Reverse-geocode all photos that have GPS but no place_id.
    Returns stats: {geocoded, skipped, errors}.
    """
    conn = get_connection(catalog_path)
    rows = conn.execute(
        "SELECT id, gps_lat, gps_lon FROM photos "
        "WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL AND place_id IS NULL"
    ).fetchall()

    total = len(rows)
    stats = {"geocoded": 0, "skipped": 0, "errors": 0}

    for i, row in enumerate(rows):
        if progress_cb:
            progress_cb(i, total)
        try:
            pid = get_or_create_place(row["gps_lat"], row["gps_lon"], catalog_path)
            with CatalogWriter(catalog_path) as wconn:
                wconn.execute("UPDATE photos SET place_id=? WHERE id=?", (pid, row["id"]))
            stats["geocoded"] += 1
        except Exception as exc:
            log.error("geocode_photos error for photo %d: %s", row["id"], exc)
            stats["errors"] += 1

    if progress_cb:
        progress_cb(total, total)
    return stats


# ── Place queries ─────────────────────────────────────────────────────────────

def get_places_summary(catalog_path: Path) -> list[dict]:
    """
    Return [{id, city, region, country, lat, lon, photo_count}]
    ordered by photo_count DESC.
    """
    conn = get_connection(catalog_path)
    rows = conn.execute("""
        SELECT pl.id, pl.city, pl.region, pl.country, pl.lat, pl.lon,
               COUNT(ph.id) AS photo_count
        FROM places pl
        LEFT JOIN photos ph ON ph.place_id = pl.id AND ph.status='ok'
        GROUP BY pl.id
        ORDER BY photo_count DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_photos_by_place(place_id: int, catalog_path: Path) -> list[dict]:
    """Return all photos at a given place."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT id, filename, file_path, thumbnail_path, date_taken, rating, flag
           FROM photos WHERE place_id=? AND status='ok'
           ORDER BY date_taken""",
        (place_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_place_manual(
    photo_id: int,
    city: str,
    region: str,
    country: str,
    catalog_path: Path,
) -> int:
    """Manually assign a place to a photo. Creates the place if needed."""
    conn = get_connection(catalog_path)
    row = conn.execute(
        "SELECT id FROM places WHERE city=? AND region=? AND country=?",
        (city, region, country),
    ).fetchone()
    if row:
        place_id = row["id"]
    else:
        with CatalogWriter(catalog_path) as wconn:
            wconn.execute(
                "INSERT INTO places (city, region, country) VALUES (?,?,?)",
                (city, region, country),
            )
            place_id = wconn.execute("SELECT last_insert_rowid()").fetchone()[0]

    with CatalogWriter(catalog_path) as wconn:
        wconn.execute("UPDATE photos SET place_id=? WHERE id=?", (place_id, photo_id))
    return place_id


# ── Trip grouping ─────────────────────────────────────────────────────────────

def auto_group_trips(
    catalog_path: Path,
    gap_days: int = 3,
) -> dict:
    """
    Group photos into trips based on date gaps.

    Photos are sorted by date_taken.  A new trip starts whenever the gap
    to the previous photo is > gap_days.  Each trip is named after its
    date range (e.g. "Trip 2024-05-10 – 2024-05-17").

    Only processes photos that have no trip_id yet.
    Returns stats: {trips_created, photos_assigned}.
    """
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT id, date_taken FROM photos
           WHERE date_taken IS NOT NULL AND trip_id IS NULL AND status='ok'
           ORDER BY date_taken"""
    ).fetchall()

    if not rows:
        return {"trips_created": 0, "photos_assigned": 0}

    from datetime import datetime, timedelta

    groups: list[list[dict]] = []
    current: list[dict] = []

    for row in rows:
        r = dict(row)
        if not current:
            current.append(r)
            continue
        try:
            prev_dt = datetime.fromisoformat(current[-1]["date_taken"][:10])
            cur_dt  = datetime.fromisoformat(r["date_taken"][:10])
            if (cur_dt - prev_dt).days > gap_days:
                groups.append(current)
                current = [r]
            else:
                current.append(r)
        except ValueError:
            current.append(r)

    if current:
        groups.append(current)

    # Filter groups: only keep multi-day or multi-photo groups (skip single isolated shots)
    meaningful = [g for g in groups if len(g) >= 2]

    trips_created = 0
    photos_assigned = 0

    for group in meaningful:
        start = group[0]["date_taken"][:10]
        end   = group[-1]["date_taken"][:10]
        name  = f"Trip {start}" if start == end else f"Trip {start} – {end}"

        with CatalogWriter(catalog_path) as wconn:
            wconn.execute(
                "INSERT INTO trips (name, start_date, end_date) VALUES (?,?,?)",
                (name, start, end),
            )
            trip_id = wconn.execute("SELECT last_insert_rowid()").fetchone()[0]
            for r in group:
                wconn.execute("UPDATE photos SET trip_id=? WHERE id=?", (trip_id, r["id"]))

        trips_created += 1
        photos_assigned += len(group)

    return {"trips_created": trips_created, "photos_assigned": photos_assigned}


def get_trips(catalog_path: Path) -> list[dict]:
    """Return [{id, name, start_date, end_date, photo_count}] ordered by start_date."""
    conn = get_connection(catalog_path)
    rows = conn.execute("""
        SELECT t.id, t.name, t.start_date, t.end_date,
               COUNT(p.id) AS photo_count
        FROM trips t
        LEFT JOIN photos p ON p.trip_id = t.id AND p.status='ok'
        GROUP BY t.id
        ORDER BY t.start_date
    """).fetchall()
    return [dict(r) for r in rows]


def get_photos_by_trip(trip_id: int, catalog_path: Path) -> list[dict]:
    """Return all photos in a trip ordered by date."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT id, filename, file_path, thumbnail_path, date_taken, rating, flag
           FROM photos WHERE trip_id=? AND status='ok'
           ORDER BY date_taken""",
        (trip_id,),
    ).fetchall()
    return [dict(r) for r in rows]
