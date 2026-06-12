"""Photo quality scoring — sharpness (Laplacian) + exposure analysis.

All scoring is pure CPU via numpy + Pillow; no ML model required.
Returns a float in [0, 1]; higher is better.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger

log = get_logger("picurate.quality")

# Tune these if typical scores bunch at extremes in practice.
_SHARPNESS_SCALE = 500.0   # Laplacian variance at which sharpness = 1.0
_EXTREME_THRESHOLD = 10    # pixel value (0-255) for very dark / very bright cutoff
_EXTREME_FRACTION_MAX = 0.20  # fraction of extreme pixels that maps to 0 exposure score


def compute_quality_components(file_path: str | Path) -> tuple[float, float, float] | None:
    """Return (total_score, sharpness_score, exposure_score) or None on failure.

    All three values are in [0, 1]; higher is better.
    """
    try:
        from PIL import Image
        img = Image.open(file_path).convert("L")  # grayscale
        img.thumbnail((512, 512))
        arr = np.array(img, dtype=np.float32)

        # Sharpness: variance of the discrete Laplacian
        lap = (
            arr[:-2, 1:-1] + arr[2:, 1:-1] +
            arr[1:-1, :-2] + arr[1:-1, 2:] -
            4 * arr[1:-1, 1:-1]
        )
        sharpness = min(float(np.var(lap)) / _SHARPNESS_SCALE, 1.0)

        # Exposure: penalise blown highlights + crushed shadows
        total = arr.size
        dark = float(np.sum(arr < _EXTREME_THRESHOLD)) / total
        bright = float(np.sum(arr > (255 - _EXTREME_THRESHOLD))) / total
        extreme = min((dark + bright) / _EXTREME_FRACTION_MAX, 1.0)
        exposure = 1.0 - extreme

        quality = round(0.65 * sharpness + 0.35 * exposure, 4)
        return quality, round(sharpness, 4), round(exposure, 4)
    except Exception as exc:
        log.warning("Quality scoring failed for %s: %s", file_path, exc)
        return None


def compute_quality_score(file_path: str | Path) -> float | None:
    """Return a [0, 1] quality score, or None on failure."""
    result = compute_quality_components(file_path)
    return result[0] if result is not None else None


def compute_quality_batch(catalog_path: Path | None = None) -> dict:
    """Enqueue quality-score jobs for photos that don't have one yet."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        """SELECT id, file_path FROM photos
           WHERE status NOT IN ('missing', 'duplicate') AND quality_score IS NULL"""
    ).fetchall()
    with CatalogWriter(catalog_path) as wconn:
        for row in rows:
            wconn.execute(
                "INSERT INTO jobs(job_type, payload, status) VALUES('quality',?,?)",
                (json.dumps({"photo_id": row["id"], "path": row["file_path"]}), "pending"),
            )
    return {"enqueued": len(rows)}
