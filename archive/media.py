"""Discover episode video files on disk and report what is inside them.

Files are matched to episodes by, in order:
  1. an SxxEyy / 1x01 code in the file name,
  2. a season number from the folder ("Season 1", "S01") plus the episode title in the file name,
  3. the episode title alone, if it is unambiguous.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from archive.models import episode_code, parse_episode_code
from archive.normalize import normalize
from archive.video import VIDEO_EXTENSIONS, require_ffmpeg

TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
IMAGE_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
SIDECAR_EXTENSIONS = (".srt", ".vtt")


@dataclass
class SubStream:
    index: int          # position among subtitle streams (for -map 0:s:N)
    codec: str
    language: str | None
    title: str | None
    forced: bool
    hearing_impaired: bool

    @property
    def text_based(self) -> bool:
        return self.codec in TEXT_SUB_CODECS


@dataclass
class MediaFile:
    path: Path
    code: str | None = None
    match: str = "unmatched"   # code | title+season | title | unmatched
    duration_ms: int | None = None
    video: str | None = None   # e.g. "h264 1920x1080"
    audio: list[str] = field(default_factory=list)
    subtitles: list[SubStream] = field(default_factory=list)
    sidecars: list[Path] = field(default_factory=list)
    error: str | None = None

    def best_text_subtitle(self) -> SubStream | None:
        """English, full (not forced) text track; SDH is fine since cues are cleaned."""
        text = [s for s in self.subtitles if s.text_based and not s.forced]
        eng = [s for s in text if (s.language or "eng").lower() in ("eng", "en", "und")]
        return (eng or text or [None])[0]


def probe_media(path: Path) -> dict:
    require_ffmpeg()
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=index,codec_type,codec_name,width,height,channels:stream_tags=language,title"
         ":stream_disposition=forced,hearing_impaired", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _title_candidates(episodes: list[dict], name: str, season: int | None) -> list[tuple[float, dict]]:
    stem = normalize(re.sub(r"(?i)\b(720|1080|2160)p\b|\bx26[45]\b|\bweb[- ]?dl\b|\bbluray\b|\bdvdrip\b", " ", name))
    scored = []
    for e in episodes:
        if season is not None and e["season"] != season:
            continue
        title = normalize(e.get("title") or "")
        if title:
            scored.append((fuzz.partial_ratio(title, stem) if len(title) <= len(stem) else fuzz.ratio(title, stem), e))
    return sorted(scored, key=lambda x: -x[0])


def match_file(path: Path, episodes: list[dict]) -> tuple[str | None, str]:
    code = parse_episode_code(path.name)
    if code:
        return episode_code(*code), "code"
    season = None
    for part in reversed(path.parent.parts):
        m = re.fullmatch(r"(?i)(?:season|series|s)[ ._-]*(\d{1,2})", part.strip())
        if m:
            season = int(m.group(1))
            break
    cands = _title_candidates(episodes, path.stem, season)
    if cands and cands[0][0] >= 90 and (len(cands) == 1 or cands[0][0] - cands[1][0] >= 5):
        e = cands[0][1]
        return e["episode_code"], "title+season" if season is not None else "title"
    return None, "unmatched"


def discover(root: Path, episodes: list[dict], probe: bool = True) -> list[MediaFile]:
    files = [root] if root.is_file() else sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS and not p.name.startswith("."))
    out = []
    for f in files:
        mf = MediaFile(f)
        mf.code, mf.match = match_file(f, episodes)
        mf.sidecars = sorted(
            p for p in f.parent.iterdir()
            if p.is_file() and p.suffix.lower() in SIDECAR_EXTENSIONS and (
                p.stem == f.stem or p.stem.startswith(f.stem + ".") or
               (mf.code and parse_episode_code(p.name) == parse_episode_code(mf.code)))
        )
        if probe:
            try:
                data = probe_media(f)
                dur = data.get("format", {}).get("duration")
                mf.duration_ms = round(float(dur) * 1000) if dur else None
                sub_i = 0
                for s in data.get("streams", []):
                    tags, disp = s.get("tags", {}), s.get("disposition", {})
                    if s.get("codec_type") == "video" and mf.video is None:
                        mf.video = f"{s.get('codec_name')} {s.get('width')}x{s.get('height')}"
                    elif s.get("codec_type") == "audio":
                        mf.audio.append(f"{s.get('codec_name')} {tags.get('language', '?')}")
                    elif s.get("codec_type") == "subtitle":
                        mf.subtitles.append(SubStream(sub_i, s.get("codec_name", "?"), tags.get("language"),
                                                      tags.get("title"), bool(disp.get("forced")),
                                                      bool(disp.get("hearing_impaired"))))
                        sub_i += 1
            except Exception as e:  # corrupt file, not a video, etc.
                mf.error = str(e)[-200:]
        out.append(mf)
    return out


def report(files: list[MediaFile], episodes: list[dict]) -> str:
    """Human-readable summary: what was found and how each episode will get its timing."""
    lines = []
    by_code: dict[str, list[MediaFile]] = {}
    for mf in files:
        subs = ", ".join(f"{s.codec}/{s.language or '?'}{'/forced' if s.forced else ''}{'/SDH' if s.hearing_impaired else ''}"
                         for s in mf.subtitles) or "none"
        timing = ("embedded subtitles" if mf.best_text_subtitle() else
                  "sidecar .srt/.vtt" if mf.sidecars else
                  "image subtitles only (needs Whisper)" if mf.subtitles else "Whisper")
        lines.append(f"{mf.code or '??????'}  [{mf.match:<12}] {mf.path.name}\n"
                     f"         {mf.video or '?'} | {(mf.duration_ms or 0) / 60000:.1f} min | audio: {', '.join(mf.audio) or '?'}\n"
                     f"         subtitles: {subs} | sidecars: {len(mf.sidecars)} | timing via: {timing}"
                     + (f"\n         ERROR: {mf.error}" if mf.error else ""))
        if mf.code:
            by_code.setdefault(mf.code, []).append(mf)
    known = {e["episode_code"] for e in episodes}
    missing = sorted(known - set(by_code))
    dupes = {c: v for c, v in by_code.items() if len(v) > 1}
    unmatched = [mf for mf in files if not mf.code]
    exts = {}
    for mf in files:
        exts[mf.path.suffix.lower()] = exts.get(mf.path.suffix.lower(), 0) + 1
    timing_counts = {"embedded": sum(1 for f in files if f.best_text_subtitle()),
                     "sidecar": sum(1 for f in files if not f.best_text_subtitle() and f.sidecars),
                     "whisper": sum(1 for f in files if not f.best_text_subtitle() and not f.sidecars)}
    lines += [
        "",
        f"files: {len(files)}  ({', '.join(f'{k} x{v}' for k, v in sorted(exts.items()))})",
        f"matched episodes: {len(by_code)}   unmatched files: {len(unmatched)}   duplicates: {len(dupes)}",
        f"timing source: embedded subs {timing_counts['embedded']}, sidecar files {timing_counts['sidecar']}, "
        f"Whisper needed {timing_counts['whisper']}",
    ]
    if missing:
        lines.append(f"episodes with no file: {', '.join(missing[:40])}{' …' if len(missing) > 40 else ''}")
    for c, v in dupes.items():
        lines.append(f"duplicate {c}: " + " | ".join(m.path.name for m in v))
    return "\n".join(lines)
