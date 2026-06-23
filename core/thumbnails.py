"""Thumbnail generation and caching. Always applies EXIF orientation."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageOps

from core.paths import thumbnail_dir

THUMB_SIZE = (256, 256)

# Register pillow-heif so Pillow can open HEIC/HEIF files
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass


def _thumb_path(photo_path: Path, size: int = 256) -> Path:
    key = hashlib.sha1(str(photo_path).encode()).hexdigest()
    return thumbnail_dir() / f"{key}_{size}.jpg"


def get_thumbnail(photo_path: Path, size: int = 256, force_regen: bool = False) -> Path | None:
    """Return path to cached thumbnail, generating it if needed. Returns None on error.

    If *force_regen* is True and the cached thumbnail already exists, it is
    deleted so the thumbnail is regenerated from the source file.
    """
    dest = _thumb_path(photo_path, size)
    if force_regen and dest.exists():
        dest.unlink(missing_ok=True)
    if dest.exists():
        return dest
    return _generate(photo_path, dest, size)


def _generate(src: Path, dest: Path, size: int) -> Path | None:
    try:
        suffix = src.suffix.lower()
        if suffix in (".cr2", ".nef", ".arw", ".dng", ".orf", ".rw2", ".raw"):
            img = _open_raw(src)
        else:
            img = Image.open(src)

        img = ImageOps.exif_transpose(img)  # ALWAYS apply orientation
        img.thumbnail((size, size), Image.LANCZOS)
        img = img.convert("RGB")
        img.save(dest, "JPEG", quality=85, optimize=True)
        return dest
    except Exception:
        return None


def _open_raw(src: Path) -> Image.Image:
    import rawpy
    with rawpy.imread(str(src)) as raw:
        # Use embedded preview thumbnail for speed
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                import io
                return Image.open(io.BytesIO(thumb.data))
        except Exception:
            pass
        rgb = raw.postprocess(use_camera_wb=True, half_size=True)
        return Image.fromarray(rgb)
