# Picurate — Build Plan

A local, private, **desktop** photo organizer for **Windows + Linux**, in the spirit of Picasa: sort by **people**, **places**, and **topics**; **review folders and pick photos into collections**; curate **"best of" collections**; **export collections to a stick or gallery**; **import your existing organization**; keep everything **safe and recoverable**; all behind an **elegant, simple interface** that's **easy to install — and to uninstall.**

Two parts: **Part I — What we're building** (design, features) and **Part II — Step-by-step implementation plan**.

---

# PART I — WHAT WE'RE BUILDING

## 1. Guiding principles

- **Non-destructive / index, don't move.** Build a *catalog* (a database pointing at your files); keep all knowledge there; never alter originals unless asked.
- **Your work is durable and portable.** The catalog is crash-safe and auto-backed-up, and your organization can be mirrored into the photo files so it can never be silently lost (sections 10, 11).
- **Desktop-first, no command line.** Launch from an icon; double-click to install; clean to uninstall.
- **Elegant and simple.** Smart defaults, calm layout, advanced options tucked away.
- **Everything local, no cloud.** Faces/places/topics computed on your machine — private, free, offline.
- **Track files by content, not path.** A content hash lets the catalog survive moves/renames (section 5) and makes import reliable.
- **Track provenance.** Record where each rating/tag/person came from, so imports are traceable and reversible.
- **Background work, responsive UI.** Slow jobs run on a worker queue; the window never freezes.
- **Ship something small first**, then layer features on the same catalog.

## 2. Technology stack

| Concern | Recommendation | Notes / alternatives |
|---|---|---|
| **Language** | Python | Best ML ecosystem; one language end to end. |
| **GUI** | PySide6 (Qt) | Native-feeling on Windows + Linux; great thumbnail grids. *Alt:* local web UI. |
| **Database** | SQLite (WAL mode) | One file, zero-config, crash-safe with transactions. *Optional:* SQLCipher for an encrypted catalog. |
| **Vector search** | hnswlib / FAISS / `sqlite-vec` | Nearest-neighbor for faces & semantic search. |
| **ML runtime** | ONNX Runtime | Cross-platform CPU, optional GPU, light install. |
| **Faces** | InsightFace (RetinaFace + ArcFace) | See section 3. |
| **Topics** | CLIP (+ optional Places365) | See section 3. |
| **Images** | Pillow + `pillow-heif` + `rawpy` | HEIC + RAW support. |
| **Metadata** | `pyexiv2` / exiftool | EXIF/XMP/IPTC read **and write** (for the metadata mirror). |
| **Reverse geocoding** | `reverse_geocoder` (offline) | GPS → city/country, no internet. |
| **File watching** | `watchdog` | Catch moves/renames live (section 5). |
| **Near-duplicates** | `imagehash` | Collapse bursts. |
| **Packaging** | PyInstaller + Inno Setup (Win); AppImage/Flatpak (Linux) | Briefcase is an alternative. |

## 3. The AI we use, and how it works

All the "smart" parts run as **local models via ONNX Runtime** — downloaded once, then run on your own machine with no internet and no per-use cost. Three jobs:

- **Faces — InsightFace.** A *detector* finds each face; a *recognition model* turns it into an **embedding** — about 512 numbers that act as a fingerprint. Same person → similar numbers. The app stores and compares these numbers rather than a guess at "who this is," which is why naming one face teaches it the rest, and why imported Picasa names attach cleanly.
- **Topics — CLIP.** Trained so images and words share one "meaning space" (a beach photo sits near the word *beach*). We score each photo against a list of candidate words to produce tags; the same mechanism powers **search by description**. Optional **Places365** adds dedicated scene categories.
- **Best-of quality — classic computer vision** (sharpness, exposure) plus an optional small aesthetic-scoring model.

**How we work the problem:** models run **once at index time**; their outputs (embeddings, tags) are stored in the catalog, so all later searching, clustering, and matching is fast math on stored numbers — no re-running models.

