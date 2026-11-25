"""Canonical in-memory data model shared by every source adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from archive import SERIES


class TimingSource(str, Enum):
    """Where a dialogue line's start/end came from. Stored verbatim in the DB."""

    NONE = "none"          # transcript-only, no timing known
    SUBTITLE = "subtitle"  # line *is* a subtitle cue, timing taken directly
    ALIGNED = "aligned"    # transcript line fuzzily matched to subtitle cue(s)
    MANUAL = "manual"      # a human verified or corrected the timing


class Origin(str, Enum):
    """What kind of document a dialogue line was parsed from."""

    TRANSCRIPT = "transcript"
    SUBTITLE = "subtitle"


_CODE_RE = re.compile(r"[Ss](\d{1,2})\s*[Ee](\d{1,2})")


def parse_episode_code(value: str) -> tuple[int, int] | None:
    """'S04E08', 's4e8', 'S04E08.mp4', '4x08' -> (4, 8)."""
    m = _CODE_RE.search(value) or re.search(r"(?<!\d)(\d{1,2})x(\d{2})(?!\d)", value)
    return (int(m.group(1)), int(m.group(2))) if m else None


def episode_code(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


@dataclass
class Line:
    text: str
    sequence: int
    speaker: str | None = None
    start: int | None = None  # milliseconds
    end: int | None = None
    timing_source: TimingSource = TimingSource.NONE
    timing_confidence: float | None = None


@dataclass
class Episode:
    season: int
    episode: int
    title: str | None = None
    source: str | None = None
    source_url: str | None = None
    origin: Origin = Origin.TRANSCRIPT
    lines: list[Line] = field(default_factory=list)
    series: str = SERIES

    @property
    def episode_code(self) -> str:
        return episode_code(self.season, self.episode)

    def to_dict(self) -> dict:
        return {
            "series": self.series,
            "season": self.season,
            "episode": self.episode,
            "episode_code": self.episode_code,
            "title": self.title,
            "source": self.source,
            "source_url": self.source_url,
            "lines": [
                {
                    "speaker": ln.speaker,
                    "text": ln.text,
                    "start": ln.start,
                    "end": ln.end,
                    "sequence": ln.sequence,
                    "timing_source": ln.timing_source.value,
                }
                for ln in self.lines
            ],
        }


@dataclass
class Cue:
    """One subtitle cue (SRT/WebVTT block)."""

    sequence: int
    start: int  # ms
    end: int    # ms
    text: str   # cleaned: tags removed, lines joined with ' '
    speaker: str | None = None
