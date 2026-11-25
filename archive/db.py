"""SQLite schema and write helpers. Every writer is idempotent (safe to rerun)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from archive.models import Cue, Episode, Origin
from archive.normalize import normalize

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id                INTEGER PRIMARY KEY,
    episode_code      TEXT NOT NULL UNIQUE,
    season            INTEGER NOT NULL,
    episode           INTEGER NOT NULL,
    title             TEXT,
    air_date          TEXT,
    duration_ms       INTEGER,
    transcript_source TEXT,
    subtitle_source   TEXT,
    video_path        TEXT,
    timing_offset_ms  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (season, episode)
);

CREATE TABLE IF NOT EXISTS dialogue (
    id                INTEGER PRIMARY KEY,
    episode_id        INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    sequence          INTEGER NOT NULL,
    speaker           TEXT,
    text              TEXT NOT NULL,
    normalized_text   TEXT NOT NULL,
    start_ms          INTEGER,
    end_ms            INTEGER,
    origin            TEXT NOT NULL CHECK (origin IN ('transcript', 'subtitle')),
    timing_source     TEXT NOT NULL DEFAULT 'none'
                      CHECK (timing_source IN ('none', 'subtitle', 'aligned', 'manual')),
    timing_confidence REAL,
    UNIQUE (episode_id, sequence)
);
CREATE INDEX IF NOT EXISTS dialogue_episode_start ON dialogue (episode_id, start_ms);
CREATE INDEX IF NOT EXISTS dialogue_speaker ON dialogue (speaker);

-- Raw subtitle layer, kept independently of the transcript so alignment can be redone.
CREATE TABLE IF NOT EXISTS subtitle_cues (
    id          INTEGER PRIMARY KEY,
    episode_id  INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,
    sequence    INTEGER NOT NULL,
    start_ms    INTEGER NOT NULL,
    end_ms      INTEGER NOT NULL,
    speaker     TEXT,
    text        TEXT NOT NULL,
    UNIQUE (episode_id, source, sequence)
);

-- Indexes normalized_text (not text) so queries and documents share one normalisation,
-- e.g. "that's" -> "thats" on both sides.
CREATE VIRTUAL TABLE IF NOT EXISTS dialogue_fts USING fts5 (
    normalized_text, speaker,
    content='dialogue', content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS dialogue_ai AFTER INSERT ON dialogue BEGIN
    INSERT INTO dialogue_fts (rowid, normalized_text, speaker) VALUES (new.id, new.normalized_text, new.speaker);
END;
CREATE TRIGGER IF NOT EXISTS dialogue_ad AFTER DELETE ON dialogue BEGIN
    INSERT INTO dialogue_fts (dialogue_fts, rowid, normalized_text, speaker) VALUES ('delete', old.id, old.normalized_text, old.speaker);
END;
CREATE TRIGGER IF NOT EXISTS dialogue_au AFTER UPDATE OF normalized_text, speaker ON dialogue BEGIN
    INSERT INTO dialogue_fts (dialogue_fts, rowid, normalized_text, speaker) VALUES ('delete', old.id, old.normalized_text, old.speaker);
    INSERT INTO dialogue_fts (rowid, normalized_text, speaker) VALUES (new.id, new.normalized_text, new.speaker);
END;

-- One dialogue line can own many images; is_primary marks the one shown in results.
-- Images are keyed by (episode, timestamp) so they survive a dialogue re-import.
CREATE TABLE IF NOT EXISTS images (
    id                INTEGER PRIMARY KEY,
    dialogue_id       INTEGER REFERENCES dialogue(id) ON DELETE SET NULL,
    episode_id        INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    timestamp_ms      INTEGER NOT NULL,
    filename          TEXT NOT NULL UNIQUE,
    width             INTEGER,
    height            INTEGER,
    format            TEXT NOT NULL DEFAULT 'webp',
    extraction_method TEXT NOT NULL,
    quality_score     REAL,
    scores_json       TEXT,
    phash             TEXT,
    is_primary        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (episode_id, timestamp_ms)
);
CREATE INDEX IF NOT EXISTS images_dialogue ON images (dialogue_id);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY,
    episode_id   INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    source_type  TEXT NOT NULL,
    url          TEXT NOT NULL,
    retrieved_at TEXT,
    notes        TEXT,
    UNIQUE (episode_id, source_type, url)
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_episode(conn: sqlite3.Connection, season: int, episode: int, title: str | None = None, **fields) -> int:
    """Create the episode row if needed; fill in any provided non-null fields. Returns id."""
    code = f"S{season:02d}E{episode:02d}"
    conn.execute(
        "INSERT INTO episodes (episode_code, season, episode, title) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (episode_code) DO UPDATE SET title = COALESCE(excluded.title, episodes.title)",
        (code, season, episode, title),
    )
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE episodes SET {sets} WHERE episode_code = ?", (*fields.values(), code))
    return conn.execute("SELECT id FROM episodes WHERE episode_code = ?", (code,)).fetchone()[0]


def get_episode(conn: sqlite3.Connection, code: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM episodes WHERE episode_code = ?", (code.upper(),)).fetchone()


def record_source(conn, episode_id: int, source_type: str, url: str, notes: str | None = None) -> None:
    conn.execute(
        "INSERT INTO sources (episode_id, source_type, url, retrieved_at, notes) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (episode_id, source_type, url) DO UPDATE SET retrieved_at = excluded.retrieved_at, "
        "notes = COALESCE(excluded.notes, sources.notes)",
        (episode_id, source_type, url, _now(), notes),
    )


def replace_dialogue(conn: sqlite3.Connection, ep: Episode) -> tuple[int, bool]:
    """Store an episode's dialogue, replacing earlier lines for it.

    Returns (episode_id, changed). If the incoming lines are identical to what is
    stored, nothing is written, so reruns are cheap and keep dialogue ids stable.
    Images are re-linked to the new rows by timestamp afterwards (see relink_images).
    """
    fields = {"transcript_source": ep.source} if ep.origin is Origin.TRANSCRIPT else {"subtitle_source": ep.source}
    ep_id = upsert_episode(conn, ep.season, ep.episode, ep.title, **fields)
    if ep.source_url:
        record_source(conn, ep_id, f"{ep.origin.value}:{ep.source}", ep.source_url)

    # Compare content only: timing is filled in later by alignment and must not count as a change.
    existing = conn.execute(
        "SELECT sequence, speaker, text, origin FROM dialogue WHERE episode_id = ? ORDER BY sequence",
        (ep_id,),
    ).fetchall()
    incoming = [(ln.sequence, ln.speaker, ln.text, ep.origin.value) for ln in ep.lines]
    if [tuple(r) for r in existing] == incoming:
        conn.commit()
        return ep_id, False

    conn.execute("DELETE FROM dialogue WHERE episode_id = ?", (ep_id,))
    conn.executemany(
        "INSERT INTO dialogue (episode_id, sequence, speaker, text, normalized_text, start_ms, end_ms, "
        "origin, timing_source, timing_confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (ep_id, ln.sequence, ln.speaker, ln.text, normalize(ln.text), ln.start, ln.end,
             ep.origin.value, ln.timing_source.value, ln.timing_confidence)
            for ln in ep.lines
        ],
    )
    relink_images(conn, ep_id)
    conn.commit()
    return ep_id, True


def replace_cues(conn: sqlite3.Connection, episode_id: int, source: str, cues: Iterable[Cue]) -> int:
    conn.execute("DELETE FROM subtitle_cues WHERE episode_id = ? AND source = ?", (episode_id, source))
    rows = [(episode_id, source, c.sequence, c.start, c.end, c.speaker, c.text) for c in cues]
    conn.executemany(
        "INSERT INTO subtitle_cues (episode_id, source, sequence, start_ms, end_ms, speaker, text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.execute("UPDATE episodes SET subtitle_source = ? WHERE id = ?", (source, episode_id))
    conn.commit()
    return len(rows)


def load_cues(conn: sqlite3.Connection, episode_id: int, source: str | None = None) -> list[Cue]:
    if source is None:
        row = conn.execute("SELECT subtitle_source FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        source = row[0] if row else None
    rows = conn.execute(
        "SELECT sequence, start_ms, end_ms, text, speaker FROM subtitle_cues "
        "WHERE episode_id = ? AND source = ? ORDER BY sequence",
        (episode_id, source),
    ).fetchall()
    return [Cue(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def update_timing(conn: sqlite3.Connection, updates: Iterable[tuple[int, int | None, int | None, str, float | None]]) -> None:
    """updates: (dialogue_id, start_ms, end_ms, timing_source, confidence). Never overwrites manual timing."""
    conn.executemany(
        "UPDATE dialogue SET start_ms = ?, end_ms = ?, timing_source = ?, timing_confidence = ? "
        "WHERE id = ? AND timing_source != 'manual'",
        [(s, e, src, conf, did) for did, s, e, src, conf in updates],
    )
    conn.commit()


def relink_images(conn: sqlite3.Connection, episode_id: int) -> None:
    """Point each image at the dialogue line whose interval contains its timestamp."""
    conn.execute(
        """
        UPDATE images SET dialogue_id = (
            SELECT d.id FROM dialogue d
            WHERE d.episode_id = images.episode_id AND d.start_ms IS NOT NULL
              AND images.timestamp_ms BETWEEN d.start_ms AND d.end_ms
            ORDER BY d.end_ms - d.start_ms LIMIT 1
        ) WHERE episode_id = ?
        """,
        (episode_id,),
    )


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO dialogue_fts (dialogue_fts) VALUES ('rebuild')")
    conn.execute("INSERT INTO dialogue_fts (dialogue_fts) VALUES ('optimize')")
    conn.commit()
