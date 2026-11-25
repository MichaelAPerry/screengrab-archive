"""episodes.json: the project's progress tracker, regenerated from the DB + filesystem.

Titles and episode numbering are hand-maintained metadata; every *_status field is
derived, so the file can be rewritten at any time with `python -m archive manifest`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from archive import db
from archive.config import ROOT, Config
from archive.models import episode_code
from archive.video import find_video

STATUS_FIELDS = ("transcript_status", "subtitle_status", "timing_status", "image_status")


def load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    episodes = json.loads(path.read_text(encoding="utf-8")).get("episodes", [])
    for entry in episodes:
        entry["episode_code"] = episode_code(entry["season"], entry["episode"])
    return episodes


def seed_episodes(conn: sqlite3.Connection, path: Path) -> int:
    eps = load_manifest(path)
    for e in eps:
        db.upsert_episode(conn, e["season"], e["episode"], e.get("title"), air_date=e.get("air_date"))
    conn.commit()
    return len(eps)


def _level(done: int, total: int) -> str:
    if total == 0 or done == 0:
        return "none"
    return "complete" if done / total >= 0.9 else "partial"


def episode_status(conn: sqlite3.Connection, cfg: Config, row: sqlite3.Row) -> dict:
    ep_id, code = row["id"], row["episode_code"]
    d = conn.execute(
        "SELECT COUNT(*) total, SUM(origin = 'transcript') transcript, SUM(start_ms IS NOT NULL) timed, "
        "SUM(speaker IS NOT NULL) with_speaker FROM dialogue WHERE episode_id = ?", [ep_id]
    ).fetchone()
    cues = conn.execute("SELECT COUNT(*) FROM subtitle_cues WHERE episode_id = ?", [ep_id]).fetchone()[0]
    imaged = conn.execute(
        "SELECT COUNT(DISTINCT dialogue_id) FROM images WHERE episode_id = ? AND dialogue_id IS NOT NULL", [ep_id]
    ).fetchone()[0]
    total, timed = d["total"] or 0, d["timed"] or 0
    indexed = Path(row["video_path"]) if row["video_path"] else None
    video = indexed if indexed and indexed.is_file() else find_video(cfg.media_dir, code)
    try:
        video_rel = str(video.relative_to(ROOT)) if video else None
    except ValueError:
        video_rel = str(video)
    return {
        "video_path": video_rel or f"media/{code[:3]}/{code}.mp4",
        "video_present": video is not None,
        "transcript_status": ("complete" if d["transcript"] else "subtitle-only" if total else "missing"),
        "subtitle_status": "complete" if cues else "missing",
        "timing_status": _level(timed, total),
        "image_status": _level(imaged, timed),
        "counts": {"lines": total, "with_speaker": d["with_speaker"] or 0, "cues": cues,
                   "timed": timed, "with_image": imaged},
    }


def write_manifest(conn: sqlite3.Connection, cfg: Config) -> Path:
    existing = {e["episode_code"]: e for e in load_manifest(cfg.manifest_path)}
    out = []
    for row in conn.execute("SELECT * FROM episodes ORDER BY season, episode").fetchall():
        base = existing.get(row["episode_code"], {})
        entry = {
            "episode_code": row["episode_code"],
            "season": row["season"],
            "episode": row["episode"],
            "title": base.get("title") or row["title"],
        }
        if base.get("air_date") or row["air_date"]:
            entry["air_date"] = base.get("air_date") or row["air_date"]
        entry.update(episode_status(conn, cfg, row))
        out.append(entry)
    # keep manifest entries that aren't in the DB yet
    in_db = {e["episode_code"] for e in out}
    for code, e in existing.items():
        if code not in in_db:
            out.append({k: v for k, v in e.items()})
    out.sort(key=lambda e: (e["season"], e["episode"]))
    cfg.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.manifest_path.write_text(json.dumps({"series": cfg.series, "episodes": out}, indent=1, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    return cfg.manifest_path
