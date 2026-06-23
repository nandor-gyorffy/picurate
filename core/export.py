"""Export engine: copy/resize photos, verify hashes, optional GPS strip."""
from __future__ import annotations

import hashlib
import io
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.logger import get_logger

log = get_logger("picurate.export")

# Layout constants
LAYOUT_FLAT     = "flat"
LAYOUT_BY_YEAR  = "by_year"
LAYOUT_BY_DATE  = "by_date"

# Naming constants
NAMING_ORIGINAL   = "original"
NAMING_SEQUENTIAL = "sequential"
NAMING_DATE       = "date_name"


@dataclass
class ExportOptions:
    resize: bool = False
    max_dim: int = 1920
    quality: int = 85
    layout: str = LAYOUT_FLAT
    naming: str = NAMING_ORIGINAL
    strip_gps: bool = False
    html_gallery: bool = False
    contact_sheet: bool = False


def export_collection(
    collection_id: int,
    dest_folder: Path,
    options: ExportOptions,
    catalog_path: Path,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
    """
    Export all photos in *collection_id* to *dest_folder*.

    Returns stats: {exported, skipped, errors, verify_failures, dest}.
    """
    from core.db.catalog import get_connection
    from core.query import get_photos

    conn = get_connection(catalog_path)
    rows = get_photos(conn, collection_id=collection_id, limit=100_000)

    dest_folder = Path(dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)

    total = len(rows)
    stats = {"exported": 0, "skipped": 0, "errors": 0, "verify_failures": 0, "dest": str(dest_folder)}
    seq = 0

    from core.edits import has_edit

    for i, row in enumerate(rows):
        if progress_cb:
            progress_cb(i, total)

        src = Path(row["file_path"])
        if not src.exists():
            log.warning("Source missing: %s", src)
            stats["skipped"] += 1
            continue

        try:
            rel_dir = _dest_subdir(row, options)
            out_dir = dest_folder / rel_dir if rel_dir else dest_folder
            out_dir.mkdir(parents=True, exist_ok=True)

            seq += 1
            out_name = _dest_name(row, options, seq, src.suffix)
            out_path = _unique_path(out_dir / out_name)

            _copy_photo(src, out_path, options, photo_id=row["id"], catalog_path=catalog_path)

            photo_has_edit = has_edit(row["id"], catalog_path)
            if not _verify(src, out_path, options, photo_has_edit=photo_has_edit):
                log.error("Hash verify failed: %s", out_path)
                stats["verify_failures"] += 1
            else:
                stats["exported"] += 1

        except Exception as exc:
            log.error("Export error for %s: %s", src, exc)
            stats["errors"] += 1

    if progress_cb:
        progress_cb(total, total)

    if options.html_gallery and stats["exported"] > 0:
        from core.gallery import generate_gallery
        generate_gallery(rows, dest_folder)

    if options.contact_sheet and stats["exported"] > 0:
        from core.contact_sheet import generate_contact_sheet
        generate_contact_sheet(rows, dest_folder / "contact_sheet.pdf")

    return stats


# ── Internal helpers ──────────────────────────────────────────────────────────

def _dest_subdir(row, options: ExportOptions) -> str:
    if options.layout == LAYOUT_FLAT:
        return ""
    date = (row["date_taken"] or "")[:10]  # "YYYY-MM-DD" or ""
    if not date:
        return "undated"
    parts = date.split("-")
    if options.layout == LAYOUT_BY_YEAR:
        return parts[0]
    if options.layout == LAYOUT_BY_DATE:
        year = parts[0]
        month = parts[1] if len(parts) > 1 else "00"
        return f"{year}/{month}"
    return ""


def _dest_name(row, options: ExportOptions, seq: int, ext: str) -> str:
    orig = Path(row["filename"] or f"photo{seq}").stem
    date = (row["date_taken"] or "")[:10].replace("-", "")

    if options.naming == NAMING_SEQUENTIAL:
        return f"{seq:04d}_{orig}{ext}"
    if options.naming == NAMING_DATE:
        prefix = date if date else f"{seq:04d}"
        return f"{prefix}_{orig}{ext}"
    return row["filename"] or f"{seq:04d}{ext}"


def _unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suffix = p.stem, p.suffix
    i = 2
    while True:
        candidate = p.parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _copy_photo(
    src: Path,
    dest: Path,
    options: ExportOptions,
    photo_id: int | None = None,
    catalog_path: Path | None = None,
) -> None:
    from core.edits import get_edit, has_edit

    photo_has_edit = (
        photo_id is not None
        and catalog_path is not None
        and has_edit(photo_id, catalog_path)
    )
    if options.resize or options.strip_gps or photo_has_edit:
        edit = get_edit(photo_id, catalog_path) if photo_has_edit else None
        _copy_via_pillow(src, dest, options, edit=edit)
    else:
        shutil.copy2(src, dest)


def _copy_via_pillow(
    src: Path,
    dest: Path,
    options: ExportOptions,
    edit: dict | None = None,
) -> None:
    from PIL import Image, ImageOps
    from core.edits import apply_edit_to_image

    img = Image.open(src)
    img = ImageOps.exif_transpose(img)

    # Apply non-destructive edits (rotate/crop/adjustments) before resize
    if edit is not None:
        img = apply_edit_to_image(img, edit)

    if options.resize:
        img.thumbnail((options.max_dim, options.max_dim), Image.LANCZOS)

    exif_bytes: bytes | None = None
    if not options.strip_gps:
        # Preserve EXIF (minus orientation which we already applied)
        exif_bytes = _exif_without_orientation(img)
    else:
        exif_bytes = _exif_strip_gps(img)

    save_kwargs: dict = {}
    suffix = dest.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        save_kwargs["format"] = "JPEG"
        save_kwargs["quality"] = options.quality
        save_kwargs["subsampling"] = 0
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes
    elif suffix in (".png",):
        save_kwargs["format"] = "PNG"
    else:
        # For HEIC/RAW outputs, just save as JPEG with the destination suffix
        save_kwargs["format"] = "JPEG"
        save_kwargs["quality"] = options.quality
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes

    img = img.convert("RGB") if save_kwargs.get("format") == "JPEG" else img
    img.save(dest, **save_kwargs)


def _exif_without_orientation(img) -> bytes | None:
    try:
        import piexif
        raw = img.info.get("exif")
        if not raw:
            return None
        exif = piexif.load(raw)
        # Zero out orientation tag (274) so display isn't doubled
        exif["0th"].pop(piexif.ImageIFD.Orientation, None)
        return piexif.dump(exif)
    except Exception:
        return None


def _exif_strip_gps(img) -> bytes | None:
    try:
        import piexif
        raw = img.info.get("exif")
        if not raw:
            return None
        exif = piexif.load(raw)
        exif["GPS"] = {}
        exif["0th"].pop(piexif.ImageIFD.Orientation, None)
        return piexif.dump(exif)
    except Exception:
        return None


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify(src: Path, dest: Path, options: ExportOptions, photo_has_edit: bool = False) -> bool:
    """Verify copy integrity. For resized/processed copies, just check dest exists and has size > 0."""
    if not dest.exists() or dest.stat().st_size == 0:
        return False
    if options.resize or options.strip_gps or photo_has_edit:
        # Transformed — can't compare hashes, just verify dest is non-empty
        return True
    # Verbatim copy — hashes must match
    return _hash_file(src) == _hash_file(dest)
