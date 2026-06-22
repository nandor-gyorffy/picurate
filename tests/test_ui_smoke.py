"""UI smoke tests — basic widget creation and interaction without a real window.

All tests run with QT_QPA_PLATFORM=offscreen so no display is required.
"""
import os
import sys
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    yield app


@pytest.fixture()
def catalog(tmp_path):
    from core.db.catalog import open_catalog
    p = tmp_path / "catalog.db"
    open_catalog(p).close()
    return p


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class TestMainWindow:
    def test_opens_without_error(self, qapp, catalog):
        from ui.mainwindow import MainWindow
        win = MainWindow()
        win.show()
        assert win.isVisible()
        win.close()

    def test_menubar_has_required_menus(self, qapp):
        from ui.mainwindow import MainWindow
        win = MainWindow()
        mb = win.menuBar()
        titles = [mb.actions()[i].text() for i in range(mb.actions().__len__())]
        for expected in ("&File", "&View", "aces", "&Places", "&Library"):
            assert any(expected in t for t in titles), f"Missing menu: {expected}"
        win.close()

    def test_cull_action_exists(self, qapp):
        from ui.mainwindow import MainWindow
        win = MainWindow()
        assert hasattr(win, "_act_cull")
        assert win._act_cull.isCheckable()
        win.close()


# ---------------------------------------------------------------------------
# CullView
# ---------------------------------------------------------------------------

class TestCullView:
    def test_creates_with_empty_catalog(self, qapp, catalog):
        from ui.cullview import CullView
        cv = CullView(catalog, {})
        cv.show()
        assert cv.isVisible()
        cv.close()

    def test_navigation_with_no_photos(self, qapp, catalog):
        from ui.cullview import CullView
        cv = CullView(catalog, {})
        cv.go_next()   # should not raise
        cv.go_prev()   # should not raise
        cv.close()

    def test_similar_panel_present(self, qapp, catalog):
        from ui.cullview import CullView
        cv = CullView(catalog, {})
        assert hasattr(cv, "_similar_panel")
        cv.close()

    def test_fullscreen_toggle(self, qapp, catalog):
        from ui.cullview import CullView
        cv = CullView(catalog, {})
        cv.show()
        cv._toggle_fullscreen()
        assert cv._fullscreen is True
        cv._toggle_fullscreen()
        assert cv._fullscreen is False
        cv.close()


# ---------------------------------------------------------------------------
# MapView
# ---------------------------------------------------------------------------

class TestMapView:
    def test_opens_empty_catalog(self, qapp, catalog):
        from ui.mapview import MapView
        dlg = MapView(catalog)
        dlg.show()
        assert "0 photos" in dlg._count_label.text() or "GPS" in dlg._count_label.text()
        dlg.close()

    def test_html_generation(self):
        from ui.mapview import _build_html
        markers = [
            {"lat": 41.89, "lon": 12.49, "filename": "rome.jpg", "thumbnail_path": "", "rating": 3},
            {"lat": 48.85, "lon": 2.35,  "filename": "paris.jpg", "thumbnail_path": "", "rating": 5},
        ]
        html = _build_html(markers)
        assert "Colosseum" not in html   # marker text is filename, not landmark name
        assert "rome.jpg" in html
        assert "paris.jpg" in html
        assert "L.markerClusterGroup" in html
        assert "41.89" in html


# ---------------------------------------------------------------------------
# ThumbGrid
# ---------------------------------------------------------------------------

class TestThumbGrid:
    def test_creates(self, qapp, catalog):
        from ui.thumbgrid import ThumbnailGrid as ThumbGrid
        tg = ThumbGrid(catalog)
        tg.show()
        assert tg.isVisible()
        tg.close()

    def test_load_photos_empty(self, qapp, catalog):
        from ui.thumbgrid import ThumbnailGrid as ThumbGrid
        tg = ThumbGrid(catalog)
        tg.load_photos({})   # should not raise, just show empty grid
        tg.close()


# ---------------------------------------------------------------------------
# SimilarPanel (unit-level)
# ---------------------------------------------------------------------------

class TestSimilarPanel:
    def test_set_searching_clears_list(self, qapp, catalog):
        from ui.cullview import _SimilarPanel
        sp = _SimilarPanel(catalog)
        sp.set_searching()
        assert sp._list.count() == 0
        assert not sp._spinner.isHidden()   # spinner shown (isVisible needs parent shown)

    def test_populate_empty_results(self, qapp, catalog):
        from ui.cullview import _SimilarPanel
        sp = _SimilarPanel(catalog)
        sp.populate([], current_photo_id=1)
        assert sp._list.count() == 0
        assert sp._spinner.isHidden()       # spinner hidden after search
        assert not sp._empty.isHidden()     # empty label shown


# ---------------------------------------------------------------------------
# Topics DEFAULT_LABELS
# ---------------------------------------------------------------------------

class TestTopicLabels:
    def test_landmark_labels_present(self):
        from core.topics import DEFAULT_LABELS
        landmarks = ["Colosseum", "Eiffel Tower", "Taj Mahal", "Angkor Wat"]
        for lm in landmarks:
            assert lm in DEFAULT_LABELS, f"Missing landmark: {lm}"

    def test_no_duplicate_labels(self):
        from core.topics import DEFAULT_LABELS
        assert len(DEFAULT_LABELS) == len(set(DEFAULT_LABELS))
