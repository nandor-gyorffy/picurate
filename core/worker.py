"""Background job worker — runs in a daemon thread, processes the jobs table."""
from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.db.catalog import CatalogWriter, get_connection
from core import hashing, thumbnails
from core.logger import get_logger

log = get_logger("picurate.worker")


class JobWorker(threading.Thread):
    """Single background worker thread that drains the jobs table."""

    def __init__(
        self,
        catalog_path: Path | None = None,
        progress_cb: Callable[[str, int, int], None] | None = None,
        result_queue: queue.Queue | None = None,
    ):
        super().__init__(daemon=True, name="picurate-worker")
        self._catalog_path = catalog_path
        self._progress_cb = progress_cb
        self._result_queue = result_queue
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    def wake(self) -> None:
        self._wake_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def run(self) -> None:
        log.info("Worker started")
        while not self._stop_event.is_set():
            try:
                self._drain()
            except Exception as exc:
                log.error("Worker error: %s", exc)
            self._wake_event.wait(timeout=5.0)
            self._wake_event.clear()
        log.info("Worker stopped")

    def _drain(self) -> None:
        conn = get_connection(self._catalog_path)
        pending = conn.execute(
            "SELECT id, job_type, payload FROM jobs WHERE status='pending' ORDER BY id LIMIT 50"
        ).fetchall()
        total = len(pending)
        for i, job in enumerate(pending):
            if self._stop_event.is_set():
                break
            self._run_job(job)
            if self._progress_cb:
                self._progress_cb(job["job_type"], i + 1, total)

    def _run_job(self, job) -> None:
        job_id = job["id"]
        job_type = job["job_type"]
        payload = json.loads(job["payload"] or "{}")
        try:
            self._mark(job_id, "running")
            if job_type == "full_hash":
                self._job_full_hash(payload)
            elif job_type == "thumbnail":
                self._job_thumbnail(payload)
            elif job_type == "face_detect":
                self._job_face_detect(payload)
            elif job_type == "clip_tag":
                self._job_clip_tag(payload)
            elif job_type == "phash":
                self._job_phash(payload)
            elif job_type == "quality":
                self._job_quality(payload)
            else:
                log.warning("Unknown job type: %s", job_type)
            self._mark(job_id, "done")
        except Exception as exc:
            log.warning("Job %s (%s) failed: %s", job_id, job_type, exc)
            self._mark(job_id, "error")

    def _mark(self, job_id: int, status: str) -> None:
        with CatalogWriter(self._catalog_path) as conn:
            conn.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                (status, datetime.now().isoformat(), job_id),
            )

    def _job_full_hash(self, payload: dict) -> None:
        path = Path(payload["path"])
        photo_id = payload["photo_id"]
        if not path.exists():
            return
        fhash = hashing.full_hash(path)
        with CatalogWriter(self._catalog_path) as conn:
            dup = conn.execute(
                "SELECT id FROM photos WHERE full_hash=? AND id!=? AND status NOT IN ('missing','duplicate')",
                (fhash, photo_id),
            ).fetchone()
            if dup:
                conn.execute(
                    "UPDATE photos SET full_hash=?, status='duplicate' WHERE id=?",
                    (fhash, photo_id),
                )
            else:
                conn.execute(
                    "UPDATE photos SET full_hash=? WHERE id=?",
                    (fhash, photo_id),
                )
            missing = conn.execute(
                "SELECT id FROM photos WHERE full_hash=? AND status='missing' AND id!=?",
                (fhash, photo_id),
            ).fetchone()
            if missing:
                conn.execute("DELETE FROM photos WHERE id=?", (missing["id"],))

    def _job_thumbnail(self, payload: dict) -> None:
        path = Path(payload["path"])
        photo_id = payload["photo_id"]
        if not path.exists():
            return
        thumb = thumbnails.get_thumbnail(path)
        if thumb:
            with CatalogWriter(self._catalog_path) as conn:
                conn.execute(
                    "UPDATE photos SET thumbnail_path=? WHERE id=?",
                    (str(thumb), photo_id),
                )
            if self._result_queue is not None:
                self._result_queue.put(("thumbnail", photo_id, str(thumb)))

    def _job_face_detect(self, payload: dict) -> None:
        from core.faces import process_photo_faces
        photo_id = payload["photo_id"]
        conn = get_connection(self._catalog_path)
        row = conn.execute("SELECT file_path FROM photos WHERE id=?", (photo_id,)).fetchone()
        if row:
            process_photo_faces(photo_id, row["file_path"], self._catalog_path)

    def _job_clip_tag(self, payload: dict) -> None:
        from core.topics import tag_photo
        photo_id = payload["photo_id"]
        path = payload.get("path", "")
        tag_photo(photo_id, path, self._catalog_path)

    def _job_phash(self, payload: dict) -> None:
        from core.duplicates import compute_phash
        photo_id = payload["photo_id"]
        path = payload.get("path", "")
        h = compute_phash(path)
        if h is not None:
            with CatalogWriter(self._catalog_path) as conn:
                conn.execute("UPDATE photos SET phash=? WHERE id=?", (h, photo_id))

    def _job_quality(self, payload: dict) -> None:
        from core.quality import compute_quality_components
        photo_id = payload["photo_id"]
        path = payload.get("path", "")
        result = compute_quality_components(path)
        if result is not None:
            quality, sharpness, exposure = result
            with CatalogWriter(self._catalog_path) as conn:
                conn.execute(
                    "UPDATE photos SET quality_score=?, sharpness_score=?, exposure_score=? WHERE id=?",
                    (quality, sharpness, exposure, photo_id),
                )
