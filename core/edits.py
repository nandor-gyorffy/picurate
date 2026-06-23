"""Non-destructive photo edits: stored in catalog, applied at view/export time."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.db.catalog import CatalogWriter, get_connection

if TYPE_CHECKING:
    from PIL import Image as PILImage

_DEFAULT_EDIT = {
    "crop_x": 0.0,
    "crop_y": 0.0,
    "crop_w": 1.0,
    "crop_h": 1.0,
    "rotate": 0,
    "brightness": 0.0,
    "contrast": 0.0,
    "saturation": 0.0,
}

_COLUMNS = list(_DEFAULT_EDIT.keys())


def get_edit(photo_id: int, catalog_path) -> dict | None:
    """Return edit dict or None if no edit row exists for photo_id."""
    conn = get_connection(Path(catalog_path) if catalog_path else None)
    row = conn.execute(
        "SELECT crop_x, crop_y, crop_w, crop_h, rotate, brightness, contrast, saturation"
        " FROM photo_edits WHERE photo_id = ?",
        (photo_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def set_edit(photo_id: int, catalog_path, **fields) -> None:
    """Upsert edit fields for photo_id. Only the supplied fields are updated."""
    if not fields:
        return
    # Ensure only valid columns are touched
    valid = {k: v for k, v in fields.items() if k in _DEFAULT_EDIT}
    if not valid:
        return

    with CatalogWriter(Path(catalog_path) if catalog_path else None) as conn:
        existing = conn.execute(
            "SELECT photo_id FROM photo_edits WHERE photo_id = ?", (photo_id,)
        ).fetchone()
        if existing is None:
            # Insert a full default row first, then apply the supplied fields
            defaults = dict(_DEFAULT_EDIT)
            defaults.update(valid)
            conn.execute(
                """
                INSERT INTO photo_edits
                    (photo_id, crop_x, crop_y, crop_w, crop_h,
                     rotate, brightness, contrast, saturation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    photo_id,
                    defaults["crop_x"],
                    defaults["crop_y"],
                    defaults["crop_w"],
                    defaults["crop_h"],
                    defaults["rotate"],
                    defaults["brightness"],
                    defaults["contrast"],
                    defaults["saturation"],
                ),
            )
        else:
            # Update only the supplied columns
            set_clause = ", ".join(f"{col} = ?" for col in valid)
            params = list(valid.values()) + [photo_id]
            conn.execute(
                f"UPDATE photo_edits SET {set_clause} WHERE photo_id = ?", params
            )


def clear_edit(photo_id: int, catalog_path) -> None:
    """Remove all edits for photo_id."""
    with CatalogWriter(Path(catalog_path) if catalog_path else None) as conn:
        conn.execute("DELETE FROM photo_edits WHERE photo_id = ?", (photo_id,))


def has_edit(photo_id: int, catalog_path) -> bool:
    """True if photo has any non-default edit applied."""
    edit = get_edit(photo_id, catalog_path)
    if edit is None:
        return False
    return (
        edit["crop_x"] != 0.0
        or edit["crop_y"] != 0.0
        or edit["crop_w"] != 1.0
        or edit["crop_h"] != 1.0
        or edit["rotate"] != 0
        or edit["brightness"] != 0.0
        or edit["contrast"] != 0.0
        or edit["saturation"] != 0.0
    )


def apply_edit_to_image(img, edit: dict | None):
    """Apply edit dict to a PIL Image.  Returns modified image (may be same object).

    Order: rotate → crop → brightness/contrast/saturation.
    """
    if edit is None:
        return img

    from PIL import ImageEnhance

    # 1. Rotate
    rotate = int(edit.get("rotate", 0))
    if rotate:
        img = img.rotate(rotate, expand=True)

    # 2. Crop (normalised fractions of the post-rotation dimensions)
    x = float(edit.get("crop_x", 0.0))
    y = float(edit.get("crop_y", 0.0))
    cw = float(edit.get("crop_w", 1.0))
    ch = float(edit.get("crop_h", 1.0))
    if not (x == 0.0 and y == 0.0 and cw == 1.0 and ch == 1.0):
        w, h = img.size
        left = int(x * w)
        upper = int(y * h)
        right = int((x + cw) * w)
        lower = int((y + ch) * h)
        # Clamp to image bounds
        left = max(0, min(left, w))
        upper = max(0, min(upper, h))
        right = max(left + 1, min(right, w))
        lower = max(upper + 1, min(lower, h))
        img = img.crop((left, upper, right, lower))

    # 3. Brightness/Contrast/Saturation  (factor = 1.0 + value)
    brightness = float(edit.get("brightness", 0.0))
    if brightness != 0.0:
        img = ImageEnhance.Brightness(img).enhance(1.0 + brightness)

    contrast = float(edit.get("contrast", 0.0))
    if contrast != 0.0:
        img = ImageEnhance.Contrast(img).enhance(1.0 + contrast)

    saturation = float(edit.get("saturation", 0.0))
    if saturation != 0.0:
        img = ImageEnhance.Color(img).enhance(1.0 + saturation)

    return img
