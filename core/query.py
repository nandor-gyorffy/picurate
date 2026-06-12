"""Reusable catalog queries used by the UI and tests (no UI imports)."""
from __future__ import annotations

import sqlite3
from pathlib import Path


_PHOTO_COLS = """id, file_path, filename, date_taken, camera_make, camera_model,
                 width, height, gps_lat, gps_lon, file_size, rating, flag,
                 thumbnail_path, caption, keywords, place_id, trip_id"""

_PHOTO_COLS_WITH_STATUS = _PHOTO_COLS + ", status"


def _build_where(
    folder: str | None,
    year: int | None,
    month: int | None,
    rating_min: int | None,
    flag: int | None,
    search: str | None,
    collection_id: int | None,
    place_id: int | None,
    trip_id: int | None,
) -> tuple[str, list]:
    clauses: list[str] = ["p.status NOT IN ('missing', 'duplicate')"]
    params: list = []

    if folder:
        clauses.append("p.file_path LIKE ?")
        params.append(str(folder).rstrip("/") + "/%")
    if year is not None:
        clauses.append("CAST(strftime('%Y', p.date_taken) AS INTEGER) = ?")
        params.append(year)
    if month is not None:
        clauses.append("CAST(strftime('%m', p.date_taken) AS INTEGER) = ?")
        params.append(month)
    if rating_min is not None and rating_min > 0:
        clauses.append("p.rating >= ?")
        params.append(rating_min)
    if flag is not None:
        clauses.append("p.flag = ?")
        params.append(flag)
    if search:
        clauses.append("p.filename LIKE ?")
        params.append(f"%{search}%")
    if place_id is not None:
        clauses.append("p.place_id = ?")
        params.append(place_id)
    if trip_id is not None:
        clauses.append("p.trip_id = ?")
        params.append(trip_id)

    return " AND ".join(clauses), params


def get_photos(
    conn: sqlite3.Connection,
    folder: str | None = None,
    year: int | None = None,
    month: int | None = None,
    rating_min: int | None = None,
    flag: int | None = None,
    search: str | None = None,
    collection_id: int | None = None,
    place_id: int | None = None,
    trip_id: int | None = None,
    limit: int = 2000,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Return photo rows matching all active filters, newest first."""
    where, params = _build_where(
        folder, year, month, rating_min, flag, search, collection_id, place_id, trip_id
    )

    cols = ", ".join(f"p.{c.strip()}" for c in _PHOTO_COLS.split(","))

    if collection_id is not None:
        sql = f"""SELECT {cols}
                  FROM photos p
                  JOIN collection_photos cp ON cp.photo_id = p.id AND cp.collection_id = ?
                  WHERE {where}
                  ORDER BY p.date_taken DESC, p.filename
                  LIMIT ? OFFSET ?"""
        return conn.execute(sql, [collection_id] + params + [limit, offset]).fetchall()

    return conn.execute(
        f"""SELECT {cols}
            FROM photos p
            WHERE {where}
            ORDER BY p.date_taken DESC, p.filename
            LIMIT ? OFFSET ?""",
        params + [limit, offset],
    ).fetchall()


def count_photos(
    conn: sqlite3.Connection,
    folder: str | None = None,
    year: int | None = None,
    month: int | None = None,
    rating_min: int | None = None,
    flag: int | None = None,
    search: str | None = None,
    collection_id: int | None = None,
    place_id: int | None = None,
    trip_id: int | None = None,
) -> int:
    where, params = _build_where(
        folder, year, month, rating_min, flag, search, collection_id, place_id, trip_id
    )
    if collection_id is not None:
        return conn.execute(
            f"""SELECT COUNT(*) FROM photos p
                JOIN collection_photos cp ON cp.photo_id = p.id AND cp.collection_id = ?
                WHERE {where}""",
            [collection_id] + params,
        ).fetchone()[0]
    return conn.execute(
        f"SELECT COUNT(*) FROM photos p WHERE {where}", params
    ).fetchone()[0]


def get_photo_by_id(conn: sqlite3.Connection, photo_id: int) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_PHOTO_COLS_WITH_STATUS} FROM photos WHERE id=?",
        (photo_id,),
    ).fetchone()


def get_timeline(conn: sqlite3.Connection) -> list[tuple[int, int, int]]:
    """Return [(year, month, count)] ordered newest first."""
    rows = conn.execute(
        """SELECT CAST(strftime('%Y', date_taken) AS INTEGER) AS y,
                  CAST(strftime('%m', date_taken) AS INTEGER) AS m,
                  COUNT(*) AS cnt
           FROM photos
           WHERE status NOT IN ('missing', 'duplicate') AND date_taken IS NOT NULL
           GROUP BY y, m
           ORDER BY y DESC, m DESC"""
    ).fetchall()
    result = []
    for row in rows:
        try:
            if row["y"] and row["m"]:
                result.append((int(row["y"]), int(row["m"]), int(row["cnt"])))
        except (TypeError, ValueError):
            pass
    return result


def get_unique_folders(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {parent_folder: photo_count} for all indexed photos."""
    rows = conn.execute(
        "SELECT file_path FROM photos WHERE status NOT IN ('missing', 'duplicate')"
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        parent = str(Path(row["file_path"]).parent)
        counts[parent] = counts.get(parent, 0) + 1
    return counts


def get_adjacent_photo_ids(
    conn: sqlite3.Connection,
    photo_id: int,
    folder: str | None = None,
    year: int | None = None,
    month: int | None = None,
    rating_min: int | None = None,
    flag: int | None = None,
    search: str | None = None,
    collection_id: int | None = None,
    place_id: int | None = None,
    trip_id: int | None = None,
) -> tuple[int | None, int | None]:
    """Return (prev_id, next_id) within the current filter context."""
    rows = get_photos(
        conn,
        folder=folder, year=year, month=month,
        rating_min=rating_min, flag=flag, search=search,
        collection_id=collection_id, place_id=place_id, trip_id=trip_id,
        limit=10000,
    )
    ids = [r["id"] for r in rows]
    if photo_id not in ids:
        return None, None
    idx = ids.index(photo_id)
    return (ids[idx - 1] if idx > 0 else None), (ids[idx + 1] if idx < len(ids) - 1 else None)
