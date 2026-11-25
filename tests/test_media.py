import json
import shutil
import subprocess
from pathlib import Path

import pytest

from archive.media import match_file
from archive.transcribe import words_to_cues, write_srt
from archive.subtitles import load_subtitle_file

EPISODES = [
    {"episode_code": "S01E01", "season": 1, "episode": 1, "title": "The Clock Cabinet"},
    {"episode_code": "S04E08", "season": 4, "episode": 8, "title": "The Porcelain Staircase"},
    {"episode_code": "S05E08", "season": 5, "episode": 8, "title": "Instructions for a Paper Moon"},
    {"episode_code": "S03E01", "season": 3, "episode": 1, "title": "The Cabinet Votes Twice"},
]


@pytest.mark.parametrize("path, code, how", [
    ("media/S01/S01E01.mkv", "S01E01", "code"),
    ("x/The Cabinet Hearing - 4x08 - Whatever.mp4", "S04E08", "code"),
    ("x/cabinet.s05e08.720p.bluray.x264.mkv", "S05E08", "code"),
    ("x/Season 5/08 - Instructions for a Paper Moon.mkv", "S05E08", "title+season"),
    ("x/Season 3/The Cabinet Votes Twice (1080p).mp4", "S03E01", "title+season"),
    ("x/The Clock Cabinet.avi", "S01E01", "title"),
    ("x/Season 4/Totally Unrelated Home Video.mp4", None, "unmatched"),
])
def test_match_file(path, code, how):
    assert match_file(Path(path), EPISODES) == (code, how)


def test_whisper_words_grouped_into_sentence_cues(tmp_path):
    words = [(0, 300, "That's"), (300, 600, "what"), (600, 900, "fog"), (900, 1200, "choir"), (1200, 1500, "is."),
             (1600, 1900, "Emergency"), (1900, 2400, "teacups!"),
             (6000, 6300, "Pause"), (6300, 6600, "then"), (6600, 6900, "more")]
    cues = words_to_cues(words)
    assert cues == [(0, 1500, "That's what fog choir is."), (1600, 2400, "Emergency teacups!"), (6000, 6900, "Pause then more")]
    parsed = load_subtitle_file(write_srt(cues, tmp_path / "S01E01.srt"))
    assert [(c.start, c.end, c.text) for c in parsed] == cues


def test_export_static(loaded, tmp_path):
    from archive.config import Config
    from archive.export import export_static

    cfg = Config(tmp_path / "x.db", tmp_path, tmp_path / "images", tmp_path, tmp_path / "m.json", False)
    meta = export_static(loaded, cfg, tmp_path / "site", image_base="https://img.example.com/", log=lambda *_: None)
    assert meta["lines"] == 16 and meta["season_files"] == ["S00.json"]
    html = (tmp_path / "site" / "index.html").read_text()
    assert '"imageBase": "https://img.example.com/"' in html and "static/engine.js" in html and 'href="/static' not in html
    rows = json.loads((tmp_path / "site" / "data" / "S00.json").read_text())["episodes"]["S00E01"]
    fog = next(r for r in rows if "fog choir" in r[3])
    assert fog[2] == "FINCH" and fog[4] == 17_800 and fog[6] == 2  # aligned


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_engine_matches_python_search(loaded, tmp_path):
    """The static site's JS engine should return the same lines as the SQLite search."""
    from archive.config import Config
    from archive.export import export_static
    from archive.search import search

    cfg = Config(tmp_path / "x.db", tmp_path, tmp_path / "images", tmp_path, tmp_path / "m.json", False)
    export_static(loaded, cfg, tmp_path / "site", log=lambda *_: None)
    queries = ['"fog choir"', "fog", "jars", "speaker:LUMEN", "speaker:FINCH fog", "thats what",
               "kettle season:0", "kettle episode:S00E01", "quill", "fog zzzunknown"]
    engine = Path(__file__).resolve().parent.parent / "web" / "static" / "engine.js"
    script = f"""
      const fs = require('fs'); global.window = {{}};
      global.fetch = async (u) => ({{ json: async () => JSON.parse(fs.readFileSync({json.dumps(str(tmp_path / 'site'))} + '/data/' + u)) }});
      eval(fs.readFileSync({json.dumps(str(engine))}, 'utf8'));
      (async () => {{
        const e = await new window.ArchiveEngine.StaticEngine('').load();
        const out = {{}};
        for (const q of {json.dumps(queries)}) out[q] = e.search(q, {{ limit: 100 }}).results.map(r => r.id).sort((a, b) => a - b);
        console.log(JSON.stringify(out));
      }})();
    """
    js = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    for q in queries:
        py = sorted(r["id"] for r in search(loaded, q, limit=100)["results"])
        assert js[q] == py, q


