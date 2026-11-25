"""Build the public static site: HTML/JS/CSS + per-season dialogue JSON. No server needed.

    python -m archive export-static --out site --image-base https://images.example.com/

Deploy `site/` to any static host (Cloudflare Pages, Netlify, GitHub Pages). Images are
served from `--image-base` (e.g. a Cloudflare R2 bucket), so the site stays small.
Only lines' primary images are referenced.
"""

from __future__ import annotations

import filecmp
import json
import shutil
import sqlite3
from pathlib import Path

from archive import SERIES
from archive.config import ROOT, Config
from archive.search import speakers

STATIC = ROOT / "web" / "static"
TIMING_CODES = {"none": 0, "subtitle": 1, "aligned": 2, "manual": 3}


def export_share_pages(conn: sqlite3.Connection, out: Path, image_base: str, site_url: str,
                       share_base: str, log=print, *, series: str = SERIES) -> int:
    """One permalink page per primary screengrab, written to OUT/<key>.html. Unchanged files are skipped."""
    from archive.share import render_share_page, share_key

    out.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT d.speaker, d.text, d.start_ms, e.episode_code, e.title, i.filename AS image, "
        "i.width AS image_width, i.height AS image_height FROM images i "
        "JOIN dialogue d ON d.id = i.dialogue_id JOIN episodes e ON e.id = d.episode_id WHERE i.is_primary = 1"
    ).fetchall()
    written = 0
    for r in rows:
        line = dict(r)
        key = share_key(line["image"])
        page = render_share_page(line, image_base + line["image"], site_url, f"{share_base}{key}.html", series)
        path = out / f"{key}.html"
        if not path.exists() or path.read_text(encoding="utf-8") != page:
            path.write_text(page, encoding="utf-8")
            written += 1
    log(f"share pages: {len(rows)} total, {written} new/changed, in {out}")
    return len(rows)


def export_static(conn: sqlite3.Connection, cfg: Config, out: Path, image_base: str = "images/",
                  copy_images: bool = False, site_url: str | None = None, share_base: str | None = None,
                  share_out: Path | None = None, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    (out / "static").mkdir(exist_ok=True)

    eps = conn.execute(
        "SELECT e.id, e.episode_code, e.season, e.episode, e.title FROM episodes e "
        "WHERE EXISTS (SELECT 1 FROM dialogue d WHERE d.episode_id = e.id) ORDER BY e.season, e.episode"
    ).fetchall()
    season_files, total_lines, total_images = [], 0, 0
    for season in sorted({e["season"] for e in eps}):
        data = {"episodes": {}}
        for e in (x for x in eps if x["season"] == season):
            rows = conn.execute(
                "SELECT d.id, d.sequence, d.speaker, d.text, d.start_ms, d.end_ms, d.timing_source, "
                "d.timing_confidence, i.filename FROM dialogue d "
                "LEFT JOIN images i ON i.dialogue_id = d.id AND i.is_primary = 1 "
                "WHERE d.episode_id = ? ORDER BY d.sequence", [e["id"]]
            ).fetchall()
            data["episodes"][e["episode_code"]] = [
                [r[0], r[1], r[2], r[3], r[4], r[5], TIMING_CODES.get(r[6], 0),
                 round(r[7], 2) if r[7] is not None else None, r[8]] for r in rows
            ]
            total_lines += len(rows)
            total_images += sum(1 for r in rows if r[8])
        name = f"data/S{season:02d}.json"
        (out / name).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        season_files.append(name.removeprefix("data/"))

    meta = {
        "series": cfg.series,
        "season_files": season_files,
        "episodes": [{"episode_code": e["episode_code"], "season": e["season"], "episode": e["episode"],
                      "title": e["title"]} for e in eps],
        "speakers": speakers(conn, min_lines=5),
        "lines": total_lines,
        "images": total_images,
    }
    (out / "data" / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    for f in ("style.css", "app.js", "engine.js"):
        shutil.copy2(STATIC / f, out / "static" / f)
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/', 'href="static/').replace('src="/static/app.js"', 'src="static/app.js"')
    html = html.replace('<h1><a href="/">', '<h1><a href="./">')
    config = json.dumps({"mode": "static", "dataBase": "data/", "imageBase": image_base,
                         "siteUrl": site_url or "", "shareBase": share_base or ""}).replace("<", "\\u003c")
    html = html.replace('<script src="static/app.js"></script>',
                        f'<script>window.ARCHIVE_CONFIG = {config};</script>\n'
                        '  <script src="static/engine.js"></script>\n  <script src="static/app.js"></script>')
    (out / "index.html").write_text(html, encoding="utf-8")

    if copy_images:
        n = 0
        for (rel,) in conn.execute("SELECT filename FROM images WHERE is_primary = 1"):
            src, dst = cfg.images_dir / rel, out / "images" / rel
            if src.exists() and not (dst.exists() and filecmp.cmp(src, dst, shallow=False)):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                n += 1
        log(f"copied {n} images into {out / 'images'}")

    if share_out is not None:
        if not (site_url and share_base):
            raise SystemExit("share pages need --site-url and --share-base (absolute https:// URLs)")
        export_share_pages(conn, share_out, image_base, site_url, share_base, log=log, series=cfg.series)

    size = sum(p.stat().st_size for p in (out / "data").glob("*.json"))
    log(f"static site written to {out}: {len(eps)} episodes, {total_lines} lines, {total_images} images, "
        f"{size / 1e6:.1f} MB of dialogue data (compressed by the host to roughly a third)")
    return meta
