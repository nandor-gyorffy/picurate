"""Content hashing utilities: quick signature, partial id, full SHA-256."""
import hashlib
import os
from pathlib import Path

CHUNK = 64 * 1024  # 64 KB


def quick_signature(path: Path) -> str:
    """Cheap change-detector: '<size>:<mtime_ns>'."""
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def partial_hash(path: Path) -> str:
    """Fast identity check: SHA-256 of (size + first 64 KB + last 64 KB)."""
    st = path.stat()
    size = st.st_size
    h = hashlib.sha256()
    h.update(size.to_bytes(8, "little"))
    with open(path, "rb") as f:
        h.update(f.read(CHUNK))
        if size > CHUNK:
            f.seek(max(0, size - CHUNK))
            h.update(f.read(CHUNK))
    return h.hexdigest()


def full_hash(path: Path) -> str:
    """Full SHA-256 of file content."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def volume_id(path: Path) -> str:
    """Return a stable volume identifier: filesystem UUID on Linux, serial on Windows."""
    import platform
    if platform.system() == "Windows":
        import ctypes
        root = Path(path.anchor)
        serial = ctypes.c_ulong()
        ctypes.windll.kernel32.GetVolumeInformationW(
            str(root), None, 0, ctypes.byref(serial), None, None, None, 0
        )
        return f"win:{serial.value:08X}"
    else:
        # Resolve a stable UUID for the filesystem containing *path*.
        # Strategy 1: findmnt (modern Linux, works for NVMe/NFS/tmpfs alike)
        # Strategy 2: blkid on /dev/block/<major>:<minor> (older systems)
        # Fallback: device major:minor (not stable across reboots but better than nothing)
        import subprocess
        dev = os.stat(path).st_dev
        major = os.major(dev)
        minor = os.minor(dev)

        try:
            result = subprocess.run(
                ["findmnt", "--output", "UUID", "--noheadings",
                 "--source", f"/dev/block/{major}:{minor}"],
                capture_output=True, text=True, timeout=3,
            )
            uuid = result.stdout.strip()
            if uuid and uuid != "":
                return f"uuid:{uuid}"
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["blkid", "-s", "UUID", "-o", "value", f"/dev/block/{major}:{minor}"],
                capture_output=True, text=True, timeout=3,
            )
            uuid = result.stdout.strip()
            if uuid:
                return f"uuid:{uuid}"
        except Exception:
            pass

        return f"dev:{major}:{minor}"
