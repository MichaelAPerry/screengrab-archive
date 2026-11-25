"""Local video lookup and FFmpeg frame extraction. Video files never leave the user's machine."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from archive.models import parse_episode_code

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts")

#: Fractions of a dialogue interval to sample. V1 uses only the midpoint by default.
MIDPOINT = (0.5,)
CANDIDATES = (0.25, 0.5, 0.75)


class FFmpegMissing(RuntimeError):
    pass


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise FFmpegMissing(f"{tool} not found on PATH; install FFmpeg (https://ffmpeg.org/download.html)")


def find_video(media_dir: Path, code: str) -> Path | None:
    """Map 'S01E01' to a file: media/S01/S01E01.mp4 first, then any file whose name has the code."""
    code = code.upper()
    for ext in VIDEO_EXTENSIONS:
        p = media_dir / code[:3] / f"{code}{ext}"
        if p.exists():
            return p
    target = parse_episode_code(code)
    if media_dir.exists():
        for p in sorted(media_dir.rglob("*")):
            if p.suffix.lower() in VIDEO_EXTENSIONS and parse_episode_code(p.name) == target:
                return p
    return None


def probe(path: Path) -> dict:
    """Duration (ms) and stream summary via ffprobe."""
    require_ffmpeg()
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=index,codec_type,codec_name:stream_tags=language",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    dur = data.get("format", {}).get("duration")
    return {
        "duration_ms": round(float(dur) * 1000) if dur else None,
        "subtitle_streams": [s for s in data.get("streams", []) if s.get("codec_type") == "subtitle"],
    }


def candidate_timestamps(start_ms: int, end_ms: int, fractions=MIDPOINT, offset_ms: int = 0) -> list[int]:
    """Points inside [start, end] to sample, e.g. 12:34.100-12:37.500 @0.5 -> 12:35.800."""
    span = max(end_ms - start_ms, 0)
    return sorted({max(0, round(start_ms + span * f) + offset_ms) for f in fractions})


def grab_frame(video: Path, timestamp_ms: int, max_width: int | None = 1280) -> Image.Image:
    """Decode the frame at timestamp_ms. Input seeking (-ss before -i) is fast and frame-accurate."""
    require_ffmpeg()
    vf = ["-vf", f"scale='min({max_width},iw)':-2"] if max_width else []
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{timestamp_ms / 1000:.3f}", "-i", str(video),
         "-frames:v", "1", *vf, "-f", "image2pipe", "-vcodec", "png", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg could not extract {timestamp_ms} ms from {video}: {proc.stderr.decode()[-300:]}")
    img = Image.open(io.BytesIO(proc.stdout))
    img.load()
    return img.convert("RGB")
