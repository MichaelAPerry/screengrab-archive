"""SRT / WebVTT parsing into timed cues, plus extraction of subtitle tracks from local video."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from archive.models import Cue, Episode, Line, Origin, TimingSource
from archive.normalize import clean_markup, split_speaker

_TIME_RE = re.compile(
    r"(?P<a>(?:\d+:)?\d{1,2}:\d{2}[,.]\d{1,3})\s*-->\s*(?P<b>(?:\d+:)?\d{1,2}:\d{2}[,.]\d{1,3})"
)
_DASH_SPLIT_RE = re.compile(r"(?:^|\s)[-‐-—]\s+(?=\S)")
_NOISE_RE = re.compile(r"^\s*(?:[\[(][^\])]*[\])]\s*)+$")  # cue is only "[laughter]" etc.
_CREDIT_RE = re.compile(r"(?i)subtitles? by|synced? (?:and|&) corrected|opensubtitles|addic7ed|www\.")


def parse_timestamp(value: str) -> int:
    """'00:12:34,100' / '12:34.1' / '1:02:03.5' -> milliseconds."""
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    secs = float(parts[-1])
    mins = int(parts[-2]) if len(parts) >= 2 else 0
    hours = int(parts[-3]) if len(parts) >= 3 else 0
    return round(((hours * 60 + mins) * 60 + secs) * 1000)


def format_timestamp(ms: int | None, with_ms: bool = False) -> str:
    if ms is None:
        return ""
    s, rem = divmod(int(ms), 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" + (f".{rem:03d}" if with_ms else "")


def parse_subtitles(text: str) -> list[Cue]:
    """Parse SRT or WebVTT text. Tolerates missing indices, BOMs and CRLF."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            m = _TIME_RE.search(ln)
            if m:
                body = lines[i + 1:]
                break
        else:
            continue  # WEBVTT header, NOTE, STYLE blocks etc.
        start, end = parse_timestamp(m.group("a")), parse_timestamp(m.group("b"))
        raw = " ".join(clean_markup(b) for b in body)
        if not raw or _NOISE_RE.match(raw) or _CREDIT_RE.search(raw):
            continue
        speaker, spoken = split_speaker(raw)
        cues.append(Cue(sequence=len(cues) + 1, start=start, end=max(end, start), text=spoken, speaker=speaker))
    return cues


def split_dialogue_turns(cue: Cue) -> list[Cue]:
    """A cue like '- Are we cool? - We're cool!' holds two speakers' turns.

    Split it and share the interval between the parts in proportion to text length,
    so each turn gets its own (approximate) timing.
    """
    dashed = cue.text.lstrip()[:1] in "-\u2010\u2011\u2012\u2013\u2014"
    parts = [p.strip() for p in _DASH_SPLIT_RE.split(cue.text) if p.strip()] if dashed else [cue.text]
    if len(parts) <= 1:
        return [Cue(cue.sequence, cue.start, cue.end, cue.text.lstrip("- ").strip(), cue.speaker)]
    total = sum(len(p) for p in parts)
    out, t = [], cue.start
    for p in parts:
        dur = (cue.end - cue.start) * len(p) / total
        speaker, spoken = split_speaker(p)
        out.append(Cue(cue.sequence, round(t), round(t + dur), spoken, speaker))
        t += dur
    return out


def load_subtitle_file(path: Path) -> list[Cue]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return parse_subtitles(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    return []


def cues_to_episode(season: int, episode: int, cues: list[Cue], source: str, title: str | None = None) -> Episode:
    """Subtitle-only dialogue: each (split) cue becomes a timed line."""
    ep = Episode(season, episode, title=title, source=source, origin=Origin.SUBTITLE)
    for cue in cues:
        for part in split_dialogue_turns(cue):
            ep.lines.append(
                Line(part.text, len(ep.lines) + 1, part.speaker, part.start, part.end,
                     TimingSource.SUBTITLE, 1.0)
            )
    return ep


def extract_embedded_subtitles(video: Path, out: Path, stream: int = 0) -> Path | None:
    """Pull a text subtitle track (e.g. from an MKV) out of a local video with FFmpeg.

    Subtitles embedded in the viewer's own file are the best timing source: they are
    synced to exactly that cut of the episode. Image-based tracks (PGS/VobSub) cannot
    be converted without OCR and are skipped.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(video), "-map", f"0:s:{stream}", str(out)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        return None
    return out
