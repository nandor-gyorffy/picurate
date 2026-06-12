"""Picasa .picasa.ini importer.

Reads star, caption, album membership, and face-box data.
See: https://gist.github.com/fbraz3/226e01e1a7cd3f891c34

Face rect64 format: rect64(hex) where hex encodes four 16-bit BE values
(left, top, right, bottom) each divided by 65535 to get 0-1 coords.
"""
from __future__ import annotations

import configparser
import struct
from pathlib import Path
from typing import Iterator

from core.importers.base import BaseImporter, ImportRecord
from core.logger import get_logger

log = get_logger("picurate.importer.picasa")

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff",
               ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".dng", ".raf"}


def _decode_rect64(hex_str: str) -> tuple[float, float, float, float] | None:
    """rect64(abcdef01...) → (left, top, right, bottom) in [0,1]."""
    try:
        s = hex_str.strip()
        if s.lower().startswith("rect64("):
            s = s[7:]
            if s.endswith(")"):
                s = s[:-1]
        val = int(s, 16)
        left   = ((val >> 48) & 0xFFFF) / 65535.0
        top    = ((val >> 32) & 0xFFFF) / 65535.0
        right  = ((val >> 16) & 0xFFFF) / 65535.0
        bottom = (val & 0xFFFF) / 65535.0
        return left, top, right, bottom
    except Exception:
        return None


def _parse_faces(faces_str: str) -> list[tuple[str, tuple[float, float, float, float]]]:
    """faces=rect64(hex),name_token;... → [(name, (l,t,r,b)), ...]"""
    result = []
    for part in faces_str.split(";"):
        part = part.strip()
        if not part:
            continue
        # Each part: rect64(hex),name_token
        if "," not in part:
            continue
        rect_part, name_part = part.split(",", 1)
        coords = _decode_rect64(rect_part.strip())
        if coords:
            result.append((name_part.strip(), coords))
    return result


def _read_ini(path: Path) -> configparser.RawConfigParser:
    cfg = configparser.RawConfigParser()
    cfg.optionxform = str  # preserve case
    try:
        cfg.read(str(path), encoding="utf-8")
    except Exception:
        try:
            cfg.read(str(path), encoding="latin-1")
        except Exception:
            pass
    return cfg


def _collect_albums(cfg: configparser.RawConfigParser) -> dict[str, str]:
    """Return {token: album_name} from [.album:token] sections."""
    albums: dict[str, str] = {}
    for section in cfg.sections():
        low = section.lower()
        if low.startswith(".album:") or low.startswith("albums:"):
            token = section.split(":", 1)[1]
            name = cfg.get(section, "name", fallback=token)
            albums[token] = name
    return albums


class PicasaImporter(BaseImporter):
    """Walk a folder tree for .picasa.ini files and extract metadata."""

    source_type = "picasa"

    def records(self, source_path: str) -> Iterator[ImportRecord]:
        root = Path(source_path)
        if not root.is_dir():
            return

        for ini_path in sorted(root.rglob(".picasa.ini")):
            folder = ini_path.parent
            cfg = _read_ini(ini_path)
            albums = _collect_albums(cfg)

            for section in cfg.sections():
                # Skip special sections
                low = section.lower()
                if low in ("picasa",) or low.startswith(".album") or low.startswith("albums"):
                    continue

                # Check it looks like a photo filename
                ext = Path(section).suffix.lower()
                if ext not in _PHOTO_EXTS:
                    continue

                fp = folder / section
                rating: int | None = None
                flag: int | None = None

                star = cfg.get(section, "star", fallback="no").strip().lower()
                if star == "yes":
                    flag = 1  # FLAG_PICK

                caption = cfg.get(section, "caption", fallback=None)
                if caption is not None:
                    caption = caption.strip() or None

                # Albums: comma-separated tokens
                album_tokens = cfg.get(section, "albums", fallback="").split(",")
                album_names = [
                    albums.get(t.strip(), t.strip())
                    for t in album_tokens if t.strip()
                ]

                # Faces
                faces_str = cfg.get(section, "faces", fallback="")
                faces = _parse_faces(faces_str) if faces_str else []

                yield ImportRecord(
                    filename=section,
                    source_path=str(fp),
                    rating=rating,
                    flag=flag,
                    caption=caption,
                    album_names=album_names,
                    faces=faces,
                )