**Why local open models:** private, free, offline-capable, cross-platform via ONNX. (Cloud AI APIs were rejected for privacy, cost, and offline reasons.)

**One real gotcha:** embeddings are only comparable within the *same* model. If you ever upgrade the face model, old and new numbers aren't on the same scale — so you'd re-process faces. Pin model versions and treat any upgrade as a deliberate re-index.

## 4. Data model (SQLite)

- **photos** — `id, file_path, file_hash, quick_signature, filename, date_taken, camera_make, camera_model, width, height, file_size, gps_lat, gps_lon, place_id, volume_id, status (ok/missing/offline), thumbnail_path, rating, flag, quality_score, phash, trip_id`
- **people** — `id, name` · **faces** — `id, photo_id, bounding_box, embedding, person_id, confidence, source`
- **tags** — `id, name, type` · **photo_tags** — `photo_id, tag_id, confidence, source`
- **places** — `id, city, region, country, lat, lon` · **trips** — `id, name, start_date, end_date, primary_place_id`
- **collections** — `id, name, type, rules, source` · **collection_photos** — `collection_id, photo_id`
- **volumes** — `id, label` (track removable drives) · **import_batches** — `id, source_type, source_path, run_at`

`source` columns record where each fact came from; `status`/`volumes` support the offline-drive handling in section 5.

## 5. Ingestion pipeline & keeping the catalog in sync

**Pipeline:** discover files → detect new/changed/moved → extract EXIF → cache a thumbnail → reverse-geocode → detect faces → classify topics → compute perceptual hash + quality. The last three run as low-priority background jobs.

**Keeping the catalog correct when files move.** Files get moved, renamed, and reorganized outside the app. The catalog stays right because a photo's identity is its **content hash**, not its path:
- **Fast change check first:** a cheap signature (size + modified-time) catches most changes; a full hash is computed only when needed.
- **Move/rename detection:** on re-scan, a file found at a new path whose hash matches a known-but-now-missing photo is recognized as the same photo moved — its path is updated and **all metadata is kept**.
- **Missing vs offline:** a catalogued file whose path is gone is marked *missing*, not deleted (it might be an unplugged drive). Removable drives are tracked by volume, so their photos show as *offline* (still browseable from cached thumbnails) rather than lost.
- **Live watching (optional):** while the app is open, `watchdog` catches moves in real time; anything moved while it's closed is caught on the next scan.
- **Manual relink:** if a file was also edited (so its hash changed) and can't auto-relink, a "locate missing files" dialog lets you point at the new folder.

**How Picasa did it — and how we do better.** Picasa stored per-folder metadata in a `.picasa.ini` *inside each folder*, so moving a whole folder carried its stars/captions/faces along — robust. But album membership and edits lived in a central database keyed to file *paths*, so moving or renaming files outside Picasa often broke albums and lost edits — a well-known frustration. Our hash-based identity (plus the optional metadata mirror in section 10) is designed specifically to survive the arbitrary moves that broke Picasa.

## 6. Core features

**Review & cull — step through a folder, pick into a collection.** Open any folder/selection and step through photos (loupe or filmstrip), keyboard-driven: rate (1–5), flag pick/reject, rotate, and **add to collection** via a quick type-to-search picker (or create one on the spot). A "selected tray" shows your picks; commit them in one action. "Only show unreviewed" lets you resume. Walk a trip folder once → come out with a curated best-of.

**People (faces):** detect → embed → auto-cluster → name a cluster once and it propagates → match new photos with confirmation for borderline cases → easy merge/split/correct.

**Places:** automatic from GPS (Country → Region → City) via offline geocoding; fill gaps by assigning a trip/folder a place or borrowing location from nearby-in-time photos; optional map.

**Topics:** zero-shot CLIP tagging against an extensible label set, plus semantic search ("mountains at sunset"); optional Places365.

