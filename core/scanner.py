"""Folder scanner: discover image files, insert/update catalog rows, enqueue jobs."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

from core import exif, hashing, thumbnails
from core.db.catalog import CatalogWriter
from core.logger import get_logger

log = get_logger("picurate.scanner")

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif",
    ".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raw",
}


def _enqueue(conn: sqlite3.Connection, job_type: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO jobs(job_type, payload, status, created_at) VALUES (?,?,?,?)",
        (job_type, json.dumps(payload), "pending", datetime.now().isoformat()),
    )


def scan_folder(
    folder: Path,
    catalog_path: Path | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
    """
    Scan *folder* recursively. For each image file:
    - If quick_sig matches → skip (unchanged).
    - If path is new but partial_hash matches a missing row → relink.
    - Otherwise insert/update the row and enqueue a full-hash job.
    Returns stats dict.
    """
    files = [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]
    stats = {"scanned": 0, "inserted": 0, "updated": 0, "relinked": 0, "errors": 0}
    total = len(files)

    for i, fpath in enumerate(files):
        if progress_cb:
            progress_cb(i, total)
        try:
            _process_file(fpath, catalog_path, stats)
        except Exception as exc:
            log.warning("Error processing %s: %s", fpath, exc)
            stats["errors"] += 1
            try:
                with CatalogWriter(catalog_path) as _ec:
                    _ec.execute(
                        "INSERT INTO scan_errors(file_path, error_msg) VALUES (?,?)",
                        (str(fpath), str(exc)),
                    )
            except Exception:
                pass
        stats["scanned"] += 1

    if progress_cb:
        progress_cb(total, total)
    log.info("Scan complete: %s", stats)
    return stats


def _process_file(fpath: Path, catalog_path: Path | None, stats: dict) -> None:
    sig = hashing.quick_signature(fpath)
    phash = hashing.partial_hash(fpath)
    vol_id = hashing.volume_id(fpath)

    with CatalogWriter(catalog_path) as conn:
        existing = conn.execute(
            "SELECT id, quick_sig, status FROM photos WHERE file_path=?",
            (str(fpath),),
        ).fetchone()

        if existing:
            if existing["quick_sig"] == sig:
                return  # unchanged
            # File changed — update sig and re-enqueue for full hash
            _update_file(conn, existing["id"], fpath, sig, phash, vol_id)
            stats["updated"] += 1
        else:
            # Check for a missing row with the same partial hash
            missing = conn.execute(
                "SELECT id FROM photos WHERE partial_hash=? AND status='missing' LIMIT 1",
                (phash,),
            ).fetchone()
            if missing:
                # Relink: moved/renamed file
                conn.execute(
                    "UPDATE photos SET file_path=?, filename=?, quick_sig=?, status='ok', volume_id=? WHERE id=?",
                    (str(fpath), fpath.name, sig, _ensure_volume(conn, vol_id), missing["id"]),
                )
                log.info("Relinked %s (id=%s)", fpath.name, missing["id"])
                stats["relinked"] += 1
                # Enqueue full-hash verification
                _enqueue(conn, "full_hash", {"photo_id": missing["id"], "path": str(fpath)})
            else:
                photo_id = _insert_file(conn, fpath, sig, phash, vol_id)
                stats["inserted"] += 1
                _enqueue(conn, "full_hash", {"photo_id": photo_id, "path": str(fpath)})
                _enqueue(conn, "thumbnail", {"photo_id": photo_id, "path": str(fpath)})


def _ensure_volume(conn: sqlite3.Connection, vol_id: str) -> int:
    row = conn.execute("SELECT id FROM volumes WHERE label=?", (vol_id,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO volumes(label) VALUES (?)", (vol_id,))
    return cur.lastrowid


def _insert_file(conn: sqlite3.Connection, fpath: Path, sig: str, phash: str, vol_id: str) -> int:
    vid = _ensure_volume(conn, vol_id)
    exif_data = exif.extract(fpath)
    st = fpath.stat()
    cur = conn.execute(
        """INSERT INTO photos
           (file_path, volume_id, filename, file_size, mtime, quick_sig, partial_hash,
            date_taken, camera_make, camera_model, width, height, gps_lat, gps_lon, status)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ok')""",
        (
            str(fpath), vid, fpath.name, st.st_size, st.st_mtime, sig, phash,
            exif_data.get("date_taken"), exif_data.get("camera_make"),
            exif_data.get("camera_model"), exif_data.get("width"),
            exif_data.get("height"), exif_data.get("gps_lat"), exif_data.get("gps_lon"),
        ),
    )
    return cur.lastrowid


def _update_file(conn: sqlite3.Connection, photo_id: int, fpath: Path, sig: str, phash: str, vol_id: str) -> None:
    vid = _ensure_volume(conn, vol_id)
    exif_data = exif.extract(fpath)
    st = fpath.stat()
    conn.execute(
        """UPDATE photos SET
           file_size=?, mtime=?, quick_sig=?, partial_hash=?,
           date_taken=?, camera_make=?, camera_model=?, width=?, height=?,
           gps_lat=?, gps_lon=?, volume_id=?, status='ok'
           WHERE id=?""",
        (
            st.st_size, st.st_mtime, sig, phash,
            exif_data.get("date_taken"), exif_data.get("camera_make"),
            exif_data.get("camera_model"), exif_data.get("width"), exif_data.get("height"),
            exif_data.get("gps_lat"), exif_data.get("gps_lon"), vid, photo_id,
        ),
    )


def mark_missing(folder: Path, catalog_path: Path | None = None) -> int:
    """Mark catalogued files under *folder* that no longer exist as 'missing'."""
    count = 0
    with CatalogWriter(catalog_path) as conn:
        rows = conn.execute(
            "SELECT id, file_path FROM photos WHERE status='ok' AND file_path LIKE ?",
            (str(folder).rstrip("/") + "/%",),
        ).fetchall()
        for row in rows:
            if not Path(row["file_path"]).exists():
                conn.execute("UPDATE photos SET status='missing' WHERE id=?", (row["id"],))
                count += 1
    return count
