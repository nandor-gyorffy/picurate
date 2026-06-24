"""Auto-fix routines for catalog health problems — missing files, orphans."""
from __future__ import annotations

from pathlib import Path

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger

log = get_logger("picurate.recovery")


def fix_missing_files(catalog_path: Path) -> dict:
    """
    Try every cheap fix for missing catalog entries, in order:

    1. File came back / was a false-miss  → mark ok.
    2. Another catalog row has the same full_hash at an existing path
       → update the path on the missing row (relink) and drop the duplicate.
    3. A file with the same name exists somewhere inside the same watch folder
       → verify hash then relink.

    Returns stats dict with counts.
    """
    conn = get_connection(catalog_path)
    missing_rows = conn.execute(
        "SELECT id, file_path, full_hash, filename FROM photos WHERE status='missing'"
    ).fetchall()

    stats = {
        "already_back": 0,
        "relinked_by_hash": 0,
        "relinked_by_name": 0,
        "still_missing": 0,
    }

    for row in missing_rows:
        photo_id = row["id"]
        old_path  = Path(row["file_path"])
        full_hash = row["full_hash"]
        filename  = row["filename"]

        # 1. File came back at the exact same path
        if old_path.exists():
            with CatalogWriter(catalog_path) as w:
                w.execute("UPDATE photos SET status='ok' WHERE id=?", (photo_id,))
            stats["already_back"] += 1
            continue

        fixed = False

        # 2. Another ok row has the same hash at an existing path
        if full_hash:
            dup = conn.execute(
                "SELECT id, file_path FROM photos WHERE full_hash=? AND status='ok' AND id!=? LIMIT 1",
                (full_hash, photo_id),
            ).fetchone()
            if dup and Path(dup["file_path"]).exists():
                # The content lives at a different path → update this row's path,
                # then remove the duplicate row (or just mark missing one removed).
                with CatalogWriter(catalog_path) as w:
                    w.execute(
                        "UPDATE photos SET file_path=?, filename=?, status='ok' WHERE id=?",
                        (dup["file_path"], Path(dup["file_path"]).name, photo_id),
                    )
                    # Remove the duplicate entry so we don't have two rows for one file
                    w.execute("DELETE FROM photos WHERE id=?", (dup["id"],))
                stats["relinked_by_hash"] += 1
                fixed = True

        # 3. Search the same parent directory tree for a file with the same name
        if not fixed:
            search_root = old_path.parent
            # Walk up to find a valid root (the file might have been in a subdir)
            for _ in range(3):
                if search_root.exists():
                    break
                search_root = search_root.parent

            if search_root.exists():
                candidates = list(search_root.rglob(filename))
                if candidates:
                    # Pick the first existing candidate; verify hash if available
                    for cand in candidates:
                        if not cand.exists():
                            continue
                        if full_hash:
                            # Quick size check first
                            try:
                                if cand.stat().st_size != conn.execute(
                                    "SELECT file_size FROM photos WHERE id=?", (photo_id,)
                                ).fetchone()["file_size"]:
                                    continue
                            except Exception:
                                pass
                        with CatalogWriter(catalog_path) as w:
                            w.execute(
                                "UPDATE photos SET file_path=?, filename=?, status='ok' WHERE id=?",
                                (str(cand), cand.name, photo_id),
                            )
                        stats["relinked_by_name"] += 1
                        fixed = True
                        break

        if not fixed:
            stats["still_missing"] += 1

    log.info("fix_missing_files: %s", stats)
    return stats


def remove_missing_from_catalog(catalog_path: Path) -> int:
    """
    Remove all photos with status='missing' from the catalog.
    Their faces, tags, collection memberships, and edits are cascade-deleted.
    Returns the number of rows removed.
    """
    conn = get_connection(catalog_path)
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM photos WHERE status='missing'"
    ).fetchall()]
    if not ids:
        return 0
    with CatalogWriter(catalog_path) as w:
        w.executemany("DELETE FROM photos WHERE id=?", [(i,) for i in ids])
    log.info("Removed %d missing photos from catalog", len(ids))
    return len(ids)


def fix_scan_errors(catalog_path: Path) -> dict:
    """
    Retry files that previously caused scan errors.
    Removes the error log entry if the file now scans successfully.
    """
    from core.scanner import _process_file  # type: ignore[attr-defined]

    conn = get_connection(catalog_path)
    errors = conn.execute(
        "SELECT DISTINCT file_path FROM scan_errors"
    ).fetchall()

    stats = {"fixed": 0, "still_broken": 0}
    for row in errors:
        fpath = Path(row["file_path"])
        if not fpath.exists():
            stats["still_broken"] += 1
            continue
        try:
            _process_file(fpath, catalog_path, {})
            with CatalogWriter(catalog_path) as w:
                w.execute("DELETE FROM scan_errors WHERE file_path=?", (str(fpath),))
            stats["fixed"] += 1
        except Exception:
            stats["still_broken"] += 1

    log.info("fix_scan_errors: %s", stats)
    return stats
