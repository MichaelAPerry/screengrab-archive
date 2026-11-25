"""FastAPI app: JSON search API + a single static page. Run with `python -m archive serve`."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from archive import db
from archive.config import Config
from archive.search import context, search, speakers
from archive.share import parse_key, render_share_page

cfg = Config.load()
STATIC = Path(__file__).resolve().parent / "static"
cfg.images_dir.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Screengrab Archive", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/images", StaticFiles(directory=cfg.images_dir), name="images")


def conn() -> sqlite3.Connection:
    # one short-lived connection per request keeps SQLite thread-safe under uvicorn's pool
    return db.connect(cfg.db_path)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/search")
def api_search(q: str = "", speaker: str | None = None, season: int | None = None, episode: str | None = None,
               limit: int = Query(24, le=100), offset: int = Query(0, ge=0)) -> dict:
    c = conn()
    try:
        return search(c, q, speaker=speaker or None, season=season, episode=episode or None, limit=limit, offset=offset)
    finally:
        c.close()


@app.get("/api/dialogue/{dialogue_id}")
def api_dialogue(dialogue_id: int, seconds: float = Query(10.0, le=60)) -> dict:
    c = conn()
    try:
        data = context(c, dialogue_id, seconds=seconds)
    finally:
        c.close()
    if data is None:
        raise HTTPException(404, "no such dialogue line")
    data["video_url"] = f"/video/{data['line']['episode_code']}" if cfg.serve_video else None
    return data


@app.get("/api/facets")
def api_facets() -> dict:
    c = conn()
    try:
        eps = c.execute(
            "SELECT e.episode_code, e.season, e.episode, e.title FROM episodes e "
            "WHERE EXISTS (SELECT 1 FROM dialogue d WHERE d.episode_id = e.id) ORDER BY e.season, e.episode"
        ).fetchall()
        return {"series": cfg.series, "episodes": [dict(r) for r in eps], "speakers": speakers(c, min_lines=5)}
    finally:
        c.close()


@app.get("/video/{code}", include_in_schema=False)
def video(code: str) -> FileResponse:
    """Local-only convenience: stream the user's own episode file (disabled unless ARCHIVE_SERVE_VIDEO=1)."""
    if not cfg.serve_video:
        raise HTTPException(404)
    c = conn()
    try:
        row = db.get_episode(c, code)
    finally:
        c.close()
    if not row or not row["video_path"] or not Path(row["video_path"]).exists():
        raise HTTPException(404, "video not indexed")
    return FileResponse(row["video_path"])


@app.get("/api/key/{key}")
def api_key(key: str) -> dict:
    """Resolve a share key (episode + image timestamp) to the dialogue line id."""
    parsed = parse_key(key)
    c = conn()
    try:
        row = parsed and c.execute(
            "SELECT i.dialogue_id FROM images i JOIN episodes e ON e.id = i.episode_id "
            "WHERE e.episode_code = ? AND i.timestamp_ms = ?", parsed).fetchone()
    finally:
        c.close()
    if not row or row[0] is None:
        raise HTTPException(404, "unknown share key")
    return {"id": row[0]}


@app.get("/q/{key}.html", include_in_schema=False)
def share_page(key: str, request: Request) -> HTMLResponse:
    """Same permalink page the static export writes, served live for local testing."""
    line_id = api_key(key)["id"]
    c = conn()
    try:
        data = context(c, line_id)
    finally:
        c.close()
    base = str(request.base_url)
    return HTMLResponse(render_share_page(data["line"], f"{base}images/{data['line']['image']}", base,
                                          f"{base}q/{key}.html", cfg.series))
