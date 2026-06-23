"""Picurate in-app help / user guide dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QTabWidget, QTextBrowser, QVBoxLayout,
)


def _tab(title: str, html: str) -> tuple[str, str]:
    return title, html


_TABS = [
    _tab("Quick Start", """
<h2>Getting Started with Picurate</h2>
<p>Picurate is a local, private photo organizer. Nothing is ever moved, renamed,
or modified — your original photos stay exactly where they are.</p>

<h3>1. Open your photo folder</h3>
<p>Go to <b>File → Open Folder</b> (Ctrl+O) and select the folder (or drive)
where your photos live. Picurate starts indexing immediately in the background —
the status bar shows progress.</p>

<h3>2. Browse your photos</h3>
<p>The grid view shows thumbnails. Use the left sidebar to filter by folder,
year, collection, person, place, or topic. The filter bar at the top lets you
search by filename, rating, flag, or keyword.</p>

<h3>3. Rate and pick photos</h3>
<p>Click a photo to select it. Use keys <b>1–5</b> to rate, <b>P</b> to pick
(green flag), <b>X</b> to reject (red flag), or <b>U</b> to clear the flag.
Double-click to open the full-size loupe view.</p>

<h3>4. Enter Cull Mode for rapid review</h3>
<p>Press <b>Ctrl+K</b> or the toolbar button. Navigate with arrow keys or Space.
The right panel automatically shows similar photos. Press <b>F</b> for fullscreen.</p>

<h3>5. Organize into collections</h3>
<p>Press <b>C</b> (in loupe or cull mode) or right-click a photo in the grid to
add it to a collection. Collections appear in the sidebar.</p>

<h3>6. Export your selections</h3>
<p>Use <b>File → Export…</b> to copy picks to a folder, create a printable
contact sheet, or generate a self-contained HTML gallery for a USB stick.</p>
"""),

    _tab("Keyboard Shortcuts", """
<h2>Keyboard Shortcuts</h2>

<h3>Global</h3>
<table cellspacing="8">
<tr><td><b>Ctrl+O</b></td><td>Open folder</td></tr>
<tr><td><b>F5</b></td><td>Rescan watch folders</td></tr>
<tr><td><b>Ctrl+K</b></td><td>Toggle Cull Mode</td></tr>
<tr><td><b>Ctrl+F</b></td><td>Toggle filter bar</td></tr>
<tr><td><b>Ctrl+E</b></td><td>Export dialog</td></tr>
<tr><td><b>Ctrl+I</b></td><td>Import dialog</td></tr>
<tr><td><b>Ctrl+,</b></td><td>Settings</td></tr>
<tr><td><b>Ctrl+Q</b></td><td>Quit</td></tr>
</table>

<h3>Cull Mode &amp; Loupe</h3>
<table cellspacing="8">
<tr><td><b>← →</b></td><td>Previous / next photo</td></tr>
<tr><td><b>Space</b></td><td>Next photo (cull mode)</td></tr>
<tr><td><b>1 – 5</b></td><td>Set star rating</td></tr>
<tr><td><b>0</b></td><td>Clear rating</td></tr>
<tr><td><b>P</b></td><td>Pick (green flag)</td></tr>
<tr><td><b>X</b></td><td>Reject (red flag)</td></tr>
<tr><td><b>U</b></td><td>Unflag</td></tr>
<tr><td><b>C</b></td><td>Add to collection</td></tr>
<tr><td><b>F / F11</b></td><td>Toggle fullscreen</td></tr>
<tr><td><b>+ / −</b></td><td>Zoom in / out (loupe)</td></tr>
<tr><td><b>Esc</b></td><td>Exit compare / exit fullscreen / close</td></tr>
</table>

<h3>Grid View</h3>
<table cellspacing="8">
<tr><td><b>1 – 5</b></td><td>Set rating on selected photo</td></tr>
<tr><td><b>P / X / U</b></td><td>Set flag on selected photo</td></tr>
<tr><td><b>Enter / Double-click</b></td><td>Open in loupe</td></tr>
<tr><td><b>Right-click</b></td><td>Context menu (rate, flag, collection)</td></tr>
</table>

<h3>Cull Mode — Similar Panel</h3>
<table cellspacing="8">
<tr><td><b>Double-click</b> a similar photo</td><td>Compare side-by-side</td></tr>
<tr><td><b>Right-click</b> a similar photo</td><td>Options menu</td></tr>
</table>
"""),

    _tab("People", """
<h2>Face Recognition — People</h2>
<p>Picurate uses <b>InsightFace</b> (RetinaFace + ArcFace) for 100% local,
offline face detection and recognition. No cloud API, no data leaves your machine.</p>

<h3>Step-by-step workflow</h3>
<ol>
<li><b>Detect Faces</b> — <i>Faces → Detect Faces</i><br>
Scans all indexed photos and detects face regions. Queued as a background job;
progress shows in the status bar. The first run downloads the model (~170 MB for
buffalo_sc).</li>

