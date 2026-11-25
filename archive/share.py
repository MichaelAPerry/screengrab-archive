"""Share permalinks: one tiny HTML page per screengrab with Open Graph / Twitter card tags.

Link-preview bots (X/Twitter, Discord, Slack, iMessage, Facebook, Reddit) don't run
JavaScript, so a search URL would unfurl as a generic page. Each shareable line gets
its own page whose meta tags name the screengrab and quote, and which forwards human
visitors to that quote on the main site.

Keys come from the image name (episode + timestamp, e.g. S06E05-872345), so they stay
stable across database rebuilds.
"""

from __future__ import annotations

import html
import re

from archive.subtitles import format_timestamp

_IMG_RE = re.compile(r"(S\d{2}E\d{2})_(\d+)\.\w+$")
_KEY_RE = re.compile(r"^(S\d{2}E\d{2})-(\d+)$")


def share_key(image_filename: str | None) -> str | None:
    """'S06/S06E05/S06E05_000872345.webp' -> 'S06E05-872345'."""
    m = _IMG_RE.search(image_filename or "")
    return f"{m.group(1)}-{int(m.group(2))}" if m else None


def parse_key(key: str) -> tuple[str, int] | None:
    m = _KEY_RE.match(key)
    return (m.group(1), int(m.group(2))) if m else None


def render_share_page(line: dict, image_url: str, site_url: str, page_url: str, series: str) -> str:
    """HTML with social-card meta tags that forwards browsers to site_url?id=<key>."""
    key = share_key(line["image"])
    speaker = (line.get("speaker") or "").title()
    quote = line["text"]
    title = f"“{quote}”" + (f" — {speaker}" if speaker else "")
    desc = f"{line['episode_code']} · {line.get('title') or ''}".strip(" ·")
    if line.get("start_ms") is not None:
        desc += f" · {format_timestamp(line['start_ms'])}"
    target = f"{site_url}?id={key}"
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(page_url)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{e(series)} Screengrab Archive">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(page_url)}">
<meta property="og:image" content="{e(image_url)}">
<meta property="og:image:alt" content="{e(title)}">
{f'<meta property="og:image:width" content="{line["image_width"]}"><meta property="og:image:height" content="{line["image_height"]}">' if line.get("image_width") else ""}
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(desc)}">
<meta name="twitter:image" content="{e(image_url)}">
<meta http-equiv="refresh" content="0; url={e(target)}">
<style>body{{font:16px system-ui,sans-serif;max-width:720px;margin:24px auto;padding:0 16px}}img{{width:100%;border-radius:8px}}</style>
</head><body>
<a href="{e(target)}"><img src="{e(image_url)}" alt="{e(title)}"></a>
<p><strong>{e(speaker)}</strong><br>{e(quote)}</p><p>{e(desc)}</p>
<p><a href="{e(target)}">Open in the archive</a></p>
<script>location.replace({target!r});</script>
</body></html>
"""
