"""Face detection and embedding using InsightFace (RetinaFace + ArcFace).

Model loading is lazy and optional: all public functions return empty results
(rather than crashing) when the models have not been downloaded yet.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from core.db.catalog import CatalogWriter, get_connection
from core.logger import get_logger
from core.paths import data_dir

log = get_logger("picurate.faces")

_analyzer = None          # shared FaceAnalysis instance (lazy)
_model_ready = False      # set True once .prepare() succeeds


def _get_analyzer():
    """Return a FaceAnalysis instance, or None if models unavailable."""
    global _analyzer, _model_ready
    if _model_ready:
        return _analyzer
    try:
        import insightface
        from insightface.app import FaceAnalysis

        model_root = data_dir() / "insightface"
        model_root.mkdir(parents=True, exist_ok=True)

        app = FaceAnalysis(
            name="buffalo_sc",      # lighter: ~170 MB; swap to buffalo_l for accuracy
            root=str(model_root),
            providers=["CPUExecutionProvider"],
        )
        app.prepare(ctx_id=0, det_size=(640, 640))
        _analyzer = app
        _model_ready = True
        log.info("InsightFace models loaded from %s", model_root)
        return _analyzer
    except Exception as exc:
        log.warning("InsightFace unavailable (%s) — face detection disabled.", exc)
        return None


def model_available() -> bool:
    """True if InsightFace models are loaded and ready."""
    return _get_analyzer() is not None


def detect_faces(file_path: str, det_thresh: float = 0.65, min_face_px: int = 60) -> list[dict]:
    """
    Run face detection + embedding on one photo file.

    Returns list of dicts: {bbox, embedding, det_score}.
    Only faces with det_score >= det_thresh are returned.
    Returns [] if model unavailable or file can't be read.
    """
    analyzer = _get_analyzer()
    if analyzer is None:
        return []
    try:
        import numpy as np
        from PIL import Image, ImageOps
        img = Image.open(file_path)
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        img_np = np.array(img)
        faces = analyzer.get(img_np)
        result = []
        for face in faces:
            score = float(getattr(face, "det_score", 1.0))
            if score < det_thresh:
                continue
            bbox = face.bbox.tolist() if hasattr(face.bbox, "tolist") else list(face.bbox)
            face_w = bbox[2] - bbox[0]
            face_h = bbox[3] - bbox[1]
            if face_w < min_face_px or face_h < min_face_px:
                continue
            emb  = face.embedding.tolist() if hasattr(face.embedding, "tolist") else list(face.embedding)
            result.append({"bbox": bbox, "embedding": emb, "det_score": score})
        return result
    except Exception as exc:
        log.debug("detect_faces failed for %s: %s", file_path, exc)
        return []


def process_photo_faces(
    photo_id: int,
    file_path: str,
    catalog_path: Path,
) -> int:
    """
    Detect faces in one photo and write results to the `faces` table.
    Returns number of faces stored.
    """
    faces = detect_faces(file_path)
    if not faces:
        return 0

    with CatalogWriter(catalog_path) as conn:
        # Remove stale entries from a prior run
        conn.execute("DELETE FROM faces WHERE photo_id=? AND source='insightface'", (photo_id,))
        for face in faces:
            conn.execute(
                """INSERT INTO faces (photo_id, bounding_box, embedding, source, confidence)
                   VALUES (?,?,?,?,?)""",
                (
                    photo_id,
                    json.dumps(face["bbox"]),
                    json.dumps(face["embedding"]),
                    "insightface",
                    face.get("det_score"),
                ),
            )
    return len(faces)


def get_faces_for_photo(photo_id: int, catalog_path: Path) -> list[dict]:
    """Return all stored face records for a photo."""
    conn = get_connection(catalog_path)
    rows = conn.execute(
        "SELECT id, bounding_box, embedding, person_id, confidence FROM faces WHERE photo_id=?",
        (photo_id,),
    ).fetchall()
    result = []
    for row in rows:
        result.append({
            "id": row["id"],
            "bbox": json.loads(row["bounding_box"] or "[]"),
            "embedding": json.loads(row["embedding"] or "[]"),
            "person_id": row["person_id"],
            "confidence": row["confidence"],
        })
    return result


def assign_person(face_id: int, person_id: int | None, catalog_path: Path) -> None:
    """Set the person_id for a specific face record."""
    with CatalogWriter(catalog_path) as conn:
        conn.execute("UPDATE faces SET person_id=? WHERE id=?", (person_id, face_id))


def delete_face(face_id: int, catalog_path: Path) -> None:
    """Permanently delete a face record (e.g. a false positive detection)."""
    with CatalogWriter(catalog_path) as conn:
        conn.execute("DELETE FROM faces WHERE id=?", (face_id,))


def get_face_crop_pixmap(face_id: int, catalog_path: Path, size: int = 96):
    """
    Return a QPixmap of the cropped face region (with 40% padding), scaled to size×size.
    Prefers the thumbnail for speed, but falls back to the original when the face region
    would be smaller than 24 px in the thumbnail (avoids pixelated blobs).
    Returns None on any failure.
    """
    conn = get_connection(catalog_path)
    row = conn.execute(
        """SELECT f.bounding_box, p.file_path, p.thumbnail_path
           FROM faces f JOIN photos p ON p.id = f.photo_id
           WHERE f.id = ?""",
        (face_id,)
    ).fetchone()
    if not row:
        return None

    try:
        import json as _json
        bbox = _json.loads(row["bounding_box"] or "[]")
        if len(bbox) < 4:
            return None
        ox1, oy1, ox2, oy2 = bbox  # original-image coordinates

        from PIL import Image, ImageOps

        thumb = row["thumbnail_path"]
        orig  = row["file_path"]

        # Decide whether the thumbnail has enough resolution for this face
        use_thumb = False
        sx = sy = 1.0
        if thumb and Path(thumb).exists() and orig and Path(orig).exists():
            with Image.open(orig) as o:
                ot = ImageOps.exif_transpose(o)
                ow, oh = ot.size
            with Image.open(thumb) as t:
                tw, th = t.size
            sx, sy = tw / ow, th / oh
            face_w_thumb = (ox2 - ox1) * sx
            face_h_thumb = (oy2 - oy1) * sy
            use_thumb = face_w_thumb >= 24 and face_h_thumb >= 24

        if use_thumb:
            x1, y1, x2, y2 = ox1*sx, oy1*sy, ox2*sx, oy2*sy
            src_path = thumb
        elif orig and Path(orig).exists():
            x1, y1, x2, y2 = ox1, oy1, ox2, oy2
            src_path = orig
        else:
            return None

        with Image.open(src_path) as img:
            img = ImageOps.exif_transpose(img)
            img_w, img_h = img.size
            pad = max((x2 - x1), (y2 - y1)) * 0.4
            cx1 = max(0, int(x1 - pad))
            cy1 = max(0, int(y1 - pad))
            cx2 = min(img_w, int(x2 + pad))
            cy2 = min(img_h, int(y2 + pad))
            if cx2 <= cx1 or cy2 <= cy1:
                return None
            crop = img.crop((cx1, cy1, cx2, cy2)).convert("RGB")
            crop = crop.resize((size, size), Image.LANCZOS)

        raw = crop.tobytes("raw", "RGB")
        from PySide6.QtGui import QImage, QPixmap
        qimg = QImage(raw, size, size, size * 3, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)

    except Exception as exc:
        log.debug("get_face_crop_pixmap face_id=%d: %s", face_id, exc)
        return None


def detect_faces_batch(
    catalog_path: Path,
    progress_cb: Callable[[int, int], None] | None = None,
    force_redetect: bool = False,
) -> dict:
    """
    Enqueue face detection jobs for photos not yet processed.

    If force_redetect=True, also re-enqueues photos that only have faces
    smaller than 60 px (poor-quality detections). Existing person assignments
    are left intact — unassigned faces from re-detection will appear in the
    next 'Cluster Faces' run.
    """
    conn = get_connection(catalog_path)

    if force_redetect:
        # Only photos that HAVE face rows but none of them are >= 60 px
        rows = conn.execute("""
            SELECT DISTINCT p.id FROM photos p
            WHERE p.status = 'ok'
              AND EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = p.id)
              AND NOT EXISTS (
                  SELECT 1 FROM faces f
                  WHERE f.photo_id = p.id
                    AND f.bounding_box IS NOT NULL
                    AND (
                        CAST(json_extract(f.bounding_box,'$[2]') AS REAL)
                        - CAST(json_extract(f.bounding_box,'$[0]') AS REAL)
                    ) >= 60
              )
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT p.id FROM photos p
            WHERE p.status = 'ok'
              AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = p.id)
        """).fetchall()

    with CatalogWriter(catalog_path) as wconn:
        for row in rows:
            wconn.execute(
                "INSERT INTO jobs (job_type, payload, status) VALUES ('face_detect', ?, 'pending')",
                (json.dumps({"photo_id": row["id"], "force": force_redetect}),),
            )
    return {"enqueued": len(rows)}
