"""GPS map view using QWebEngineView + embedded Leaflet.js.

Shows all photos with GPS coordinates as markers on an interactive map.
Markers are clustered visually; clicking one shows the thumbnail and filename.
"""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)
from core.db.catalog import get_connection
from core.logger import get_logger

log = get_logger("picurate.mapview")


def _build_html(markers: list[dict]) -> str:
    """Return a self-contained HTML page with Leaflet.js map and markers."""
    marker_js = []
    for m in markers:
        lat = m["lat"]
        lon = m["lon"]
        fname = m["filename"].replace("'", "\'")
        thumb = m.get("thumbnail_path", "") or ""
        thumb = thumb.replace("\\", "/").replace("'", "\'")
        rating = m.get("rating") or 0
        stars = "\u2605" * rating
        popup = f"{fname}<br>{stars}"
        if thumb:
            popup = f"<img src='file:///{thumb}' width=120 style='max-height:90px;object-fit:cover'><br>{fname}<br>{stars}"
        marker_js.append(
            f"L.marker([{lat},{lon}]).addTo(markers).bindPopup(\"{popup}\")"
        )
    markers_code = ";\n".join(marker_js)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Picurate Map</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css" crossorigin=""/>
<link rel="stylesheet" href="https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css" crossorigin=""/>
<style>
  body {{ margin:0; padding:0; background:#1a1a1a; }}
  #map {{ height: 100vh; width: 100vw; }}
  #info {{ position:fixed; top:10px; left:50%; transform:translateX(-50%);
           background:rgba(0,0,0,0.7); color:#fff; padding:6px 14px;
           border-radius:20px; font-family:sans-serif; font-size:13px; z-index:9999; }}
</style>
</head>
<body>
<div id="info">{len(markers)} photos with GPS</div>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV/XN/WLs=" crossorigin=""></script>
<script src="https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js" crossorigin=""></script>
<script>
var map = L.map("map").setView([20, 0], 2);
L.tileLayer("https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
    attribution: "\u00a9 OpenStreetMap contributors",
    maxZoom: 19
}}).addTo(map);
var markers = L.markerClusterGroup();
{markers_code}
map.addLayer(markers);
if ({len(markers)} > 0) {{
    map.fitBounds(markers.getBounds().pad(0.1));
}}
</script>
</body>
</html>"""


class MapView(QDialog):
    """Modal map dialog showing all GPS-tagged photos."""

    def __init__(self, catalog_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Places Map")
        self.resize(1000, 680)
        self._catalog_path = catalog_path
        self._build_ui()
        self._load_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top = QWidget()
        top.setFixedHeight(36)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(8, 4, 8, 4)
        self._count_label = QLabel("Loading GPS data…")
        tl.addWidget(self._count_label)
        tl.addStretch()
        note = QLabel("Markers require internet (OpenStreetMap tiles). Clusters collapse nearby photos.")
        note.setStyleSheet("color: #888; font-size: 11px;")
        tl.addWidget(note)
        tl.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        tl.addWidget(close_btn)
        layout.addWidget(top)

        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            self._web = QWebEngineView()
            layout.addWidget(self._web, stretch=1)
            self._has_web = True
        except ImportError:
            fallback = QLabel(
                "Map view requires PySide6-WebEngine.\n"
                "Install: pip install PySide6-WebEngine"
            )
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setStyleSheet("color: #888; font-size: 14px;")
            layout.addWidget(fallback, stretch=1)
            self._has_web = False

    def _load_data(self):
        conn = get_connection(self._catalog_path)
        rows = conn.execute(
            "SELECT id, filename, gps_lat, gps_lon, thumbnail_path, rating "
            "FROM photos WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL "
            "ORDER BY taken_at"
        ).fetchall()
        markers = [
            {
                "lat": r["gps_lat"],
                "lon": r["gps_lon"],
                "filename": r["filename"] or f"photo_{r['id']}",
                "thumbnail_path": r["thumbnail_path"] or "",
                "rating": r["rating"] or 0,
            }
            for r in rows
        ]
        self._count_label.setText(f"{len(markers)} photos with GPS coordinates")
        if not self._has_web:
            return
        if not markers:
            self._web.setHtml(
                "<html><body style='background:#1a1a1a;color:#888;"
                "display:flex;align-items:center;justify-content:center;"
                "height:100vh;font-family:sans-serif;font-size:16px'>"
                "No photos with GPS coordinates found.</body></html>"
            )
            return
        html = _build_html(markers)
        self._web.setHtml(html, QUrl("about:blank"))
        log.info("Map loaded %d GPS markers", len(markers))