**Collections & best-of:** manual albums, ratings + flags, **smart collections** (rules), near-duplicate grouping, quality scoring, one-click "best of this trip."

**Search & browsing:** combine person + place + topic + date + rating; views for timeline, folder, place, person, topic, map; fast grid with a size slider.

## 7. Export & share (collection → stick / gallery)

A collection isn't useful if it's trapped in the app. Export takes any collection (or selection) and writes **real image files** to a folder, external drive, or **USB stick**:
- **Originals or resized:** full-quality for archiving, or smaller copies (pick max size/quality) to fit more on a stick and share faster.
- **Folder layout:** flat, or by trip / date / album. **Naming:** keep original, or sequential/date-based, with automatic collision handling.
- **Carry the organization along:** optionally embed captions, ratings, keywords, and people into each file's metadata (XMP/IPTC) so your work travels with the photos.
- **Privacy on share:** optionally strip GPS, and optionally blur faces of unknown or chosen people.
- **Self-contained gallery (the "show others" feature):** optionally generate a small static **HTML gallery** (`index.html` + the images) on the stick — it opens in any browser on any computer with nothing installed; optional slideshow.
- **Contact-sheet PDF** as another shareable format.
- **Integrity:** every copied file is verified by hash, so you know the export is complete and intact.

## 8. Importing existing organization

Importers are **plugins** behind one interface; each emits normalized records the catalog applies. Every import is **non-destructive, previewable, idempotent, reversible.**

| Source | What you get | Where it lives |
|---|---|---|
| **Picasa** | Stars, captions, **named people + face boxes**, albums | `.picasa.ini` + `contacts.xml` + Picasa2Albums |
| **Folder structure** | Instant first organization | Folder names → collections/tags/trips |
| **Embedded XMP/IPTC** | Ratings, labels, keywords, captions, named faces | Inside files (Lightroom, Bridge, digiKam, Photo Gallery) |
| **XMP sidecars / digiKam & Lightroom catalogs / Google Takeout** | Tags, ratings, albums, people | Sidecars / SQLite DBs / per-photo JSON |

**Matching:** path → hash → filename+date. **Conflicts:** dry-run preview, merge rule (keep/prefer/both), undo by batch. **People payoff:** imported named faces become free labelled data that seeds your recognizer. **Picasa note:** `rect64(hex)` face boxes decode to four 16-bit values ÷ 65535; on Linux the files sit inside Picasa's Wine prefix.

## 9. Picasa feature parity (honest map)

- **Organization (Core):** scan/watch folders, albums, stars, captions, tags, People/faces, Places, search, timeline, folder tree, duplicate detection, batch rename, hide photos.
- **Viewing (Core):** thumbnail grid + size slider, loupe/zoom, full-screen, slideshow, properties/EXIF.
- **Editing (Later / its own mini-project):** crop, straighten, rotate, red-eye, retouch, auto-enhance, tuning, effects, text — all non-destructive. A large opt-in body of work.
- **Output & sharing (Mostly core):** export/resize/watermark, contact-sheet PDF, HTML gallery, backup. *Web-album upload — skip (service gone).*
- **Creative extras (Optional):** collage, movie-from-photos.

## 10. Data safety, backups & recovery

Your catalog holds work you can't easily recreate. Protecting it is first-class.
- **Crash-safe by design:** SQLite in **WAL mode** with proper transactions — a crash or power loss can't leave the catalog half-written; it recovers on next launch. Multi-step operations (imports, bulk edits) are transactional (all-or-nothing). Background jobs are **resumable**.
- **Automatic backups:** periodic safe snapshots (SQLite online backup — works while running), plus an automatic snapshot **before risky operations**. A few rotating backups in the user data folder, optionally mirrored to a second drive.
- **Self-healing:** an integrity check on startup; if the catalog is ever corrupt, it offers to **restore the latest good backup** automatically.
- **The metadata mirror (strongest safety net):** optionally write ratings, captions, keywords, and named face regions back into the photo files / XMP sidecars (standard metadata, never the pixels). Then even total catalog loss doesn't lose the human-made work — a fresh scan reads it back. This also makes your data **portable** to other tools, and powers the graceful uninstall. Optional, because it modifies files.
- **Security/privacy:** nothing leaves your machine. If you want the catalog itself protected (it holds names and locations), enable the **encrypted catalog** (SQLCipher).

