"""Filesystem locations. Every path can be overridden with an environment variable.

    ARCHIVE_DB        SQLite database            (default: data/show/archive.db)
    ARCHIVE_MEDIA     local episode video root   (default: data/show/media/)
    ARCHIVE_IMAGES    extracted WebP frames      (default: data/show/images/)
    ARCHIVE_RAW       cached raw source pages    (default: data/show/raw/)
    ARCHIVE_MANIFEST  episode manifest           (default: data/show/episodes.json)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from archive import SERIES

ROOT = Path(__file__).resolve().parent.parent


def _path(env: str, default: Path) -> Path:
    value = os.environ.get(env)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Config:
    db_path: Path
    media_dir: Path
    images_dir: Path
    raw_dir: Path
    manifest_path: Path
    serve_video: bool

    @property
    def series(self) -> str:
        if self.manifest_path.exists():
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return manifest.get("series") or SERIES
        return SERIES

    @classmethod
    def load(cls) -> "Config":
        show_dir = _path("ARCHIVE_SHOW_DIR", ROOT / "data" / "show")
        return cls(
            db_path=_path("ARCHIVE_DB", show_dir / "archive.db"),
            media_dir=_path("ARCHIVE_MEDIA", show_dir / "media"),
            images_dir=_path("ARCHIVE_IMAGES", show_dir / "images"),
            raw_dir=_path("ARCHIVE_RAW", show_dir / "raw"),
            manifest_path=_path("ARCHIVE_MANIFEST", show_dir / "episodes.json"),
            serve_video=os.environ.get("ARCHIVE_SERVE_VIDEO", "0") == "1",
        )
