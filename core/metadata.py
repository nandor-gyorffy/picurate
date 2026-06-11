"""Rating and flag writes for individual photos."""
from __future__ import annotations

from pathlib import Path

from core.db.catalog import CatalogWriter
from core.logger import get_logger

log = get_logger("picurate.metadata")

# Flag constants
FLAG_NONE = 0
FLAG_PICK = 1
FLAG_REJECT = 2


def set_rating(photo_id: int, rating: int, catalog_path: Path | None = None) -> None:
    """Set star rating 0-5 for a photo."""
    if not (0 <= rating <= 5):
        raise ValueError(f"rating must be 0-5, got {rating}")
    with CatalogWriter(catalog_path) as conn:
        conn.execute("UPDATE photos SET rating=? WHERE id=?", (rating, photo_id))


def set_flag(photo_id: int, flag: int, catalog_path: Path | None = None) -> None:
    """Set flag: 0=none, 1=pick, 2=reject."""
    if flag not in (FLAG_NONE, FLAG_PICK, FLAG_REJECT):
        raise ValueError(f"flag must be 0/1/2, got {flag}")
    with CatalogWriter(catalog_path) as conn:
        conn.execute("UPDATE photos SET flag=? WHERE id=?", (flag, photo_id))


def get_rating(photo_id: int, catalog_path: Path | None = None) -> int:
    from core.db.catalog import get_connection
    conn = get_connection(catalog_path)
    row = conn.execute("SELECT rating FROM photos WHERE id=?", (photo_id,)).fetchone()
    return row["rating"] if row else 0


def get_flag(photo_id: int, catalog_path: Path | None = None) -> int:
    from core.db.catalog import get_connection
    conn = get_connection(catalog_path)
    row = conn.execute("SELECT flag FROM photos WHERE id=?", (photo_id,)).fetchone()
    return row["flag"] if row else 0
