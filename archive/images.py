"""Frame storage, perceptual hashing and pluggable quality scoring.

Scorers are plain functions `Image -> float in [0, 1]`. V1 ships two cheap ones
(sharpness, exposure). Face detection, subtitle-obstruction and similar checks can be
added later by registering more functions in SCORERS; nothing else has to change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PIL import Image, ImageFilter, ImageStat

Scorer = Callable[[Image.Image], float]


def image_relpath(code: str, timestamp_ms: int, ext: str = "webp") -> str:
    """Stable name derived from episode + timestamp, e.g. S04/S04E08/S04E08_000872345.webp."""
    code = code.upper()
    return f"{code[:3]}/{code}/{code}_{timestamp_ms:09d}.{ext}"


def save_webp(img: Image.Image, path: Path, quality: int = 80) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    img.save(tmp, "WEBP", quality=quality, method=4)
    tmp.replace(path)


def dhash(img: Image.Image, size: int = 8) -> str:
    """64-bit difference hash; near-identical frames have a small Hamming distance."""
    small = img.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = small.tobytes()
    bits = 0
    for row in range(size):
        for col in range(size):
            left, right = px[row * (size + 1) + col], px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return f"{bits:0{size * size // 4}x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def is_near_duplicate(h: str, others: list[str], threshold: int = 6) -> bool:
    return any(hamming(h, o) <= threshold for o in others)


def sharpness_score(img: Image.Image) -> float:
    """Edge-energy proxy for focus / motion blur (variance of an edge-filtered greyscale)."""
    grey = img.convert("L")
    grey.thumbnail((480, 480))
    var = ImageStat.Stat(grey.filter(ImageFilter.FIND_EDGES)).var[0]
    return min(var / 1500.0, 1.0)


def exposure_score(img: Image.Image) -> float:
    """1.0 for mid-tone frames, falling towards 0 for black/blown-out frames."""
    mean = ImageStat.Stat(img.convert("L")).mean[0]
    return max(0.0, 1.0 - abs(mean - 118) / 118)


SCORERS: dict[str, Scorer] = {
    "sharpness": sharpness_score,
    "exposure": exposure_score,
}


def score_image(img: Image.Image) -> tuple[float, dict[str, float]]:
    scores = {name: round(fn(img), 4) for name, fn in SCORERS.items()}
    return round(sum(scores.values()) / len(scores), 4), scores
