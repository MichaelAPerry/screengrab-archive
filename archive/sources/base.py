from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Iterator

from archive.models import Episode


class TranscriptSource(ABC):
    """A provider of episode transcripts."""

    name: str = ""
    #: Whether the adapter itself makes network requests.
    fetches_remote: bool = False

    @abstractmethod
    def available(self) -> list[tuple[int, int]]:
        """(season, episode) pairs this source can provide."""

    @abstractmethod
    def load(self, season: int, episode: int) -> Episode | None:
        """Return the parsed episode, or None if this source doesn't have it."""

    def iter_episodes(self, wanted: Iterable[tuple[int, int]] | None = None) -> Iterator[Episode]:
        targets = sorted(set(wanted)) if wanted is not None else self.available()
        have = set(self.available())
        for season, episode in targets:
            if (season, episode) in have:
                ep = self.load(season, episode)
                if ep is not None and ep.lines:
                    yield ep
