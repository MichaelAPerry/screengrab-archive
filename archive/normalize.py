"""Text normalisation used for search, alignment and deduplication."""

from __future__ import annotations

import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")  # HTML-ish and ASS override tags
_SPEAKER_RE = re.compile(r"^\s*([A-Z][A-Za-z .'\-]{0,30}?)\s*:\s+(?=\S)")
_BRACKETS_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)")  # [laughs], (sighs)
_PUNCT_RE = re.compile(r"[^\w\s']+")
_WS_RE = re.compile(r"\s+")

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "—": " - ", "–": "-", "…": "..."})


def clean_markup(text: str) -> str:
    """Strip subtitle formatting while keeping the spoken words."""
    text = _TAG_RE.sub("", text).translate(_QUOTES)
    text = text.replace("♪", " ").replace("#", " ")  # music notes
    return _WS_RE.sub(" ", text).strip()


def split_speaker(text: str) -> tuple[str | None, str]:
    """'FINCH: Fog choir.' -> ('FINCH', 'Fog choir.'). Leaves normal sentences alone."""
    m = _SPEAKER_RE.match(text)
    if not m:
        return None, text.strip()
    name = m.group(1).strip()
    # Reject things like "Note: ..." false positives only if clearly sentence-ish
    if len(name.split()) > 3:
        return None, text.strip()
    return name.upper(), text[m.end():].strip()


def normalize(text: str) -> str:
    """Lowercase, drop punctuation / stage directions, collapse whitespace."""
    text = unicodedata.normalize("NFKC", clean_markup(text)).lower()
    text = _BRACKETS_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text).replace("'", "")
    return _WS_RE.sub(" ", text).strip()


def tokens(text: str) -> list[str]:
    return normalize(text).split()
