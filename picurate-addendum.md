# Picurate — Addendum & Clarifications

A supplement to `photo-organizer-plan.md`. Read it alongside the plan; where this adds detail to an existing feature, it is the authoritative spec for that feature.

---

## 1. Windows vs Linux — same app, different packaging

The app is **functionally identical** on both platforms. One Python/PySide6 codebase → the same features, GUI, workflows, and keyboard shortcuts on Windows 10/11 and Ubuntu 22.04+. Only the packaging and a few under-the-hood details differ (all already handled in plan Part II §A).

| Aspect | Windows | Ubuntu/Linux |
|---|---|---|
| **Install** | One `.exe` installer (PyInstaller + Inno Setup); Start-menu entry | One **AppImage** (download → make executable → run), and/or a **Flatpak** (installs via the software centre) |
| **Data location** | `%LOCALAPPDATA%` (via platformdirs) | `~/.local/share` / `$XDG_DATA_HOME` (via platformdirs) |
| **Drive identity** | Volume serial number | Filesystem UUID |
| **File watching** | ReadDirectoryChangesW | inotify (mind the per-user watch limit; degrade to periodic rescan) |
| **Native libs** | Bundled in the build | Bundled; `libheif` may also come from `apt` when building |
| **Build** | Build the installer **on Windows** | Build the AppImage/Flatpak **on Linux** (no cross-compile) |

**Ease-of-use requirements (reaffirmed):**
- Installation must be a single, obvious step on each OS (double-click installer on Windows; run one AppImage on Linux). No terminal required of the end user.
- A **first-run wizard**: welcome → choose watch folders → optional import → background indexing while browsing starts.
- An **elegant, simple GUI** (plan §8, §11): a calm three-pane layout, smart defaults, advanced options tucked away, full keyboard control in the review/cull flow, light + dark themes.

---

## 2. Confirmation — the requested features are all in the plan

| Requested | Where in the plan | Status |
|---|---|---|
| **Face detection + categorize by person** | §6 People; §3 AI (InsightFace detect → embed → cluster → name → browse by person); Part III **Stage 7** | Covered |
| **Group by similar position (places)** | §6 Places (offline reverse geocoding → Country/Region/City); auto-**Trips** by date+location; Part III **Stage 6** | Covered |
| **Group by similar theme (topics)** | §6 Topics (CLIP zero-shot tags + semantic search + find-similar); §3 AI; Part III **Stage 8** | Covered |

Two small clarifications to implement explicitly:
- **Places proximity:** besides naming a place via reverse geocoding, also cluster photos whose GPS points are close together into the same place/spot, so "photos taken at roughly the same location" group even when no city label differs.
- **Topic similarity** reuses the same CLIP image embeddings used for the similarity feature in §3 below — compute them once, use them for tagging, semantic search, find-similar, **and** similarity grouping.

---

## 3. Feature spec — Similarity grouping & best-pick

**This expands the "near-duplicate grouping + quality scoring" already in plan §6 (Collections & best-of) and Part III Stage 9. Treat this section as the detailed requirements.**

### Goal
When building a "best of" collection (or just reviewing a folder), automatically find sets of **very similar** photos — burst shots, near-duplicates, slight variations of the same scene — group them, score how similar they are, and **suggest the best one to keep**, while the user makes the final choice.

### Similarity signals (combine these)
1. **Perceptual hash** (pHash/dHash, already stored as `phash`): compare by Hamming distance. Catches tight near-duplicates and minor edits cheaply.
2. **Deep embedding similarity**: reuse the **CLIP image embedding** (already computed for topics) and compare by cosine similarity. Catches "same scene/subject, different angle or zoom" that pHash misses.
3. **Capture timing / burst**: photos taken within a few seconds of each other (EXIF timestamp) are strong evidence of a burst — use as a grouping boost.

Produce a single **similarity score (0–100%)** between any two photos by combining these (e.g., a weighted blend, with pHash dominating for near-identical and embedding cosine for scene-level similarity).

### Grouping algorithm
- Scope a run to the **current folder or the current selection** (folders are small, so pairwise comparison is cheap).
- Build a graph: connect two photos if their combined similarity is above a threshold (and/or pHash distance below a threshold, and/or within the burst time window).
- Take **connected components** (union-find) → each component is a "similar group"; isolated photos stay ungrouped.
- Store the grouping for the run (see data model).

### Quality / best-pick scoring (to suggest the keeper)
Compute a per-photo **quality score** from:
- **Sharpness** — variance of the Laplacian (blur detection); higher is sharper.
- **Exposure** — histogram-based; penalize heavy clipping / under/over-exposure.
- **Face quality (optional, if faces present)** — prefer larger, sharper, front-facing faces; eyes-open detection is a nice-to-have, mark it optional/advanced.
- **Aesthetic score (optional)** — a small NIMA-style model if you want a learned "looks good" signal.

Combine into one score; store the components too (`sharpness_score`, `exposure_score`). The **highest-scoring photo in each group is flagged "suggested best."** The suggestion is **advisory** — never auto-delete; the user decides.