<li><b>Cluster Faces</b> — <i>Faces → Cluster Faces</i><br>
Groups similar face embeddings into people using average-linkage clustering.
New people get names like "Person 1", "Person 2", etc.</li>

<li><b>Rename people</b> — <i>Faces → People Gallery…</i><br>
Review all recognized people. Click <b>Rename</b> to give them real names.
Right-click a face thumbnail to reassign it or delete it.</li>

<li><b>Filter by person</b><br>
Click a person's name in the left sidebar to see all their photos.</li>
</ol>

<h3>Managing faces</h3>
<ul>
<li><b>People Gallery</b> (Faces menu) — overview of all people with face strips</li>
<li><b>Unassigned Faces</b> — review faces not yet linked to a person</li>
<li><b>Re-cluster Faces</b> — reset auto-generated groups and re-cluster from scratch
    (manually named people are preserved)</li>
<li><b>Re-detect Faces</b> — re-run detection on photos with only small/distant faces</li>
</ul>

<h3>Face model choice</h3>
<p>In <b>Settings</b> you can switch between:</p>
<ul>
<li><b>buffalo_sc</b> — fast, compact (~170 MB). Good for most use cases.</li>
<li><b>buffalo_l</b> — higher accuracy (~500 MB). Better for large libraries or
difficult lighting. Downloads automatically on first use after switching.</li>
</ul>

<h3>Tips</h3>
<ul>
<li>The face strip in the Properties panel shows all faces in the current photo.</li>
<li>Hover a face thumbnail for the person's name.</li>
<li>After renaming, click <b>Refresh</b> in the sidebar to update the People list.</li>
</ul>
"""),

    _tab("Places", """
<h2>Places &amp; GPS Map</h2>
<p>Picurate reads GPS coordinates embedded in your photos (by phones and GPS-enabled
cameras) and organizes them geographically — all offline.</p>

<h3>Geocoding</h3>
<p>Go to <b>Places → Geocode GPS</b> to reverse-geocode all photos with GPS data.
This converts coordinates to city/region/country names using the offline
<i>reverse_geocoder</i> library — no internet required.</p>

<h3>Map view</h3>
<p>Go to <b>Places → Places Map…</b> to open an interactive map showing all
GPS-tagged photos as clustered markers. Click a marker to see the thumbnail and
filename. <em>Requires internet for the OpenStreetMap tile layer.</em></p>

<h3>Trips</h3>
<p><b>Places → Group Trips</b> automatically groups photos into trips based on
date gaps (default: 6-hour gap = new trip). Trips appear in the sidebar
under "Trips". Click a trip to see all photos from that journey.</p>

<h3>Merge nearby places</h3>
<p><b>Places → Merge Nearby Places</b> consolidates place records that are within
500 m of each other — useful after geocoding a large library where slight
coordinate differences create duplicate places.</p>

<h3>Browsing by place</h3>
<p>After geocoding, the sidebar shows a <b>Places</b> tree. Click any city or
country to filter the grid to photos from that location.</p>

<h3>Manual place assignment</h3>
<p>Right-click a photo in the grid → "Set Place…" to manually assign a location
to photos that have no GPS data.</p>
"""),

    _tab("Topics", """
<h2>Topic Tagging (CLIP)</h2>
<p>Picurate can automatically tag your photos with topics like "beach", "mountains",
"portrait", "food", "architecture", "Eiffel Tower", and 80+ other categories using
<b>CLIP</b> (Contrastive Language–Image Pretraining) — entirely locally.</p>

<h3>Setup</h3>
<p>CLIP requires ONNX model files placed in the correct folder. Go to
<b>Library → Download CLIP Models…</b> for the exact path and file list.</p>

<h3>Running tagging</h3>
<p>Go to <b>Library → Tag Topics</b> to enqueue CLIP tagging for all untagged photos.
Jobs run in the background. Tagged photos get keyword labels that appear in the
Properties panel and the sidebar Topics tree.</p>

<h3>Automatic tagging after scan</h3>
<p>When you open a new folder, tagging is automatically queued for new photos —
you don't need to trigger it manually.</p>

<h3>Browsing by topic</h3>
<p>After tagging, the sidebar shows a <b>Topics</b> tree. Click any topic to
filter the grid to photos with that label.</p>

<h3>Supported topic categories</h3>
<p>People &amp; portraits, nature (mountains, beach, forest, sunset, snow…),
architecture (buildings, interior, street…), food &amp; drink, vehicles,
activities (hiking, sports, yoga…), animals, night scenes, famous landmarks
(Eiffel Tower, Colosseum, Taj Mahal, Sagrada Familia, Angkor Wat…), and more.</p>

<h3>Similarity search</h3>
<p>In Cull Mode, the right-hand panel uses CLIP embeddings (when available) to
find visually similar photos across your entire library — not just by filename or
metadata, but by actual visual content.</p>
"""),

    _tab("Collections", """
<h2>Collections</h2>
<p>Collections are curated sets of photos you hand-pick — like albums or projects.</p>

