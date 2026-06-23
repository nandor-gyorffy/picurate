# Picurate

A local, private desktop photo organizer for **Windows 10/11** and **Ubuntu 22.04+**,
inspired by Picasa.

Sort your photos by **people**, **places**, and **topics**. Rate and curate collections.
Export self-contained HTML galleries. All processing is 100% local — no cloud, no
subscriptions, no data ever leaves your machine.

![Picurate screenshot](assets/icon/picurate_256.png)

## Features

- **Photo grid** — browse with adjustable thumbnails, filter bar, and sidebar
- **Cull Mode** — rapid photo review with keyboard shortcuts, always-visible similarity panel, side-by-side compare
- **Face recognition** — InsightFace (RetinaFace + ArcFace) detects and clusters faces; rename people, filter by person
- **GPS map** — interactive Leaflet.js map of all GPS-tagged photos; offline reverse-geocoding; automatic trip grouping
- **Topic tagging** — CLIP zero-shot AI tagging (80+ categories including landmarks); filter by topic
- **Collections** — hand-pick "best of" sets; export as folder copy or HTML gallery
- **Non-destructive edits** — crop, rotate, brightness/contrast/saturation stored in catalog; originals never touched
- **Import** — reads Picasa `.ini` files, embedded XMP/IPTC, folder structure
- **Export** — copy picks to folder, contact sheet, self-contained HTML gallery

## Quick Start

### Linux (Ubuntu 22.04+)

```bash
git clone https://github.com/nandor-gyorffy/picurate.git
cd picurate
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

To add a desktop launcher (appears in application menu):

```bash
./install_launcher.sh
```

### Windows 10/11

```bat
git clone https://github.com/nandor-gyorffy/picurate.git
cd picurate
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
run.bat
```

Or double-click `run.bat` from Explorer after creating the venv and installing deps.

## Optional ML Components

| Component | Size | How to install |
|-----------|------|----------------|
| Face detection (InsightFace buffalo_sc) | ~170 MB | Downloaded automatically on first **Faces → Detect Faces** run |
| Face recognition (InsightFace buffalo_l) | ~500 MB | Switch in Settings → Face Recognition; downloads automatically |
| Topic tagging (CLIP) | ~300 MB | Place ONNX files in data dir — see **Library → Download CLIP Models…** |
| Metadata write-back | — | Install [exiftool](https://exiftool.org) and add to PATH |

The app works without any of these — you can add them later.

## Requirements

- Python 3.12+
- See `requirements.txt` for Python packages

## Project Structure

```
picurate/
├── main.py              # entry point
├── core/                # all business logic (no UI imports)
│   ├── db/              # SQLite schema + migrations
│   ├── faces.py         # InsightFace integration
│   ├── topics.py        # CLIP tagging
│   ├── places.py        # GPS geocoding + trips
│   ├── similar.py       # similarity search (pHash + CLIP)
│   ├── edits.py         # non-destructive edit storage
│   └── ...
├── ui/                  # PySide6 windows and widgets
│   ├── mainwindow.py    # main three-pane layout
│   ├── cullview.py      # cull/review mode
│   ├── face_gallery.py  # people management dialog
│   ├── mapview.py       # GPS map (Leaflet.js)
│   └── ...
├── tests/               # pytest headless test suite
└── assets/icon/         # application icons (PNG + ICO + SVG)
```

## Architecture Decisions

- **Non-destructive**: catalog only; originals never written
- **Identity by content hash**: moved/renamed files re-link, not duplicate
- **Single-writer SQLite** in WAL mode with auto-backup
- **Background job queue**: all ML/indexing off the UI thread
- **Cross-platform paths**: `platformdirs`, volume UUID not drive letters

## Development

```bash
source .venv/bin/activate
pytest tests/          # run headless test suite (~411 tests)
```

## License

MIT — see [LICENSE](LICENSE)
