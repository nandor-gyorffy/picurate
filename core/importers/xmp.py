"""XMP/IPTC importer: read embedded metadata from photo files."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

from core.importers.base import BaseImporter, ImportRecord
from core.logger import get_logger

log = get_logger("picurate.importer.xmp")

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff",
               ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".dng", ".raf"}

# XMP namespace map
_NS = {
    "rdf":  "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xmp":  "http://ns.adobe.com/xap/1.0/",
    "dc":   "http://purl.org/dc/elements/1.1/",
    "lr":   "http://ns.adobe.com/lightroom/1.0/",
    "MWG":  "http://www.metadataworkinggroup.com/schemas/regions/",
}


def _xmp_rating(root: ET.Element) -> int | None:
    """Extract xmp:Rating (0-5) or return None."""
    for desc in root.iter(f"{{{_NS['rdf']}}}Description"):
        r = desc.get(f"{{{_NS['xmp']}}}Rating")
        if r is not None:
            try:
                return max(0, min(5, int(float(r))))
            except ValueError:
                pass
    return None


def _xmp_caption(root: ET.Element) -> str | None:
    """Extract dc:description or dc:title as caption."""
    for ns_tag in ("description", "title"):
        for el in root.iter(f"{{{_NS['dc']}}}{ns_tag}"):
            # dc:description/Alt/li or plain text
            li = el.find(f".//{{{_NS['rdf']}}}li")
            text = (li.text if li is not None else el.text) or ""
            text = text.strip()
            if text:
                return text
    return None


def _xmp_keywords(root: ET.Element) -> list[str]:
    """Extract dc:subject items."""
    keywords: list[str] = []
    for subj in root.iter(f"{{{_NS['dc']}}}subject"):
        for li in subj.iter(f"{{{_NS['rdf']}}}li"):
            if li.text and li.text.strip():
                keywords.append(li.text.strip())
    # Also look for lr:hierarchicalSubject
    for subj in root.iter(f"{{{_NS['lr']}}}hierarchicalSubject"):
        for li in subj.iter(f"{{{_NS['rdf']}}}li"):
            if li.text and li.text.strip():
                kw = li.text.strip().split("|")[-1].strip()
                if kw and kw not in keywords:
                    keywords.append(kw)
    return keywords


def _extract_xmp(file_path: Path) -> bytes | None:
    """Pull raw XMP bytes out of a JPEG/PNG/TIFF using Pillow."""
    try:
        from PIL import Image
        img = Image.open(file_path)
        return img.info.get("xmp")
    except Exception:
        return None


def _parse_xmp(data: bytes) -> tuple[int | None, str | None, list[str]]:
    """Return (rating, caption, keywords) from raw XMP bytes."""
    try:
        text = data.decode("utf-8", errors="replace")
        # Strip BOM / leading garbage before <?xpacket
        m = re.search(r"<\?xpacket", text)
        if m:
            text = text[m.start():]
        root = ET.fromstring(text)
        return _xmp_rating(root), _xmp_caption(root), _xmp_keywords(root)
    except Exception as exc:
        log.debug("XMP parse error: %s", exc)
        return None, None, []


def _extract_iptc(file_path: Path) -> tuple[str | None, list[str]]:
    """Pull caption (IPTC 2:120) and keywords (IPTC 2:25) from JPEG."""
    try:
        from PIL import Image
        img = Image.open(file_path)
        iptc = img.info.get("photoshop") or {}
        # Pillow exposes IPTC under 'photoshop' for JPEG
        # Use IptcImagePlugin directly
        from PIL import IptcImagePlugin
        iptc_data = IptcImagePlugin.getiptcinfo(img)
        if not iptc_data:
            return None, []
        caption_bytes = iptc_data.get((2, 120))
        keyword_items = iptc_data.get((2, 25)) or []

        caption = None
        if caption_bytes:
            if isinstance(caption_bytes, (list, tuple)):
                caption_bytes = caption_bytes[0]
            caption = caption_bytes.decode("utf-8", errors="replace").strip() or None

        keywords = []
        if isinstance(keyword_items, (list, tuple)):
            for kw in keyword_items:
                if isinstance(kw, bytes):
                    kw = kw.decode("utf-8", errors="replace")
                if kw.strip():
                    keywords.append(kw.strip())
        elif isinstance(keyword_items, bytes):
            kw = keyword_items.decode("utf-8", errors="replace").strip()
            if kw:
                keywords.append(kw)

        return caption, keywords
    except Exception:
        return None, []


class XmpImporter(BaseImporter):
    """Read XMP and IPTC metadata embedded in photo files within a folder tree."""

    source_type = "xmp"

    def records(self, source_path: str) -> Iterator[ImportRecord]:
        root = Path(source_path)
        if not root.is_dir():
            return

        for fp in sorted(root.rglob("*")):
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in _PHOTO_EXTS:
                continue

            rating: int | None = None
            caption: str | None = None
            keywords: list[str] = []

            xmp_raw = _extract_xmp(fp)
            if xmp_raw:
                rating, caption, keywords = _parse_xmp(xmp_raw)

            # IPTC fallback / supplement
            if caption is None or not keywords:
                iptc_cap, iptc_kw = _extract_iptc(fp)
                if caption is None:
                    caption = iptc_cap
                if not keywords:
                    keywords = iptc_kw

            # Only yield if there's something useful
            if rating is not None or caption or keywords:
                yield ImportRecord(
                    filename=fp.name,
                    source_path=str(fp),
                    rating=rating,
                    caption=caption,
                    keywords=keywords,
                )