<h3>Creating a collection</h3>
<p>Right-click anywhere in the sidebar's Collections section → <b>New Collection…</b>,
or use the right-click menu on any photo in the grid.</p>

<h3>Adding photos</h3>
<ul>
<li>Press <b>C</b> in cull mode or loupe view to add the current photo.</li>
<li>Right-click a photo in the grid → <b>Add to Collection…</b></li>
</ul>

<h3>Browsing a collection</h3>
<p>Click a collection in the sidebar to filter the grid to its photos.</p>

<h3>Exporting</h3>
<p>Go to <b>File → Export…</b> and choose a collection to export. Options:</p>
<ul>
<li><b>Copy to folder</b> — copies picked/collected photos to a destination</li>
<li><b>HTML gallery</b> — generates a self-contained webpage with thumbnails,
    perfect for a USB stick or sharing without internet</li>
<li><b>Contact sheet</b> — creates a printable PDF grid of photos</li>
</ul>
<p>Non-destructive edits (crop, rotation, adjustments) are applied to the exported
copies — your originals are never changed.</p>
"""),

    _tab("Edits & Export", """
<h2>Non-Destructive Editing</h2>
<p>All edits are stored in the catalog, never written to your original photos.
The original file is always preserved exactly as it was.</p>

<h3>Opening the edit panel</h3>
<p>Click <b>✏ Edit</b> in the bottom bar of Cull Mode or the Loupe view.</p>

<h3>What you can do</h3>
<ul>
<li><b>Crop</b> — drag the handles to define the crop rectangle, or draw a new one</li>
<li><b>Rotate</b> — rotate 90° clockwise/anticlockwise or by custom angle</li>
<li><b>Brightness / Contrast / Saturation</b> — sliders from −100% to +100%</li>
</ul>

<h3>Applying and resetting</h3>
<p>Click <b>Apply</b> to save the edit. Click <b>Reset All</b> to remove all edits
for this photo and revert to the original. Edits can be changed any number of times.</p>

<h3>Where edits appear</h3>
<p>Edits are applied when you <b>export</b> photos — exported copies reflect
the crops and adjustments. The main grid thumbnail always shows the original
so you can always compare before/after.</p>

<h3>Metadata write-back</h3>
<p><b>Faces → Write Metadata</b> uses exiftool to mirror ratings, captions,
and keywords back into the XMP sidecar or embedded metadata of your photos —
making them readable by other apps (Lightroom, digiKam, etc.). This requires
exiftool to be installed and on your PATH.</p>
"""),

    _tab("Settings", """
<h2>Settings</h2>
<p>Open with <b>Ctrl+,</b> or <b>File → Settings…</b></p>

<h3>Appearance</h3>
<ul>
<li><b>Font size</b> — global UI font size. Takes effect immediately.</li>
<li><b>Default thumbnail size</b> — size of thumbnails in the grid view (64–384 px).
    The grid automatically adjusts; you can also resize with the slider
    at the bottom-right of the grid.</li>
</ul>

<h3>Photo Similarity (Cull Mode)</h3>
<ul>
<li><b>pHash distance limit</b> — maximum Hamming distance for near-duplicate
    detection (lower = stricter, fewer results). Range: 1–30.</li>
<li><b>CLIP min score</b> — minimum cosine similarity to show a CLIP match
    (higher = stricter, fewer results). Range: 0.30–0.95.</li>
</ul>
<p>Changes take effect on the next photo navigation — no restart needed.</p>

<h3>Face Recognition</h3>
<ul>
<li><b>Model</b> — choose buffalo_sc (fast) or buffalo_l (more accurate).
    Changing the model resets the cached instance; the next face operation
    downloads and loads the new model.</li>
<li><b>Clustering threshold</b> — cosine similarity threshold for grouping faces
    into the same person (higher = fewer, stricter groups). Adjust if people
    are being split into too many or too few groups, then re-cluster.</li>
</ul>

<h3>Where data is stored</h3>
<p>The catalog database and thumbnails live in your user data directory
(determined by platformdirs). On Linux: <code>~/.local/share/picurate/</code>.
On Windows: <code>%LOCALAPPDATA%\\picurate\\</code>. You can back this up to
preserve your ratings, collections, and face assignments.</p>
"""),
]


class HelpDialog(QDialog):
    """Tabbed help / user-guide dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Picurate — Help")
        self.resize(720, 560)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 8)

        header = QLabel("Picurate User Guide")
        f = QFont()
        f.setBold(True)
        f.setPointSize(14)
        header.setFont(f)
        layout.addWidget(header)

        tabs = QTabWidget()
        for title, html in _TABS:
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHtml(f"<style>body{{font-family:sans-serif;font-size:13px;line-height:1.5}}"
                            f"h2{{margin-top:4px}}h3{{margin-top:12px;color:#6af}}"
                            f"table{{border-collapse:collapse}}td{{padding:2px 12px 2px 0}}"
                            f"code{{background:#333;padding:1px 4px;border-radius:3px}}</style>"
                            + html)
            tabs.addTab(browser, title)

        layout.addWidget(tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
