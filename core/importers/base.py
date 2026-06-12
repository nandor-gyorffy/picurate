"""Base types shared by all importers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class ImportRecord:
    """Normalised metadata for one photo from any import source."""
    filename: str = ""
    source_path: str = ""     # absolute path as seen in the source
    rating: int | None = None
    flag: int | None = None   # 1=pick, 2=reject
    caption: str | None = None
    keywords: list[str] = field(default_factory=list)
    album_names: list[str] = field(default_factory=list)
    # Picasa face regions: [(name, (left, top, right, bottom)) in 0-1 normalised coords]
    faces: list[tuple[str, tuple[float, float, float, float]]] = field(default_factory=list)

    # Filled by the matching engine, not the importer
    matched_photo_id: int | None = None


class BaseImporter:
    """Parse a source and yield ImportRecord objects."""

    source_type: str = "unknown"

    def records(self, source_path: str) -> Iterator[ImportRecord]:
        raise NotImplementedError

    def preview(self, source_path: str, limit: int = 200) -> list[ImportRecord]:
        recs = []
        for i, r in enumerate(self.records(source_path)):
            if i >= limit:
                break
            recs.append(r)
        return recs
