"""Speech-to-text timing with Whisper, for episodes that have no usable subtitles.

Runs entirely on the local machine via faster-whisper (optional dependency):

    pip install faster-whisper

Whisper's own words are only used as a *timing layer*: the output is written as an
ordinary .srt and imported like any other subtitle file, then the transcript text is
aligned to it. Timestamps therefore match the user's exact video files.

Whisper segments often run several sentences (and speakers) together, so segments
are re-cut at sentence boundaries using word-level timestamps, giving cues roughly
the size of one transcript line.
"""

from __future__ import annotations

from pathlib import Path

from archive.subtitles import format_timestamp

DEFAULT_MODEL = "small.en"  # "base.en" is ~2x faster, "medium.en" more accurate; GPU makes any of them fast
MAX_CUE_MS = 7000
_SENTENCE_END = (".", "?", "!")


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _srt_time(ms: int) -> str:
    return format_timestamp(ms, with_ms=True).replace(".", ",")


def words_to_cues(words: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Group (start_ms, end_ms, word) into sentence-sized cues."""
    cues, cur = [], []
    for w in words:
        if cur and (w[0] - cur[-1][1] > 1200 or w[1] - cur[0][0] > MAX_CUE_MS):
            cues.append(cur)  # long pause or overlong cue: break here
            cur = []
        cur.append(w)
        if w[2].rstrip('"\'').endswith(_SENTENCE_END):
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    return [(c[0][0], c[-1][1], " ".join(x[2] for x in c).strip()) for c in cues if c]


def write_srt(cues: list[tuple[int, int, str]], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    blocks = [f"{i}\n{_srt_time(s)} --> {_srt_time(e)}\n{t}\n" for i, (s, e, t) in enumerate(cues, 1)]
    tmp = out.with_suffix(".tmp")
    tmp.write_text("\n".join(blocks), encoding="utf-8")
    tmp.replace(out)
    return out


_MODELS: dict = {}


def transcribe(video: Path, out: Path, model_name: str = DEFAULT_MODEL, device: str = "auto",
               log=print) -> Path:
    """Transcribe a video's audio to an .srt at `out`. Reuses `out` if it already exists."""
    if out.exists() and out.stat().st_size > 0:
        return out
    if not available():
        raise RuntimeError("faster-whisper is not installed: pip install faster-whisper")
    from faster_whisper import WhisperModel

    key = (model_name, device)
    if key not in _MODELS:
        compute = "int8" if device in ("cpu", "auto") else "float16"
        _MODELS[key] = WhisperModel(model_name, device=device, compute_type=compute)
    model = _MODELS[key]
    segments, info = model.transcribe(str(video), language="en", word_timestamps=True, vad_filter=True,
                                      condition_on_previous_text=False)
    words: list[tuple[int, int, str]] = []
    for seg in segments:
        for w in seg.words or []:
            words.append((round(w.start * 1000), round(w.end * 1000), w.word.strip()))
    cues = words_to_cues(words)
    log(f"   whisper {model_name}: {len(cues)} cues from {info.duration / 60:.1f} min of audio")
    return write_srt(cues, out)
