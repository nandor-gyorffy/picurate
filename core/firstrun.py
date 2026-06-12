"""First-run detection and setup utilities.

Tracks whether the app has been run before and what setup steps remain.
All state is stored in a small JSON config file in the data directory.
"""
from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path

from core.paths import data_dir


_CONFIG_FILE = "firstrun.json"


def _config_path() -> Path:
    return data_dir() / _CONFIG_FILE


def is_first_run() -> bool:
    """True if the app has never been fully set up on this machine."""
    return not _config_path().exists()


def mark_setup_complete() -> None:
    """Write the firstrun config, marking setup as complete."""
    cfg = {
        "setup_complete": True,
        "version": "0.1.0",
        "platform": platform.system(),
    }
    _config_path().write_text(json.dumps(cfg, indent=2))


def get_setup_config() -> dict:
    """Return the firstrun config dict, or {} if not yet written."""
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def check_model_status() -> dict:
    """Return a dict describing which optional ML models are available."""
    from core.faces import model_available as faces_available
    from core.topics import model_available as clip_available

    insightface_dir = data_dir() / "insightface"
    clip_dir = data_dir() / "clip"

    return {
        "faces": faces_available(),
        "clip": clip_available(),
        "insightface_dir": str(insightface_dir),
        "clip_dir": str(clip_dir),
        "exiftool": shutil.which("exiftool") is not None,
    }


def _find_icon_png(app_dir: Path) -> Path | None:
    """Return the 256x256 PNG icon path if it exists in the app directory."""
    candidates = [
        app_dir / "assets" / "icon" / "picurate.png",
        app_dir / "assets" / "picurate.png",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def install_desktop_launcher(app_dir: Path | None = None) -> bool:
    """Install a .desktop launcher and icon for Linux (XDG hicolor theme).

    app_dir should be the directory containing main.py.
    Returns True on success, False if not on Linux or insufficient permissions.
    """
    if platform.system() != "Linux":
        return False

    if app_dir is None:
        app_dir = Path(__file__).parent.parent.resolve()

    python = shutil.which("python3") or shutil.which("python") or "python3"
    main_py = app_dir / "main.py"

    # Install icon into hicolor theme so the system sees it by name
    icon_src = _find_icon_png(app_dir)
    icon_name = "picurate"
    if icon_src:
        icon_dest = (
            Path.home()
            / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
            / "picurate.png"
        )
        try:
            icon_dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(icon_src), str(icon_dest))
            icon_name = "picurate"  # name without path — system resolves it
        except Exception:
            icon_name = str(app_dir / "assets" / "icon" / "picurate.png")
    else:
        icon_name = str(app_dir / "assets" / "icon" / "picurate.png")

    desktop_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Picurate
Comment=Organize and curate your photos
Exec={python} {main_py} %U
Icon={icon_name}
Terminal=false
Categories=Graphics;Photography;
StartupWMClass=Picurate
StartupNotify=true
"""

    dest = Path.home() / ".local" / "share" / "applications" / "picurate.desktop"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(desktop_content)
        return True
    except Exception:
        return False


def remove_desktop_launcher() -> bool:
    """Remove the .desktop launcher if it exists. Returns True if removed."""
    dest = Path.home() / ".local" / "share" / "applications" / "picurate.desktop"
    if dest.exists():
        try:
            dest.unlink()
            return True
        except Exception:
            return False
    return False


def uninstall_data(remove_catalog: bool = False, remove_models: bool = False) -> dict:
    """Clean up user data as part of uninstall.

    By default only removes the firstrun config.
    Pass remove_catalog=True to delete the catalog (irreversible!).
    Pass remove_models=True to delete downloaded ML models.
    Returns a summary dict of what was removed.
    """
    removed = []

    config_p = _config_path()
    if config_p.exists():
        config_p.unlink()
        removed.append("firstrun_config")

    if remove_models:
        for model_dir in ["insightface", "clip"]:
            p = data_dir() / model_dir
            if p.exists():
                shutil.rmtree(p)
                removed.append(f"models/{model_dir}")

    if remove_catalog:
        from core.paths import catalog_path as _cp
        cp = _cp()
        if cp.exists():
            cp.unlink()
            removed.append("catalog")

    remove_desktop_launcher()

    return {"removed": removed}