## 11. Desktop app, install, uninstall & interface

**Install (effortless):** ships as a normal desktop app with an icon and menu entry; no terminal, no separate Python for the user. **Windows:** one installer (PyInstaller + Inno Setup). **Linux:** AppImage and/or Flatpak. A **first-run wizard** (welcome → pick watch folders → optional import → index in the background). Models are bundled or fetched once. Catalog + cache live in a per-user data folder.

**Uninstall (clean and safe):**
- **Your photos are never touched** — uninstalling removes the program, never your originals. Hard rule.
- Everything the app created (catalog, thumbnail cache, models, settings, logs) lives in **known per-user locations**. The uninstaller offers a checkbox **"also remove my library data"** — leave it off and a reinstall resumes exactly where you left off; tick it for a completely clean removal.
- **Graceful exit:** before uninstalling, the app offers to (a) write your organization back into your files/XMP (the metadata mirror) and/or (b) export a portable copy of the catalog — so nothing is lost and you can move tools or reinstall later.
- Exact data locations are documented for manual cleanup.

**Interface (elegant & simple):** a clean three-pane layout — left sidebar (Library / Folders / Albums / People / Places / Topics / Trips), center grid, contextual right panel. One obvious primary action per screen; generous whitespace; large legible thumbnails; full keyboard control in review/cull; light + dark themes; unobtrusive background-progress, never a blocking spinner.

## 12. More useful features

- **Video & Live Photos:** index phone/camera videos (MP4/MOV) and motion photos with thumbnail, date, and place — travel libraries are full of them.
- **Import from card/phone (ingest):** a proper "copy from camera/SD card into a folder structure, then index" flow (Picasa-style), complementing watch folders.
- **GPS-track geotagging:** match photo timestamps to a GPX track from a phone/logger to place photos from GPS-less cameras — excellent for travel.
- **Find similar / "more like this":** visual-similarity search using the same embeddings.
- **People-together search:** "photos with X and Y."
- **Saved searches, tag hierarchies, bulk tag editing.**
- **Statistics dashboard:** counts by year, place, person, camera. **Map route view** of a trip; **calendar/heatmap** of activity.
- **Soft-delete trash:** removing a photo in-app goes to a recoverable trash first; deleting the actual file is always explicit and separate.
- **Multi-language UI** and full keyboard accessibility.

## 13. Performance
Background job queue; aggressive thumbnail caching; vector index for faces/semantic search as the library grows; ONNX GPU optional with a guaranteed CPU fallback.

---

# PART II — ENGINEERING SPECIFICS (read before the build steps)

This part pins down the decisions and gotchas that otherwise cause avoidable bugs and cross-platform breakage. **Target platforms: Windows 10/11 (x64) and Ubuntu 22.04+ (Debian-based).** Build/package separately on each OS — PyInstaller and AppImage do not cross-compile.

### A. Cross-platform foundations
- **Paths:** use `pathlib.Path` everywhere; never string-concatenate. Account for Windows case-insensitive vs Linux case-sensitive filenames; enable Windows long-path (>260 char) support; store a normalized path plus keep the original.
- **Data locations:** use `platformdirs` to find catalog, cache, logs, and models — never hardcode (Windows `%LOCALAPPDATA%`, Linux `$XDG_DATA_HOME`/`~/.local/share`).
- **Drive/volume identity:** identify a photo's storage volume by a stable id — **Windows volume serial number, Linux filesystem UUID** — never the drive letter or mount path (those change). This is what makes the "offline drive" handling reliable.
- **High-DPI:** enable Qt high-DPI scaling and test Windows display scaling (125%/150%) and Linux fractional scaling, or the UI looks blurry/tiny.
- **File watching:** `watchdog` uses inotify (Linux) / ReadDirectoryChangesW (Windows). Linux inotify has a per-user watch limit — watch top-level folders, degrade gracefully, and fall back to periodic rescans for huge trees.

