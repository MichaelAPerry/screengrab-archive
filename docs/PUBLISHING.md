# Publishing a static archive

Generate the site from the database for your selected show:

```sh
python -m archive export-static --out site --copy-images
python -m http.server 8080 --bind 127.0.0.1 --directory site
```

Test the search and frame images at `http://127.0.0.1:8080/`. Export to a fresh directory when replacing an earlier build so the deployed files reflect the current database. Deploy the output directory as the static site's root. For GitHub Pages served from a branch, you can export into that branch's `docs/` folder and select it as the Pages source. This repository's `docs/` folder already serves the synthetic demo.

For separately hosted frames, omit `--copy-images` and pass an absolute `--image-base https://images.example.com/` URL. Upload the extracted files under the same relative paths. To create Open Graph preview pages, pass `--site-url https://archive.example.com/` and `--share-base https://images.example.com/q/` with that absolute image base. Upload the pages from `--share-out` (default `share/`) to the share URL prefix. Allow the site origin to read images if you want browser-side caption rendering from another origin.

The exported dialogue is readable by visitors in `data/*.json`. Publish only material you have permission to distribute.

## Rebuild the demo

From the checkout with the README dependencies installed:

```sh
python -m archive demo --out docs
```

The command generates a silent 96-second, 480×270 FFmpeg test-pattern video in a temporary directory. The original SRT and episode manifest in [scripts/demo_data](../scripts/demo_data/) supply the show title, two speaker labels, and 24 timed lines. The standard `process-all` pipeline reads the sidecar and grabs the midpoint frames. The `export-static` implementation writes the site and its WebPs into `docs/`. The temporary video and database are removed when the build finishes.
