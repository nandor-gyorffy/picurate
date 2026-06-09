# CLAUDE.md — Picurate

This file orients Claude Code for this project. At the start of every session, read it together with `photo-organizer-plan.md`. **Before writing any code, also read Part II (Engineering specifics) of the plan — it pins down the cross-platform and architecture decisions that prevent avoidable bugs.**

## What we're building
**Picurate** is a local, private **desktop** photo organizer for **Windows 10/11 + Ubuntu 22.04+**, in the spirit of Picasa: sort by **people / places / topics**, review folders and **pick photos into collections**, curate **"best of" collections**, **export** collections (incl. a self-contained HTML gallery for a USB stick), and **import** existing organization (Picasa, embedded XMP/IPTC, folder structure). The full spec, engineering details, and staged build plan live in `photo-organizer-plan.md` — that document is the source of truth; this file is the quick brief.

## Locked decisions (don't change without asking)
- **Language:** Python 3.12
- **GUI:** PySide6 (Qt), with high-DPI scaling enabled
- **Database:** SQLite in **WAL mode**, **single-writer** access pattern (optional SQLCipher later)
- **ML runtime:** ONNX Runtime — **CPU by default**, GPU opt-in/auto-detected; faces = InsightFace (RetinaFace + ArcFace), topics = CLIP (+ optional Places365)
- **Images:** Pillow + pillow-heif + rawpy
- **Metadata read/write:** **exiftool** (bundled binary) for XMP/IPTC and face regions
- **Paths/dirs:** `pathlib` + `platformdirs` (never hardcode locations)
- **Reverse geocoding:** reverse_geocoder (offline)
- All processing is **local**: no cloud, no external AI APIs.

## Non-negotiable principles
1. **Non-destructive.** NEVER move, rename, or modify the user's original photo files. The app only reads them; everything learned goes in its own catalog. (The opt-in "metadata mirror" writes only standard XMP/IPTC fields — never the pixels.)
2. **Identity by content hash, not path** — moved/renamed files are re-linked, not duplicated (see plan Part II §C for the exact rule).
3. **Crash-safe catalog:** WAL + transactions + single writer; auto-backup before risky ops; integrity check + restore on startup.
4. **Responsive UI:** the GUI runs on the Qt main thread ONLY; all indexing/ML runs on a persistent, resumable background job queue and reports back via signals/slots. Never block the window; never touch widgets off-thread.
5. **Cross-platform from day one:** test on both Windows and Ubuntu; identify drives by volume serial/UUID, not letter/mount path; apply EXIF orientation to thumbnails; isolate per-file errors so one bad file never aborts a scan.
6. **Track provenance** for reversible imports. **Elegant, simple UI:** smart defaults, advanced options tucked away.

## Project structure (target)
- `core/` — catalog, indexing, hashing, ML, importers, export (NO UI imports here)
- `ui/` — PySide6 windows, views, widgets
- `core/db/` — schema + migrations
- `tests/` — headless pytest + fixture images (incl. one HEIC, one RAW, a sample `.picasa.ini`, a sample XMP)
- `main.py` — entry point

Keep core logic fully independent of the UI so it can be tested without a window.

## How to work
- Build **one stage at a time**, in the order in plan Part III. Do not jump ahead.
- Each stage ends at a **"Done when"** milestone = (a) headless tests pass **and** (b) a short manual GUI checklist. After each stage, **state exactly what the user should run and what they should see** (you can't see the window).
- **Commit to git** at every working milestone.
- Stick to the locked stack; **ask before adding a new heavyweight dependency**.
- Build/package separately on each OS (PyInstaller/AppImage don't cross-compile).

## Current focus: Stage 0 + Stage 1
**Stage 0:** venv + pinned deps; split `core/`+`ui/`; PySide6 blank window (high-DPI) on both OSes; SQLite (WAL) + `schema_version` migrations; `platformdirs` paths + rotating logger + settings store; `pytest` harness with fixture images.

**Stage 1:** schema (plan §4); folder scanner (JPEG/PNG/HEIC/RAW) with per-file error isolation; quick-signature + partial id + lazy full hash + the move/relink rule; EXIF extraction (mtime fallback); thumbnail cache honoring EXIF orientation; single-writer DB + resumable job queue off the main thread; auto-backups + startup integrity check; headless tests.

**Done when:** launches on Windows + Ubuntu; indexes the test folder without freezing; survives a forced quit and resumes; recognizes a moved/renamed file (not a duplicate); shows correctly-rotated thumbnails; Stage 1 tests pass.

## Development guardrails
- Develop against a small **test folder** (a few hundred photos), never the whole library.
- Never write to or delete anything in the source photo folders.
- Catalog + cache live in a per-user data dir (via platformdirs); make it configurable.
- If you find yourself improvising outside the plan, re-read the plan and this file.
