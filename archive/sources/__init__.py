"""Transcript source adapters.

Every adapter turns some provider's format into canonical `Episode` objects, so the
rest of the pipeline never knows where dialogue came from. Add a provider by writing
a `TranscriptSource` subclass and registering it in `SOURCES`.
"""

from __future__ import annotations

from archive.sources.base import TranscriptSource
from archive.sources.foreverdreaming import ForeverDreamingSource
from archive.sources.plaintext import PlainTextSource

SOURCES: dict[str, type[TranscriptSource]] = {
    cls.name: cls for cls in (ForeverDreamingSource, PlainTextSource)
}

__all__ = ["SOURCES", "TranscriptSource"]
