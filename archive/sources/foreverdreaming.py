"""Import local Forever Dreaming topic pages by the 01x01 code in the title."""

from __future__ import annotations

import html
import re
from pathlib import Path

from archive.models import Episode, parse_episode_code
from archive.sources.base import TranscriptSource
from archive.sources.plaintext import parse_transcript_lines

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_POST_RE = re.compile(r'<div class="content"[^>]*>(.*?)</div>', re.S | re.I)
_CANONICAL_RE = re.compile(r'<link rel="canonical" href="([^"]+)"', re.I)


def parse_topic_html(raw: str) -> tuple[str | None, str | None, list[str]]:
    """Return (page_title, canonical_url, text_lines) from a saved phpBB topic page."""
    title_m = _TITLE_RE.search(raw)
    title = html.unescape(title_m.group(1)).strip() if title_m else None
    url_m = _CANONICAL_RE.search(raw)
    post = _POST_RE.search(raw)
    body = post.group(1) if post else raw
    body = re.sub(r"<br\s*/?>|</p>", "\n", body, flags=re.I)
    body = html.unescape(re.sub(r"<[^>]+>", "", body))
    return title, url_m.group(1) if url_m else None, [ln.strip() for ln in body.split("\n")]


def _episode_title(page_title: str | None) -> str | None:
    # e.g. "01x01 - The Clock Cabinet - Forever Dreaming"
    if not page_title:
        return None
    m = re.search(r"\d{1,2}x\d{2}\s*-\s*(.+?)(?:\s+-\s+(?:Forever Dreaming|Transcripts).*)?$", page_title)
    return m.group(1).strip() if m else None


class ForeverDreamingSource(TranscriptSource):
    name = "foreverdreaming"

    def __init__(self, path: str | Path):
        self.root = Path(path)
        self._files: dict[tuple[int, int], Path] = {}
        for f in sorted(list(self.root.rglob("*.htm")) + list(self.root.rglob("*.html"))):
            head = f.read_text(encoding="utf-8", errors="replace")[:4000]
            m = _TITLE_RE.search(head)
            code = parse_episode_code(m.group(1) if m else "") or parse_episode_code(f.name)
            if code:
                self._files[code] = f

    def available(self) -> list[tuple[int, int]]:
        return sorted(self._files)

    def load(self, season: int, episode: int) -> Episode | None:
        f = self._files.get((season, episode))
        if not f:
            return None
        title, url, text = parse_topic_html(f.read_text(encoding="utf-8", errors="replace"))
        return Episode(season, episode, title=_episode_title(title), source=self.name,
                       source_url=url or f.resolve().as_uri(), lines=parse_transcript_lines(text))
