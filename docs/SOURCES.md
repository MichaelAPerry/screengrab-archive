# Local dialogue sources

Embedded text subtitles and SRT/VTT sidecars supply both timing and searchable dialogue. Speaker labels such as `MICA: The moon has returned our toaster.` are recognized when present. Whisper can generate cues from local audio for episodes without subtitles.

To add speaker names or use a corrected transcript, provide one `.txt` file per episode with a code in the filename, such as `S01E01.txt`. Use one turn per line and an optional speaker prefix. An optional `# title:` header sets the episode title.

```sh
python -m archive import-transcripts --path ./data/my-show/transcripts
```

The saved-page adapter can also parse local Forever Dreaming HTML topic pages you have permission to use. The episode code comes from the page title or filename.

```sh
python -m archive import-transcripts --source foreverdreaming --path ./data/my-show/saved-pages
```

All transcript adapters read local files. Dialogue from a transcript is aligned to available cues. See the [pipeline design](PIPELINE.md) for timing provenance and scoring.
