"""Reusable catalog queries used by the UI and tests (no UI imports)."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def get_photos(
    conn: sqlite3.Connection,
    folder: str | None = None,
    year: int | None = None,
    month: int | None = None,
    limit: int = 2000,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Return photo rows matching the optional filters, newest first."""
    clauses: list[str] = ["status NOT IN ('missing', 'duplicate')"]
    params: list = []

    if folder:
        clauses.append("file_path LIKE ?")
        params.append(str(folder).rstrip("/") + "/%")

    if year is not None:
        clauses.append("CAST(strftime('%Y', date_taken) AS INTEGER) = ?")
        params.append(year)

    if month is not None:
        clauses.append("CAST(strftime('%m', date_taken) AS INTEGER) = ?")
        params.append(month)

    where = " AND ".join(clauses)
    params += [limit, offset]
    return conn.execute(
        f"""SELECT id, file_path, filename, date_taken, camera_make, camera_model,
                   width, height, gps_lat, gps_lon, file_size, rating, flag, thumbnail_path
            FROM photos WHERE {where}
            ORDER BY date_taken DESC, filename
            LIMIT ? OFFSET ?""",
        params,
    ).fetchall()


def count_photos(
    conn: sqlite3.Connection,
    folder: str | None = None,
    year: int | None = None,
    month: int | None = None,
) -> int:
    clauses: list[str] = ["status NOT IN ('missing', 'duplicate')"]
    params: list = []
    if folder:
        clauses.append("file_path LIKE ?")
        params.append(str(folder).rstrip("/") + "/%")
    if year is not None:
        clauses.append("CAST(strftime('%Y', date_taken) AS INTEGER) = ?")
        params.append(year)
    if month is not None:
        clauses.append("CAST(strftime('%m', date_taken) AS INTEGER) = ?")
        params.append(month)
    return conn.execute(
        f"SELECT COUNT(*) FROM photos WHERE {' AND '.join(clauses)}", params
    ).fetchone()[0]


def get_photo_by_id(conn: sqlite3.Connection, photo_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT id, file_path, filename, date_taken, camera_make, camera_model,
                  width, height, gps_lat, gps_lon, file_size, rating, flag,
                  thumbnail_path, status
           FROM photos WHERE id=?""",
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
) -> tuple[int | None, int | None]:
    """Return (prev_id, next_id) for navigation within the current filter."""
    rows = get_photos(conn, folder=folder, year=year, month=month, limit=10000)
    ids = [r["id"] for r in rows]
    if photo_id not in ids:
        return None, None
    idx = ids.index(photo_id)
    prev_id = ids[idx - 1] if idx > 0 else None
    next_id = ids[idx + 1] if idx < len(ids) - 1 else None
    return prev_id, next_id