### B. App architecture & concurrency (the most crash-prone area)
- **Qt rule:** the GUI lives on the main thread ONLY. Never touch widgets from other threads, and never run indexing/ML on the main thread (it freezes the window).
- **Job queue:** a persistent `jobs` table processed by background worker(s); workers report back to the UI via Qt signals/slots. Persisting jobs makes them **resumable after a crash**.
- **ML execution:** run inference in a worker (ONNX Runtime releases the GIL); load each model once and reuse.
- **SQLite access:** one connection per thread; route ALL writes through a **single writer** (a dedicated DB-writer thread/serialized queue) to avoid "database is locked"; readers run concurrently under WAL. Wrap multi-step operations in transactions.
- **Clean shutdown:** finish or checkpoint the current job, WAL-checkpoint, close connections.

### C. Catalog integrity details
- **Change detection:** treat a file as unchanged if size + mtime match the stored quick-signature; otherwise re-read.
- **Hashing:** compute a fast partial id (size + hash of first/last 64 KB) for quick matching, and a full content hash lazily in the background for exact identity; store both.
- **Move/rename rule:** for a new path with no row, find a `missing` row with the same full hash. Exactly one match → relink (keep all metadata). Multiple matches (true duplicates) → do NOT auto-relink; record separately and flag as duplicate.
- **Error isolation:** one corrupt/unreadable file logs a warning and is skipped — it must never abort the whole scan.
- **Backups:** SQLite online-backup snapshots; before every import/bulk edit; keep N rotating copies; `integrity_check` on startup with an auto-restore offer.

### D. Image & metadata handling
- **EXIF orientation:** ALWAYS apply it (Pillow `ImageOps.exif_transpose`) when making thumbnails and displaying, or photos appear sideways.
- **Dates:** store `date_taken` from EXIF `DateTimeOriginal` (plus the offset tag if present); EXIF carries no timezone, so be consistent (naive local) and fall back to file mtime when absent.
- **Formats:** register `pillow-heif` for HEIC; decode RAW with `rawpy` (use the embedded preview for fast thumbnails). Both need native libs — see deps.
- **RAW+JPEG pairing:** group a RAW and a same-basename JPEG as one logical photo (two files), so they aren't treated as duplicates.
- **Metadata read/write:** use **exiftool** (bundled binary) for XMP/IPTC, especially named **face regions** (MWG Regions and Microsoft "People" RegionInfo) and Picasa data — it handles these namespaces far more reliably than pure-Python libraries. Only standard fields; never modify pixels.

### E. AI model management
- **CPU by default:** ship `onnxruntime` (CPU). GPU (`onnxruntime-gpu`/CUDA) is opt-in and auto-detected — never force CUDA on CPU users.
- **Model manifest:** name + URL + SHA-256 + size for each model; first-run download with progress, retry, and checksum verification; allow manual/offline placement. Models live in the per-user data dir.
- **Versioning:** pin model versions; record which embedding model produced each face embedding. Changing the model means re-embedding — never mix embeddings from different models.

### F. Config, logging, migrations
- **Settings:** a small store (settings table or a TOML file in the data dir) with sane defaults; the watch-folder list lives here.
- **Logging:** rotating log file in the data dir; friendly user-facing errors, full detail in the log; an "export diagnostics" action.
- **Migrations:** a `schema_version` row + ordered migration steps run on startup; never destructive without a backup first.

