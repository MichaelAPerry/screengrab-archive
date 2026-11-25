"""Local plain-text transcripts: one file per episode, named like S01E01*.txt.

Format is deliberately forgiving:

    # title: The Clock Cabinet          (optional header)
    # source_url: https://...               (optional header)
    FINCH: That's what fog choir practice is for.
    [stage direction lines in brackets are ignored]
    A line with no speaker is kept with speaker = null.

This is also the format for hand-corrected transcripts.
"""

from __future__ import annotations

import re
from pathlib import Path

from archive.models import Episode, Line, parse_episode_code
from archive.normalize import clean_markup, split_speaker
from archive.sources.base import TranscriptSource

_DIRECTION_RE = re.compile(r"^\s*[\[(].*[\])]\s*$")


def parse_transcript_lines(raw_lines: list[str]) -> list[Line]:
    lines: list[Line] = []
    for raw in raw_lines:
        text = clean_markup(raw)
        if not text or _DIRECTION_RE.match(text):
            continue
        speaker, spoken = split_speaker(text)
        if spoken:
            lines.append(Line(spoken, len(lines) + 1, speaker))
    return lines


class PlainTextSource(TranscriptSource):
    name = "text"

    def __init__(self, path: str | Path, label: str | None = None):
        self.root = Path(path)
        self.label = label or "text"
        self._files: dict[tuple[int, int], Path] = {}
        for f in sorted(self.root.rglob("*.txt")):
            code = parse_episode_code(f.name)
            if code:
                self._files[code] = f

    def available(self) -> list[tuple[int, int]]:
        return sorted(self._files)

    def load(self, season: int, episode: int) -> Episode | None:
        f = self._files.get((season, episode))
        if not f:
            return None
        meta: dict[str, str] = {}
        body: list[str] = []
        for ln in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^#\s*(\w+)\s*:\s*(.*)$", ln)
            if m and not body:
                meta[m.group(1).lower()] = m.group(2).strip()
            else:
                body.append(ln)
        return Episode(season, episode, title=meta.get("title"), source=self.label,
                       source_url=meta.get("source_url") or f.as_uri(), lines=parse_transcript_lines(body))
