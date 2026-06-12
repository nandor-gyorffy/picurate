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


def detect_faces(file_path: str) -> list[dict]:
    """
    Run face detection + embedding on one photo file.

    Returns list of dicts: {bbox: [x1,y1,x2,y2], embedding: list[float]}.
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
            bbox = face.bbox.tolist() if hasattr(face.bbox, "tolist") else list(face.bbox)
            emb  = face.embedding.tolist() if hasattr(face.embedding, "tolist") else list(face.embedding)
            result.append({"bbox": bbox, "embedding": emb})
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
                """INSERT INTO faces (photo_id, bounding_box, embedding, source)
                   VALUES (?,?,?,?)""",
                (
                    photo_id,
                    json.dumps(face["bbox"]),
                    json.dumps(face["embedding"]),
                    "insightface",
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


def detect_faces_batch(
    catalog_path: Path,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
    """
    Enqueue face detection jobs for all photos that haven't been processed.
    Actual processing happens in the job worker.
    Returns stats: {enqueued}.
    """
    conn = get_connection(catalog_path)
    # Photos with no face rows and status=ok
    rows = conn.execute("""
        SELECT p.id FROM photos p
        WHERE p.status = 'ok'
          AND NOT EXISTS (SELECT 1 FROM faces f WHERE f.photo_id = p.id)
    """).fetchall()

    with CatalogWriter(catalog_path) as wconn:
        for row in rows:
            wconn.execute(
                "INSERT INTO jobs (job_type, payload, status) VALUES ('face_detect', ?, 'pending')",
                (json.dumps({"photo_id": row["id"]}),),
            )
    return {"enqueued": len(rows)}