### UX in the review/cull flow (ties into Stage 3)
- A **"group similar" toggle** in folder review: similar photos collapse into a **stack** showing the count, with the suggested-best on top.
- **Expand a stack** → side-by-side compare, each photo showing its quality components and the **similarity %** relative to the others.
- One tap to **keep this one → add to collection**; the rest can be left unpicked or sent to the soft-delete trash.
- An **"auto-pick best of each group"** action to bulk-select all suggested keepers into the collection, then review.
- A **"similarity aggressiveness" slider** (only-near-identical ↔ loosely-similar) that adjusts the thresholds, with sensible defaults so it works out of the box.

### Data model additions
- A `similarity_groups` table (`id, run_scope, created_at`) + a `photo_similarity_group` membership table (`photo_id, group_id, similarity_to_best`), **or** a nullable `similarity_group_id` on `photos` scoped per run.
- On `photos`: keep `quality_score` (already in schema) and add `sharpness_score`, `exposure_score`.
- Reuse existing `phash` and the stored CLIP embedding — do not recompute.

### Performance
- pHash comparison within a folder is trivial. Embedding cosine within a folder is fine (small N). For any cross-library "find similar," use the existing vector index (hnswlib/FAISS).
- Compute grouping **on demand** when the user enters "group similar" for a folder, or as a low-priority background job after indexing. Cache results; invalidate when the folder's contents change.

### Where it fits in the build
- The **grouping + scoring engine** belongs in `core/` and should be covered by headless tests (feed in a few near-identical fixture images and assert they group and that the sharpest scores highest).
- Hook the **UI (stacks, compare, auto-pick)** into the Stage 3 review/cull screen, and into the Stage 9 best-of automation.
- If you're past Stage 3 already, add the engine now and surface the UI as an enhancement to the existing review screen.

### Done when
- In a folder of burst/near-identical shots, the app groups them, shows a similarity % and per-photo quality scores, flags a suggested best, and lets the user pick one into a collection in a couple of clicks — verified by headless tests on fixtures plus a short manual check.

---

## 4. Desktop launchers & app icon (click-and-start on both OSes)

**Goal:** after installation the user launches Picurate by clicking an icon — no terminal — on both Windows and Ubuntu. This is part of plan Part III **Stage 11** (packaging) and plan §11.

### App icon asset (shared)
- Keep one **source icon** in the repo (e.g. `assets/icon/picurate.svg`, plus a 1024×1024 PNG), transparent background.
- Generate platform formats during the build:
  - **Windows:** a multi-size `.ico` (16/32/48/256) embedded in the exe and used by shortcuts.
  - **Linux:** PNGs for the icon theme (at least 256×256; ideally 48/64/128/256) named `picurate.png`, plus the scalable `picurate.svg`.

### Windows
- Build with PyInstaller using `--icon assets/icon/picurate.ico` so the exe carries the icon.
- The **Inno Setup** installer:
  - Installs per-user under `%LOCALAPPDATA%\Programs\Picurate` by default (no admin needed).
  - Always creates a **Start-menu** entry; offers a **Desktop shortcut** via a checkbox (default on).
  - Registers an uninstaller in Add/Remove Programs.
  - Both shortcuts point at the exe and use the icon.
- Result: double-clicking the Desktop or Start-menu icon launches the app.

### Ubuntu / Linux
- Provide a freedesktop **`.desktop`** entry and install the icon into the **hicolor** theme so the app appears in the Activities/app menu with its icon. Minimal `.desktop`:
  ```
  [Desktop Entry]
  Type=Application
  Name=Picurate
  Comment=Organize and curate your photos
  Exec=picurate %U
  Icon=picurate
  Categories=Graphics;Photography;
  Terminal=false
  StartupNotify=true
  ```
  - Install to `~/.local/share/applications/picurate.desktop` (per-user) or `/usr/share/applications/` (system).
  - Install icons to `~/.local/share/icons/hicolor/256x256/apps/picurate.png` (and other sizes / the SVG).
- **Packaging choice for true click-and-start** (in order of smoothness):
  - **Flatpak (recommended):** registers the `.desktop` + icon automatically, shows up in GNOME Software and the app menu, sandboxed — the most "just works."
  - **.deb:** also installs the `.desktop` entry + icon automatically; familiar to Ubuntu users.
  - **AppImage:** a single runnable file; bundle the `.desktop` + icon inside it (AppImage requires this) and add the menu entry via Gear Lever / `appimaged`, or a tiny built-in "Add to menu" helper. Keep AppImage as the portable, no-install option.
- **Recommendation:** ship a **Flatpak (or .deb)** as the primary click-and-start package; offer the AppImage as the portable alternative.

### Build notes
- Build the Windows installer **on Windows** and the Linux packages **on Linux** (no cross-compile).
- Generate the icon formats from the single source in the build step.

### Done when
- **Windows:** the installer creates working Desktop + Start-menu shortcuts with the icon, and double-clicking launches Picurate.
- **Ubuntu:** after installing the Flatpak/.deb (or integrating the AppImage), Picurate appears in the app menu with its icon and launches on click.
