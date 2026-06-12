"""Import engine: match ImportRecords to catalog photos and apply metadata."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from core.db.catalog import CatalogWriter, get_connection
from core.importers.base import ImportRecord
from core.logger import get_logger

log = get_logger("picurate.importer.engine")

# Conflict strategies
CONFLICT_KEEP   = "keep"    # keep existing catalog value
CONFLICT_PREFER = "prefer"  # prefer imported value
CONFLICT_MERGE  = "merge"   # merge (for lists); prefer import for scalars


def match_records(
    records: list[ImportRecord],
    catalog_path: Path,
) -> list[ImportRecord]:
    """
    Match each ImportRecord to a photo in the catalog.

    Strategy (in order):
    1. Exact file_path match
    2. filename + (optionally) date match

    Sets record.matched_photo_id.  Unmatched records have matched_photo_id=None.
    """
    conn = get_connection(catalog_path)

    # Build lookup tables
    path_to_id: dict[str, int] = {}
    name_to_rows: dict[str, list[dict]] = {}

    for row in conn.execute("SELECT id, file_path, filename, date_taken FROM photos WHERE status='ok'"):
        path_to_id[row["file_path"]] = row["id"]
        name = row["filename"] or ""
        name_to_rows.setdefault(name, []).append(dict(row))

    for rec in records:
        # 1. Exact path
        if rec.source_path in path_to_id:
            rec.matched_photo_id = path_to_id[rec.source_path]
            continue
        # 2. filename match
        candidates = name_to_rows.get(rec.filename, [])
        if len(candidates) == 1:
            rec.matched_photo_id = candidates[0]["id"]
        elif len(candidates) > 1:
            # Multiple files with the same name — match is ambiguous, skip
            log.debug("Ambiguous match for %s (%d candidates)", rec.filename, len(candidates))

    return records


def apply_records(
    records: list[ImportRecord],
    catalog_path: Path,
    source_type: str,
    source_path: str,
    conflict: str = CONFLICT_PREFER,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
    """
    Apply matched ImportRecords to the catalog.

    Returns stats: {matched, applied, skipped, albums_created, batch_id}.
    """
    total = len(records)
    matched_recs = [r for r in records if r.matched_photo_id is not None]

    stats = {
        "total": total,
        "matched": len(matched_recs),
        "applied": 0,
        "skipped": 0,
        "albums_created": 0,
        "batch_id": None,
    }

    if not matched_recs:
        return stats

    # Ensure collections exist for all album names
    album_name_to_id = _ensure_collections(matched_recs, catalog_path)
    stats["albums_created"] = len(album_name_to_id)

    # Record original values for undo
    undo_data = _snapshot_originals([r.matched_photo_id for r in matched_recs], catalog_path)

    with CatalogWriter(catalog_path) as conn:
        # Record batch
        conn.execute(
            """INSERT INTO import_batches (source_type, source_path, run_at, record_count, undo_data)
               VALUES (?,?,?,?,?)""",
            (source_type, source_path, datetime.now().isoformat(),
             len(matched_recs), json.dumps(undo_data)),
        )
        batch_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        stats["batch_id"] = batch_id

        for i, rec in enumerate(matched_recs):
            if progress_cb:
                progress_cb(i, len(matched_recs))

            pid = rec.matched_photo_id
            existing = conn.execute(
                "SELECT rating, flag, caption, keywords FROM photos WHERE id=?", (pid,)
            ).fetchone()
            if existing is None:
                stats["skipped"] += 1
                continue

            updates: dict[str, object] = {}

            if rec.rating is not None:
                if conflict == CONFLICT_KEEP and existing["rating"]:
                    pass
                else:
                    updates["rating"] = rec.rating

            if rec.flag is not None:
                if conflict == CONFLICT_KEEP and existing["flag"]:
                    pass
                else:
                    updates["flag"] = rec.flag

            if rec.caption:
                if conflict == CONFLICT_KEEP and existing["caption"]:
                    pass
                else:
                    updates["caption"] = rec.caption

            if rec.keywords:
                existing_kw = set((existing["keywords"] or "").split(",")) - {""}
                if conflict == CONFLICT_MERGE:
                    merged = existing_kw | set(rec.keywords)
                    updates["keywords"] = ",".join(sorted(merged))
                elif conflict == CONFLICT_KEEP and existing_kw:
                    pass
                else:
                    updates["keywords"] = ",".join(rec.keywords)

            if updates:
                set_clause = ", ".join(f"{k}=?" for k in updates)
                conn.execute(
                    f"UPDATE photos SET {set_clause} WHERE id=?",
                    list(updates.values()) + [pid],
                )

            # Add to collections
            for album_name in rec.album_names:
                cid = album_name_to_id.get(album_name)
                if cid is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO collection_photos (collection_id, photo_id) VALUES (?,?)",
                        (cid, pid),
                    )

            stats["applied"] += 1

        if progress_cb:
            progress_cb(len(matched_recs), len(matched_recs))

    return stats


def undo_batch(batch_id: int, catalog_path: Path) -> bool:
    """Reverse an import batch using stored undo data. Returns True on success."""
    conn = get_connection(catalog_path)
    row = conn.execute(
        "SELECT undo_data FROM import_batches WHERE id=?", (batch_id,)
    ).fetchone()
    if row is None or not row["undo_data"]:
        return False

    undo_data: dict = json.loads(row["undo_data"])

    with CatalogWriter(catalog_path) as wconn:
        for photo_id_str, orig in undo_data.items():
            pid = int(photo_id_str)
            wconn.execute(
                "UPDATE photos SET rating=?, flag=?, caption=?, keywords=? WHERE id=?",
                (orig["rating"], orig["flag"], orig["caption"], orig["keywords"], pid),
            )
            # Remove collection memberships added by this batch — stored in undo_data["collections"]
            for cid, pid2 in orig.get("collection_photos", []):
                wconn.execute(
                    "DELETE FROM collection_photos WHERE collection_id=? AND photo_id=?",
                    (cid, pid2),
                )
        wconn.execute("DELETE FROM import_batches WHERE id=?", (batch_id,))

    return True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_collections(records: list[ImportRecord], catalog_path: Path) -> dict[str, int]:
    """Create any missing collections and return {name: id}."""
    from core.collections import create_collection, get_collections
    existing = {c["name"]: c["id"] for c in get_collections(catalog_path)}
    result: dict[str, int] = {}
    needed = {name for rec in records for name in rec.album_names}
    for name in needed:
        if name in existing:
            result[name] = existing[name]
        else:
            cid = create_collection(name, catalog_path=catalog_path)
            result[name] = cid
            existing[name] = cid
    return result


def _snapshot_originals(photo_ids: list[int], catalog_path: Path) -> dict:
    """Capture current rating/flag/caption/keywords for undo."""
    conn = get_connection(catalog_path)
    snap: dict[str, dict] = {}
    for pid in photo_ids:
        row = conn.execute(
            "SELECT rating, flag, caption, keywords FROM photos WHERE id=?", (pid,)
        ).fetchone()
        if row:
            snap[str(pid)] = {
                "rating": row["rating"],
                "flag": row["flag"],
                "caption": row["caption"],
                "keywords": row["keywords"],
                "collection_photos": [],
            }
    return snap
