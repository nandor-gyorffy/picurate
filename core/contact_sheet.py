"""Contact-sheet PDF generator using Pillow."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from core.logger import get_logger

log = get_logger("picurate.contact_sheet")

# A4 at 150 dpi
PAGE_W = 1240
PAGE_H = 1754
MARGIN = 40
COLS = 5
FONT_SIZE = 14


def generate_contact_sheet(
    photo_rows,
    dest_path: Path,
    title: str = "Contact Sheet",
    cols: int = COLS,
) -> Path:
    """
    Generate a multi-page contact-sheet PDF at *dest_path*.
    Returns dest_path.
    """
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    cell_w = (PAGE_W - MARGIN * 2) // cols
    cell_h = int(cell_w * 0.75) + FONT_SIZE + 6
    rows_per_page = max(1, (PAGE_H - MARGIN * 2) // cell_h)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", FONT_SIZE)
    except OSError:
        font = ImageFont.load_default()

    pages: list[Image.Image] = []
    current_page: Image.Image | None = None
    draw: ImageDraw.ImageDraw | None = None
    pos = 0

    def new_page():
        nonlocal current_page, draw
        p = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
        draw = ImageDraw.Draw(p)
        return p

    for row in photo_rows:
        thumb_path = row["thumbnail_path"] or ""
        file_path = row["file_path"] or ""
        filename = row["filename"] or "photo"

        src = Path(thumb_path) if thumb_path and Path(thumb_path).exists() else (
            Path(file_path) if file_path and Path(file_path).exists() else None
        )
        if src is None:
            continue

        try:
            img = Image.open(src)
            img = ImageOps.exif_transpose(img)
            thumb_size = cell_w - 4, int((cell_w - 4) * 0.75)
            img.thumbnail(thumb_size, Image.LANCZOS)
            img = img.convert("RGB")
        except Exception as exc:
            log.warning("Contact sheet: can't load %s: %s", src, exc)
            continue

        col_idx = pos % cols
        row_idx = (pos % (cols * rows_per_page)) // cols

        if pos % (cols * rows_per_page) == 0:
            if current_page is not None:
                pages.append(current_page)
            current_page = new_page()

        x = MARGIN + col_idx * cell_w + (cell_w - img.width) // 2
        y = MARGIN + row_idx * cell_h
        current_page.paste(img, (x, y))

        # Filename label below thumbnail
        label = filename if len(filename) <= 22 else filename[:19] + "…"
        draw.text((MARGIN + col_idx * cell_w + 2, y + img.height + 2), label,
                  fill=(60, 60, 60), font=font)
        pos += 1

    if current_page is not None:
        pages.append(current_page)

    if not pages:
        log.warning("No photos for contact sheet")
        return dest_path

    pages[0].save(
        str(dest_path),
        format="PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=150,
    )
    log.info("Contact sheet written to %s (%d pages)", dest_path, len(pages))
    return dest_path
