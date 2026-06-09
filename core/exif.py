"""EXIF extraction using Pillow. Date, GPS, camera, dimensions."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ExifTags, ImageOps

_EXIF_IFD_TAG = 34665
_GPS_IFD_TAG = 34853


def _parse_datetime(s: str) -> datetime | None:
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _gps_dms_to_decimal(dms, ref: str) -> float | None:
    try:
        deg, mn, sec = dms
        def to_f(v):
            if hasattr(v, "numerator") and hasattr(v, "denominator"):
                return v.numerator / v.denominator if v.denominator else 0.0
            if isinstance(v, tuple):
                return v[0] / v[1] if v[1] else 0.0
            return float(v)
        val = to_f(deg) + to_f(mn) / 60 + to_f(sec) / 3600
        if ref in ("S", "W"):
            val = -val
        return val
    except Exception:
        return None


def extract(path: Path) -> dict[str, Any]:
    """Return dict with: date_taken, camera_make, camera_model, width, height, gps_lat, gps_lon."""
    result: dict[str, Any] = {}
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        result["width"], result["height"] = img.size

        raw_exif = img.getexif()
        if raw_exif is None:
            raw_exif = {}

        # Top-level tags
        top = {ExifTags.TAGS.get(k, k): v for k, v in raw_exif.items()}

        # Exif sub-IFD (DateTimeOriginal lives here)
        sub_exif: dict = {}
        if _EXIF_IFD_TAG in raw_exif:
            sub_exif = {ExifTags.TAGS.get(k, k): v for k, v in raw_exif.get_ifd(_EXIF_IFD_TAG).items()}

        # GPS sub-IFD
        gps_ifd: dict = {}
        if _GPS_IFD_TAG in raw_exif:
            gps_ifd = {ExifTags.GPSTAGS.get(k, k): v for k, v in raw_exif.get_ifd(_GPS_IFD_TAG).items()}

        # Date — prefer DateTimeOriginal from Exif sub-IFD
        for key in ("DateTimeOriginal", "DateTimeDigitized"):
            if key in sub_exif:
                dt = _parse_datetime(str(sub_exif[key]))
                if dt:
                    result["date_taken"] = dt.isoformat()
                    break
        if "date_taken" not in result and "DateTime" in top:
            dt = _parse_datetime(str(top["DateTime"]))
            if dt:
                result["date_taken"] = dt.isoformat()
        if "date_taken" not in result:
            result["date_taken"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat()

        result["camera_make"] = str(top.get("Make", "")).strip() or None
        result["camera_model"] = str(top.get("Model", "")).strip() or None

        # GPS
        if gps_ifd:
            lat = _gps_dms_to_decimal(
                gps_ifd.get("GPSLatitude"), gps_ifd.get("GPSLatitudeRef", "N")
            )
            lon = _gps_dms_to_decimal(
                gps_ifd.get("GPSLongitude"), gps_ifd.get("GPSLongitudeRef", "E")
            )
            if lat is not None:
                result["gps_lat"] = lat
            if lon is not None:
                result["gps_lon"] = lon
    except Exception:
        pass
    return result