### G. Verification approach (Claude Code cannot see the GUI)
- Keep `core/` UI-independent and cover it with **headless pytest** tests: indexing, hashing, move detection, EXIF/date parsing, geocoding, importers, export integrity. Claude Code runs these itself.
- Ship tiny **fixture files** in `tests/` (a couple of JPEGs, one HEIC, one RAW, a sample `.picasa.ini`, a sample XMP) so decoders/importers are testable.
- Every stage's "Done when" = (a) headless tests pass **and** (b) a short **manual GUI checklist** the user runs. After each stage, Claude Code must state both explicitly.

### H. Dependencies (with platform notes)
PySide6 · platformdirs · Pillow · pillow-heif *(wheels bundle libheif; on Ubuntu you may also `apt install libheif1`)* · rawpy *(LibRaw; wheels available)* · **exiftool** *(external binary — bundle one per OS)* · onnxruntime *(CPU; onnxruntime-gpu optional)* · InsightFace / ONNX face + CLIP models · reverse_geocoder · imagehash · numpy · hnswlib *(or faiss-cpu)* · watchdog · pytest *(dev)* · PyInstaller + Inno Setup *(Windows build)* / AppImage tooling *(Linux build)*. Prefer pip wheels; document any apt packages Ubuntu needs.

### I. Additional functions worth adding
- **RAW+JPEG pairing** (also a correctness item above).
- **"On this day" / anniversary memories** — nice for revisiting travels.
- **General undo/redo** for organizing actions (an action history), beyond import undo.
- **Saved export presets**; **auto-ingest** from watched folders (index new files automatically).
- **Customizable keyboard shortcuts** + an in-app cheatsheet.
- **Tag autocomplete + synonyms/merge**; **side-by-side duplicate review**.
- **"Continue where you left off"** session resume; **diagnostics export**.
- **Internationalization (i18n):** scaffold Qt translations early (even if you ship English first) — retrofitting languages later is painful. Useful if you want a German or other-language UI.

---

# PART III — STEP-BY-STEP IMPLEMENTATION PLAN

Build in order. Each **stage** ends at a usable milestone.

*Apply the engineering specifics in Part II throughout — they are not optional polish.*

### Stage 0 — Project setup
1. Repo + Python environment (`uv`/`venv`), pinned deps; `requirements.txt`/`pyproject`. 2. Split **core** (catalog/indexing/ML) from **ui**; `core` has no UI imports. 3. PySide6 blank window with high-DPI scaling, runs on Windows 10/11 + Ubuntu 22.04+. 4. SQLite (WAL) + a `schema_version` migration runner. 5. `platformdirs`-based data/cache/log paths; rotating logger; a settings store with defaults. 6. `pytest` harness + a few fixture images in `tests/`.
> **Done when:** an empty window launches on both OSes; `pytest` runs (even if near-empty); data/log paths resolve per-OS.

### Stage 1 — Catalog, indexing & safety engine
7. Create the schema (§4). 8. Folder scanner (JPEG/PNG/HEIC/RAW) with **per-file error isolation**. 9. Quick-signature (size+mtime) + partial id + lazy full hash; insert/update; **move/rename relink rule** (Part II §C); missing/offline handling via stable volume id. 10. EXIF extraction (date with mtime fallback, GPS, camera). 11. Thumbnail cache **honoring EXIF orientation**. 12. **Single-writer** DB access + a persistent, resumable **job queue** off the main thread. 13. **Automatic backups + startup integrity check + restore-from-backup.** 14. Headless tests for hashing, move detection, EXIF/date parsing, error isolation.
> **Done when:** it indexes the test folder without freezing, survives a forced quit and resumes, recognizes a moved/renamed file (not a duplicate), shows correctly-rotated thumbnails, and the Stage 1 tests pass.

### Stage 2 — Browsing UI (first usable app)
12. Thumbnail grid + size slider. 13. Sidebar: Folders + Timeline. 14. Loupe view (zoom, full-screen, next/prev). 15. Properties/EXIF panel.
> **Milestone:** a usable visual browser.

