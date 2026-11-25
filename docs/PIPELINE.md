# Pipeline: text → match → timestamp → video frame → image → search result

```
 transcript source ──► dialogue (speaker, text, origin=transcript, timing=none)
                              │
 subtitles (.srt/.vtt/embedded/Whisper) ──► subtitle_cues ──► fuzzy alignment ──► dialogue.start_ms/end_ms
                              │                                            timing=aligned, confidence
 (no transcript?) cues become the dialogue directly ─────────────────────► timing=subtitle
                              │
 local video (data/show/media/S01/S01E01.mp4) ──► FFmpeg seek to candidate timestamps ──► Pillow ──► WebP
                              │                                             data/show/images/S01/S01E01/…webp
                              ▼
                     SQLite FTS5 search ──► FastAPI JSON ──► vanilla JS UI
```

## 1. Dialogue and timing provenance

Every dialogue row carries two independent labels:

* `origin`: `transcript` (a transcript source) or `subtitle` (the line *is* a subtitle cue).
* `timing_source`:
  * `none`: transcript-only, no timing known
  * `subtitle`: timing copied directly from the cue the line came from
  * `aligned`: transcript line fuzzily matched to cue(s); `timing_confidence` holds the 0–1 similarity
  * `manual`: a human set or checked it. Re-alignment **never** overwrites manual timing.

Raw cues are stored in `subtitle_cues` and never edited, so alignment can be re-run whenever
the algorithm or the transcript improves (`python -m archive align`).

## 1b. Whisper timing (`archive/transcribe.py`)

When an episode has no subtitle track and no sidecar file, faster-whisper transcribes the audio
locally with word timestamps, re-cut into sentence-sized cues. The result is saved as
`data/show/raw/whisper/<model>/SxxEyy.srt` and imported like any subtitle file (source
`whisper:<model>`); the transcript text is then aligned to it. Whisper's own wording is only used
for timing, and when there is no transcript at all it becomes the searchable dialogue.

Background music and overlapping speech can affect Whisper timing.

## 2. Alignment (`archive/alignment.py`)

1. Clean both sides: strip `<i>`/ASS tags, music notes and `[LAUGHS]`-only cues; drop credits;
   split `- A - B` cues into separate turns and divide the cue's time between them by text length.
2. Normalise: NFKC, lowercase, remove bracketed directions and punctuation, fold apostrophes,
   collapse whitespace.
3. Run a monotonic dynamic-programming alignment (like `diff`) inside a diagonal band.
   * One line can match 1–3 consecutive cue parts, or 2–3 short lines can match one cue.
   * Either side may skip items (lines missing from the subtitles and vice versa).
   * Similarity = max(`ratio`, `token_set_ratio` × length balance) from rapidfuzz, threshold 0.65.
4. Keep each match's similarity as `timing_confidence`.

## 3. Frame extraction (`archive/video.py`, `archive/pipeline.py`)

* **Video lookup:** `media/S01/S01E01.<mkv|mp4|…>` first, then any file under `media/` whose name
  contains the code. `index-video` records the resolved path and duration from ffprobe.
  Video is never copied or committed.
* **Candidate timestamps** are computed from the interval, e.g. 12:34.100–12:37.500:
  * 25% → 12:34.950, **50% → 12:35.800 (V1 default)**, 75% → 12:36.650
  * plus the episode's `timing_offset_ms`.
* **Decode:** `ffmpeg -ss <t> -i video -frames:v 1 -vf scale='min(1280,iw)':-2 -f image2pipe -vcodec png -`.
  Seeking before `-i` is fast and frame-accurate with modern FFmpeg. The frame is piped as a
  lossless PNG straight into Pillow; no intermediate files.
* **Encode:** WebP, quality 80, into `data/show/images/S01/S01E01/S01E01_<ms>.webp`. Names come from the
  episode and timestamp, **not** from a database id, so they survive re-imports, and an existing
  file is reused instead of re-decoded. The original frame is burned with nothing: no captions.
* **Dedup:** 64-bit difference hash per frame. Candidates for the same line within Hamming
  distance 6 are skipped.
* **Scoring:** `archive/images.py` has a `SCORERS` registry of `Image → [0,1]` functions. V1 has
  `sharpness` (edge-energy variance, a stand-in for blur) and `exposure`. Face detection, subtitle
  obstruction and cross-line duplicate checks can be added there later without schema changes;
  per-scorer values are stored in `images.scores_json`.
* **Primary image:** midpoint by default; with `--candidates`, the best-scoring distinct frame.
  A line can own any number of images (`images.dialogue_id`). `is_primary` picks the one shown in results.

Throughput: one FFmpeg process per frame, about 0.1–0.3 s each, so a few minutes per episode
at midpoint-only. Batching many timestamps into one decode pass is an easy later optimisation.

## 4. Search (`archive/search.py`)

* FTS5 over `normalized_text` (porter stemming, diacritics removed), restricted to the dialogue
  column so a word like "mica" doesn't match lines just because Mica spoke them.
* Query syntax: words, `"phrases"`, `speaker:`/`who:`, `episode:S04E08`, `season:4`, combinable.
  All user terms are quoted before they reach FTS5, so the syntax can't be broken.
* Ranking: bm25 re-ranked with bonuses for an exact phrase in the line, the line being exactly
  the query, short lines, and the share of query terms present. If no line has every word, it
  falls back to any-word matching and says so.
* Context: lines within ±10 s of the selection when timed, else ±6 lines.

## 5. Static export

The same UI can load season JSON and search in the browser. The export contains searchable
dialogue, available speaker labels and episode details, and links to primary WebP frames.
Use `--copy-images` to include the referenced WebPs alongside the site.
