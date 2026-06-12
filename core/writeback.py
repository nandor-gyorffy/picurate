"""Metadata write-back — mirror catalog ratings/captions/keywords to XMP/IPTC
via the exiftool binary.  NON-DESTRUCTIVE: only standard metadata fields are
written; pixel data and proprietary camera data are never touched.

Gracefully no-ops when exiftool is not found on PATH (or bundled path).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from core.db.catalog import get_connection
from core.logger import get_logger

log = get_logger("picurate.writeback")


def _exiftool_path() -> str | None:
    """Return path to exiftool, or None if not found."""
    found = shutil.which("exiftool")
    if found:
        return found
    # Check bundled location alongside main.py
    bundled = Path(__file__).parent.parent / "bin" / "exiftool"
    if bundled.exists():
        return str(bundled)
    return None


def exiftool_available() -> bool:
    return _exiftool_path() is not None


def write_back_photo(photo_id: int, catalog_path: Path | None = None) -> bool:
    """Write rating, caption, and keywords for one photo back to its file.

    Returns True on success, False if exiftool is unavailable or write failed.
    The original file is modified in-place (XMP sidecar-compatible metadata only).
    Only safe, lossless metadata fields are written.
    """
    et = _exiftool_path()
    if et is None:
        log.debug("exiftool not found — skipping write-back")
        return False

    conn = get_connection(catalog_path)
    row = conn.execute(
        "SELECT file_path, rating, caption, keywords FROM photos WHERE id=?", (photo_id,)
    ).fetchone()
    if row is None:
        return False

    args = [et, "-overwrite_original_in_place", "-P"]

    if row["rating"] is not None and row["rating"] > 0:
        args += [f"-XMP:Rating={row['rating']}", f"-IPTC:Urgency={6 - row['rating']}"]

    if row["caption"]:
        safe = row["caption"].replace('"', '\\"')
        args += [f'-XMP:Description={safe}', f'-IPTC:Caption-Abstract={safe}']

    if row["keywords"]:
        for kw in row["keywords"].split(","):
            kw = kw.strip()
            if kw:
                args.append(f'-XMP:Subject={kw}')
                args.append(f'-IPTC:Keywords={kw}')

    args.append(row["file_path"])

    try:
        result = subprocess.run(args, capture_output=True, timeout=15)
        if result.returncode != 0:
            log.warning("exiftool failed for %s: %s", row["file_path"], result.stderr.decode())
            return False
        return True
    except Exception as exc:
        log.warning("write_back_photo failed: %s", exc)
        return False


def write_back_batch(
    catalog_path: Path | None = None,
    progress_cb=None,
) -> dict:
    """Write back metadata for all rated/captioned/keyworded photos.

    Returns {written, skipped, errors}.
    """
    if not exiftool_available():
        log.info("exiftool not available — write-back skipped")
        return {"written": 0, "skipped": 0, "errors": 0, "unavailable": True}

    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT id FROM photos
           WHERE status NOT IN ('missing','duplicate','deleted')
             AND (rating > 0 OR caption IS NOT NULL OR keywords IS NOT NULL)"""
    ).fetchall()

    written = skipped = errors = 0
    for i, row in enumerate(rows):
        ok = write_back_photo(row["id"], catalog_path)
        if ok:
            written += 1
        else:
            errors += 1
        if progress_cb:
            progress_cb(i + 1, len(rows))

    return {"written": written, "skipped": skipped, "errors": errors, "unavailable": False}