### Stage 3 — Culling, collections & review workflow
16. Ratings + flags (shortcuts). 17. Collections data + UI. 18. **Folder review/cull mode** with quick add-to-collection + selected-tray. 19. Basic search/filter.
> **Milestone:** review a trip folder in one pass → a "best of" collection.

### Stage 4 — Export & share
20. Export a collection to a folder/stick (originals or resized; layout/naming). 21. Optional metadata-embed + GPS-strip + face-blur. 22. **Self-contained HTML gallery** + contact-sheet PDF. 23. Hash-verify copies.
> **Milestone:** put a collection on a stick that opens on any computer.

### Stage 5 — Import existing organization
24. Importer framework (plugins, preview, batches, undo). 25. Folder-structure importer. 26. Picasa `.picasa.ini` importer. 27. XMP/IPTC importer. 28. Matching engine + conflict/merge UI + preview report.
> **Milestone:** old Picasa stars/captions/albums appear in the app.

### Stage 6 — Places & Trips
29. Offline reverse-geocode. 30. Places view (+ optional map). 31. Manual place + time interpolation. 32. Automatic trip grouping.
> **Milestone:** browse by place; auto-grouped trips.

### Stage 7 — People (faces)
33. Detection + embeddings (queue). 34. Cluster unnamed faces. 35. Name-once UI + merge/split + confirm. 36. Incremental matching (vector index). 37. **Import named faces** to seed the recognizer.
> **Milestone:** browse by person; new imports suggest who's in them.

### Stage 8 — Topics & semantic search
38. CLIP tagging (default + custom labels). 39. Optional Places365. 40. Semantic text search.
> **Milestone:** browse/search by topic and description.

### Stage 9 — Best-of automation
41. Perceptual-hash near-duplicate grouping. 42. Quality scoring. 43. Smart collections + one-click best-of.
> **Milestone:** the app proposes best shots and de-dupes bursts.

### Stage 10 — Extras (pick what you want)
44. Slideshow. 45. Batch rename/export. 46. **Metadata mirror write-back** (durability + portability). 47. Card/phone ingest; GPX geotagging; video indexing; find-similar; stats dashboard; soft-delete trash. 48. **Optional:** non-destructive editing suite (its own mini-project); collage maker.
> **Milestone:** parity with the Picasa functions you care about.

### Stage 11 — Packaging, install, uninstall & polish
49. First-run wizard. 50. Desktop launcher + icon. 51. Windows installer (PyInstaller + Inno Setup); Linux AppImage/Flatpak. 52. Bundle/first-run-download models. 53. **Clean uninstaller** with "remove library data?" + graceful-exit export. 54. UI polish (elegant + simple), themes, performance tuning.
> **Milestone:** a non-technical person installs by double-clicking, uses it, and can cleanly uninstall.

---

## 14. Hardest parts (plan for these)
- **Face-recognition UX** — the merge/split/correct flow matters more than the model.
- **Catalog durability** — getting WAL + transactions + backup/restore genuinely crash-proof.
- **Move/relink edge cases** — files that were *also edited* (hash changed) need manual relink.
- **Import matching & conflicts** — reliable matching plus a safe preview/undo flow.
- **Picasa album recovery** — `.ini`/`contacts.xml` are readable; the binary album data is reverse-engineered, so do the high-value readable parts first.
- **Metadata write-back safety** — only standard XMP/IPTC fields, never the pixels; always reversible.
- **HEIC + RAW decoding** and **cross-platform ML packaging** — test early.
- **Staying simple** — defend the elegant goal against feature creep with smart defaults and hidden advanced options.

### Suggested first commit
Stage 0 + early Stage 1: launch a window, point it at one travel folder, index EXIF, show a date-sorted grid — backed by a crash-safe, auto-backed-up catalog. Everything else hangs off it.
