"""Folder-structure importer: subfolder names become collection names."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from core.importers.base import BaseImporter, ImportRecord

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff",
               ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".dng", ".raf"}


class FolderImporter(BaseImporter):
    """
    Walk a folder tree.  Each direct subfolder of root becomes an album name.
    Photos at root level go into an album named after the root folder itself.
    """

    source_type = "folder"

    def records(self, source_path: str) -> Iterator[ImportRecord]:
        root = Path(source_path)
        if not root.is_dir():
            return

        # Photos directly under root → album = root folder name
        for fp in sorted(root.iterdir()):
            if fp.is_file() and fp.suffix.lower() in _PHOTO_EXTS:
                yield ImportRecord(
                    filename=fp.name,
                    source_path=str(fp),
                    album_names=[root.name],
                )

        # Subfolders → album = subfolder name
        for sub in sorted(root.iterdir()):
            if not sub.is_dir():
                continue
            for fp in sorted(sub.rglob("*")):
                if fp.is_file() and fp.suffix.lower() in _PHOTO_EXTS:
                    yield ImportRecord(
                        filename=fp.name,
                        source_path=str(fp),
                        album_names=[sub.name],
                    )
