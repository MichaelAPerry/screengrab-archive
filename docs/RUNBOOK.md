# Process your own episodes

Install the dependencies and tools listed in the [README](../README.md). Choose a local folder for each show's database, images, timing caches, and manifest. The default is `data/show/`. Set `ARCHIVE_SHOW_DIR` to choose another folder and keep that setting for subsequent commands.

```sh
export ARCHIVE_SHOW_DIR=./data/my-show
python -m archive inspect-media ./media
python -m archive process-all ./media --episodes S01E01 --limit 20 --whisper never
python -m archive serve
```

Use episode codes such as `S01E01` or `1x01` in filenames. Processing registers the codes and generates `episodes.json` in the show's folder. Add a manifest before processing to provide titles or match videos by title. Use season folders such as `Season 1` to disambiguate similar titles.

```json
{
  "series": "My Show",
  "episodes": [
    {"season": 1, "episode": 1, "title": "Pilot"}
  ]
}
```

Save this shape as `episodes.json` in your show folder. Each successful processing run updates the manifest with progress and preserves configured titles. `python -m archive init` can seed the database before importing transcripts.

The first usable timing source is chosen in this order:

1. Cues already imported into the database.
2. An embedded text subtitle track. The engine prefers a full English track when available.
3. A local SRT or VTT sidecar. Use the same basename as the video or include its episode code.
4. A local Whisper transcription if enabled. Install it with `python -m pip install faster-whisper` and omit `--whisper never`. Model weights may be downloaded on first use.

Use subtitles from the same cut as the video. Matching timing matters when editions differ. For speaker names and stronger search text, see [local transcript imports](SOURCES.md).

Run `python -m archive process-all ./media` with the same options for the full collection after checking the pilot. The pipeline reuses existing timing and frames on subsequent runs. Use `stats` for counts, or `extract-frames --episode S01E01 --candidates` to sample three points in each interval when extracting new frames.

Individual paths can be overridden with `ARCHIVE_DB`, `ARCHIVE_MEDIA`, `ARCHIVE_IMAGES`, `ARCHIVE_RAW`, and `ARCHIVE_MANIFEST`. The local UI binds to loopback by default. `ARCHIVE_SERVE_VIDEO=1` enables the optional local episode player.