def test_share_keys_and_page():
    from archive.share import parse_key, render_share_page, share_key

    assert share_key("S06/S06E05/S06E05_000872345.webp") == "S06E05-872345"
    assert parse_key("S06E05-872345") == ("S06E05", 872345)
    assert share_key(None) is None and parse_key("nope") is None
    line = {"speaker": "MICA", "text": 'We are "at capacity" <sir>!', "episode_code": "S06E05",
            "title": "The Clock Cabinet", "start_ms": 872345,
            "image": "S06/S06E05/S06E05_000872345.webp", "image_width": 1280, "image_height": 720}
    page = render_share_page(line, "https://img.example.com/S06/S06E05/S06E05_000872345.webp",
                             "https://example.com/", "https://img.example.com/q/S06E05-872345.html", "The Cabinet Hearing")
    assert '<meta property="og:image" content="https://img.example.com/S06/S06E05/S06E05_000872345.webp">' in page
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    assert "https://example.com/?id=S06E05-872345" in page
    assert "&lt;sir&gt;" in page and "<sir>" not in page  # quote text is escaped
    assert "S06E05 · The Clock Cabinet · 00:14:32" in page


def test_export_writes_share_pages(loaded, tmp_path):
    from archive.config import Config
    from archive.export import export_share_pages

    loaded.execute("INSERT INTO images (dialogue_id, episode_id, timestamp_ms, filename, extraction_method, is_primary) "
                   "SELECT id, episode_id, 19050, 'S00/S00E01/S00E01_000019050.webp', 'midpoint', 1 FROM dialogue "
                   "WHERE text LIKE '%fog choir%'")
    n = export_share_pages(loaded, tmp_path / "share", "https://i.example/", "https://s.example/",
                           "https://i.example/q/", log=lambda *_: None)
    assert n == 1
    page = (tmp_path / "share" / "S00E01-19050.html").read_text()
    assert "og:title" in page and "fog choir" in page


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("with_manifest", [False, True])
def test_process_local_show(tmp_path, with_manifest):
    from archive import db
    from archive.config import Config
    from archive.manifest import load_manifest
    from archive.pipeline import process_all
    from archive.search import search
    from scripts.demo import make_test_video

    media = tmp_path / "recordings"
    manifest = tmp_path / "episodes.json"
    cfg = Config(tmp_path / "archive.db", tmp_path / "media", tmp_path / "images",
                 tmp_path / "raw", manifest, False)
    name = "The Velvet Compass" if with_manifest else "The Velvet Compass S02E04"
    if with_manifest:
        manifest.write_text(json.dumps({"series": "Cabinet Hours", "episodes": [
            {"season": 2, "episode": 4, "title": "The Velvet Compass"},
        ]}))
    video = make_test_video(media / f"{name}.mp4", seconds=2, size="320x180")
    video.with_suffix(".srt").write_text(
        "1\n00:00:00,100 --> 00:00:01,500\nMICA: The compass points to velvet.\n")
    with db.connect(cfg.db_path) as conn:
        result, = process_all(conn, cfg, media, whisper="never", log=lambda *_: None)
        assert result["error"] is None and result["images"] == 1
        line, = search(conn, "velvet")["results"]
        assert (line["episode_code"], line["speaker"]) == ("S02E04", "MICA")
        assert (cfg.images_dir / line["image"]).is_file()
    episode, = load_manifest(manifest)
    assert episode["episode_code"] == "S02E04" and episode["video_present"]
    if with_manifest:
        assert episode["title"] == "The Velvet Compass" and cfg.series == "Cabinet Hours"
