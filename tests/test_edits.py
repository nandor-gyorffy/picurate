"""Tests for non-destructive photo edits (core/edits.py + schema v5)."""
from __future__ import annotations
import pytest
from pathlib import Path
from PIL import Image
import numpy as np


@pytest.fixture()
def catalog(tmp_path):
    from core.db.catalog import open_catalog
    p = tmp_path / "catalog.db"
    open_catalog(p).close()
    return p


@pytest.fixture()
def photo_id(catalog):
    from core.db.catalog import CatalogWriter
    with CatalogWriter(catalog) as conn:
        conn.execute(
            "INSERT INTO photos(file_path, filename, status) VALUES (?,?,?)",
            ("/tmp/test.jpg", "test.jpg", "ok"),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ── Schema ────────────────────────────────────────────────────────────────────

class TestPhotoEditsSchema:
    def test_table_exists(self, catalog):
        from core.db.catalog import get_connection
        conn = get_connection(catalog)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='photo_edits'"
        ).fetchone()
        assert row is not None

    def test_columns_exist(self, catalog):
        from core.db.catalog import get_connection
        conn = get_connection(catalog)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photo_edits)")}
        for col in ("photo_id", "crop_x", "crop_y", "crop_w", "crop_h",
                    "rotate", "brightness", "contrast", "saturation"):
            assert col in cols


# ── CRUD ─────────────────────────────────────────────────────────────────────

class TestEditCRUD:
    def test_get_edit_returns_none_when_no_row(self, catalog, photo_id):
        from core.edits import get_edit
        assert get_edit(photo_id, catalog) is None

    def test_set_edit_creates_row(self, catalog, photo_id):
        from core.edits import set_edit, get_edit
        set_edit(photo_id, catalog, rotate=90, brightness=0.2)
        edit = get_edit(photo_id, catalog)
        assert edit is not None
        assert edit["rotate"] == 90
        assert abs(edit["brightness"] - 0.2) < 1e-6

    def test_set_edit_updates_partial_fields(self, catalog, photo_id):
        from core.edits import set_edit, get_edit
        set_edit(photo_id, catalog, contrast=0.5)
        set_edit(photo_id, catalog, brightness=0.3)
        edit = get_edit(photo_id, catalog)
        assert abs(edit["contrast"] - 0.5) < 1e-6
        assert abs(edit["brightness"] - 0.3) < 1e-6

    def test_clear_edit_removes_row(self, catalog, photo_id):
        from core.edits import set_edit, clear_edit, get_edit
        set_edit(photo_id, catalog, rotate=180)
        clear_edit(photo_id, catalog)
        assert get_edit(photo_id, catalog) is None

    def test_has_edit_false_when_none(self, catalog, photo_id):
        from core.edits import has_edit
        assert not has_edit(photo_id, catalog)

    def test_has_edit_true_after_set(self, catalog, photo_id):
        from core.edits import set_edit, has_edit
        set_edit(photo_id, catalog, rotate=90)
        assert has_edit(photo_id, catalog)

    def test_has_edit_false_after_clear(self, catalog, photo_id):
        from core.edits import set_edit, clear_edit, has_edit
        set_edit(photo_id, catalog, brightness=0.5)
        clear_edit(photo_id, catalog)
        assert not has_edit(photo_id, catalog)

    def test_crop_defaults(self, catalog, photo_id):
        from core.edits import set_edit, get_edit
        set_edit(photo_id, catalog, rotate=90)  # only set rotate
        edit = get_edit(photo_id, catalog)
        assert edit["crop_x"] == 0.0
        assert edit["crop_y"] == 0.0
        assert edit["crop_w"] == 1.0
        assert edit["crop_h"] == 1.0


# ── apply_edit_to_image ───────────────────────────────────────────────────────

def _make_img(w=100, h=60, color=(128, 200, 50)):
    arr = np.full((h, w, 3), color, dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


class TestApplyEditToImage:
    def test_none_edit_returns_same_image(self):
        from core.edits import apply_edit_to_image
        img = _make_img(100, 60)
        result = apply_edit_to_image(img, None)
        assert result.size == (100, 60)

    def test_identity_edit_unchanged(self):
        from core.edits import apply_edit_to_image
        edit = {"crop_x": 0.0, "crop_y": 0.0, "crop_w": 1.0, "crop_h": 1.0,
                "rotate": 0, "brightness": 0.0, "contrast": 0.0, "saturation": 0.0}
        img = _make_img(100, 60)
        result = apply_edit_to_image(img, edit)
        assert result.size == (100, 60)

    def test_rotate_90_swaps_dimensions(self):
        from core.edits import apply_edit_to_image
        edit = {"crop_x": 0.0, "crop_y": 0.0, "crop_w": 1.0, "crop_h": 1.0,
                "rotate": 90, "brightness": 0.0, "contrast": 0.0, "saturation": 0.0}
        img = _make_img(100, 60)
        result = apply_edit_to_image(img, edit)
        assert result.size == (60, 100)

    def test_rotate_180_preserves_dimensions(self):
        from core.edits import apply_edit_to_image
        edit = {"crop_x": 0.0, "crop_y": 0.0, "crop_w": 1.0, "crop_h": 1.0,
                "rotate": 180, "brightness": 0.0, "contrast": 0.0, "saturation": 0.0}
        img = _make_img(100, 60)
        result = apply_edit_to_image(img, edit)
        assert result.size == (100, 60)

    def test_crop_reduces_size(self):
        from core.edits import apply_edit_to_image
        edit = {"crop_x": 0.1, "crop_y": 0.1, "crop_w": 0.5, "crop_h": 0.5,
                "rotate": 0, "brightness": 0.0, "contrast": 0.0, "saturation": 0.0}
        img = _make_img(100, 100)
        result = apply_edit_to_image(img, edit)
        assert result.width < 100
        assert result.height < 100

    def test_brightness_increase_raises_pixel_value(self):
        from core.edits import apply_edit_to_image
        edit = {"crop_x": 0.0, "crop_y": 0.0, "crop_w": 1.0, "crop_h": 1.0,
                "rotate": 0, "brightness": 0.5, "contrast": 0.0, "saturation": 0.0}
        img = _make_img(10, 10, color=(100, 100, 100))
        result = apply_edit_to_image(img, edit)
        pixel = result.getpixel((5, 5))
        assert pixel[0] > 100

    def test_cascade_delete_on_photo_delete(self, catalog, photo_id):
        from core.edits import set_edit, get_edit
        from core.db.catalog import CatalogWriter
        set_edit(photo_id, catalog, rotate=90)
        assert get_edit(photo_id, catalog) is not None
        with CatalogWriter(catalog) as conn:
            conn.execute("DELETE FROM photos WHERE id=?", (photo_id,))
        assert get_edit(photo_id, catalog) is None
