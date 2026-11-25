"""Query parsing and ranked search over the SQLite FTS5 index.

Supported syntax (all parts optional, combinable):

    fog choir                     all words
    "fog choir"                   exact phrase
    speaker:FINCH fog         speaker filter (also who:, character:)
    episode:S04E08               episode filter (also ep:)
    season:4                     season filter (also s:)
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from archive.normalize import normalize
from archive.subtitles import format_timestamp

_TOKEN_RE = re.compile(r'(\w+):("[^"]*"|\S+)|"([^"]*)"?|(\S+)')
_FILTER_KEYS = {
    "speaker": "speaker", "who": "speaker", "character": "speaker", "char": "speaker",
    "episode": "episode", "ep": "episode",
    "season": "season", "s": "season",
}
RERANK_POOL = 500


@dataclass
class Query:
    words: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)
    speaker: str | None = None
    episode: str | None = None
    season: int | None = None

    @property
    def has_text(self) -> bool:
        return bool(self.words or self.phrases)

    @property
    def has_filters(self) -> bool:
        return any(v is not None for v in (self.speaker, self.episode, self.season))

    def fts(self, any_word: bool = False) -> str:
        """Build an FTS5 MATCH expression; every term is quoted so user input can't break syntax."""
        def q(t: str) -> str:
            return '"' + t.replace('"', '""') + '"'
        terms = [q(p) for p in self.phrases]
        words = [q(w) for w in self.words]
        if any_word and words:
            terms.append("(" + " OR ".join(words) + ")")
        else:
            terms.extend(words)
        # restrict to dialogue text so a word like "finch" doesn't match the speaker column
        return "normalized_text : (" + " AND ".join(terms) + ")"


def parse_query(raw: str) -> Query:
    q = Query()
    for m in _TOKEN_RE.finditer(raw or ""):
        key, val, phrase, word = m.groups()
        if key is not None:
            target = _FILTER_KEYS.get(key.lower())
            val = val.strip('"')
            if target == "speaker" and val:
                q.speaker = val.upper()
            elif target == "episode" and re.fullmatch(r"(?i)s?\d{1,2}e\d{1,2}", val):
                s, e = re.findall(r"\d+", val)
                q.episode = f"S{int(s):02d}E{int(e):02d}"
            elif target == "season" and val.isdigit():
                q.season = int(val)
            else:  # unknown key: treat "foo:bar" as ordinary words
                q.words.extend(normalize(f"{key} {val}").split())
        elif phrase is not None:
            norm = normalize(phrase)
            if len(norm.split()) > 1:
                q.phrases.append(norm)
            elif norm:
                q.words.append(norm)
        elif word:
            q.words.extend(normalize(word).split())
    return q


_BASE_SELECT = """
SELECT d.id, d.sequence, d.speaker, d.text, d.normalized_text, d.start_ms, d.end_ms,
       d.timing_source, d.timing_confidence, e.episode_code, e.season, e.episode, e.title,
       i.filename AS image, i.width AS image_width, i.height AS image_height,
       (SELECT COUNT(*) FROM images x WHERE x.dialogue_id = d.id) AS image_count
       {rank}
FROM dialogue d
JOIN episodes e ON e.id = d.episode_id
LEFT JOIN images i ON i.dialogue_id = d.id AND i.is_primary = 1
{fts_join}
WHERE 1 = 1 {where}
"""


def _filters(q: Query) -> tuple[str, list]:
    where, params = [], []
    if q.speaker:
        where.append("AND d.speaker = ?")
        params.append(q.speaker)
    if q.episode:
        where.append("AND e.episode_code = ?")
        params.append(q.episode)
    if q.season is not None:
        where.append("AND e.season = ?")
        params.append(q.season)
    return " ".join(where), params


def _row_to_result(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "episode_code": r["episode_code"],
        "season": r["season"],
        "episode": r["episode"],
        "title": r["title"],
        "sequence": r["sequence"],
        "speaker": r["speaker"],
        "text": r["text"],
        "start_ms": r["start_ms"],
        "end_ms": r["end_ms"],
        "timestamp": format_timestamp(r["start_ms"]),
        "timing_source": r["timing_source"],
        "timing_confidence": r["timing_confidence"],
        "image": r["image"],
        "image_width": r["image_width"],
        "image_height": r["image_height"],
        "image_count": r["image_count"],
    }


def _rerank(q: Query, rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Order: exact phrase > short line > multiple terms > broad bm25 match."""
    full = " ".join(q.phrases + q.words)
    terms = set(" ".join(q.phrases + q.words).split())

    def key(r: sqlite3.Row) -> float:
        norm = r["normalized_text"]
        toks = norm.split()
        score = -r["bm25"]  # bm25(): more negative = better
        if full and f" {full} " in f" {norm} ":
            score += 10
        if norm == full:
            score += 5
        score += 3 * max(0.0, 1 - len(toks) / 25)
        score += 2 * len(terms & set(toks)) / max(len(terms), 1)
        return -score

    return sorted(rows, key=key)


def search(conn: sqlite3.Connection, raw: str, *, speaker: str | None = None, season: int | None = None,
           episode: str | None = None, limit: int = 24, offset: int = 0) -> dict:
    q = parse_query(raw)
    # explicit filter arguments (from UI dropdowns) override query-string filters
    if speaker:
        q.speaker = speaker.upper()
    if season is not None:
        q.season = season
    if episode:
        parsed = parse_query(f"episode:{episode}")
        q.episode = parsed.episode or q.episode
    where, params = _filters(q)

    if not q.has_text:
        if not q.has_filters:
            return {"query": raw, "total": 0, "results": [], "mode": "empty"}
        sql = _BASE_SELECT.format(rank="", fts_join="", where=where) + " ORDER BY e.season, e.episode, d.sequence LIMIT ? OFFSET ?"
        total = conn.execute(f"SELECT COUNT(*) FROM ({_BASE_SELECT.format(rank='', fts_join='', where=where)})", params).fetchone()[0]
        rows = conn.execute(sql, [*params, limit, offset]).fetchall()
        return {"query": raw, "total": total, "results": [_row_to_result(r) for r in rows], "mode": "browse"}

    mode = "all"
    for any_word in (False, True):
        match = q.fts(any_word=any_word)
        fts_join = "JOIN dialogue_fts f ON f.rowid = d.id AND dialogue_fts MATCH ?"
        inner = _BASE_SELECT.format(rank=", bm25(dialogue_fts) AS bm25", fts_join=fts_join, where=where)
        total = conn.execute(f"SELECT COUNT(*) FROM ({inner})", [match, *params]).fetchone()[0]
        if total or any_word or len(q.words) < 2:
            break
        mode = "any"  # nothing matched every word: fall back to broader OR matching
    rows = conn.execute(inner + " ORDER BY bm25 LIMIT ?", [match, *params, RERANK_POOL]).fetchall()
    ranked = _rerank(q, rows)[offset:offset + limit]
    return {"query": raw, "total": total, "results": [_row_to_result(r) for r in ranked], "mode": mode}


def context(conn: sqlite3.Connection, dialogue_id: int, seconds: float = 10.0, fallback_lines: int = 6) -> dict | None:
    """The selected line plus surrounding dialogue (about +/- `seconds` when timed)."""
    row = conn.execute(_BASE_SELECT.format(rank="", fts_join="", where="AND d.id = ?"), [dialogue_id]).fetchone()
    if row is None:
        return None
    ep_id = conn.execute("SELECT episode_id FROM dialogue WHERE id = ?", [dialogue_id]).fetchone()[0]
    seq, start = row["sequence"], row["start_ms"]
    window = conn.execute(
        "SELECT id, sequence, speaker, text, start_ms, end_ms, timing_source FROM dialogue "
        "WHERE episode_id = ? AND sequence BETWEEN ? AND ? ORDER BY sequence",
        [ep_id, seq - 40, seq + 40],
    ).fetchall()
    span = seconds * 1000
    lines = []
    for w in window:
        near_seq = abs(w["sequence"] - seq) <= fallback_lines
        if start is not None and w["start_ms"] is not None:
            keep = abs(w["start_ms"] - start) <= span
        else:
            keep = near_seq
        if keep:
            lines.append({**dict(w), "timestamp": format_timestamp(w["start_ms"]), "selected": w["id"] == dialogue_id})
    images = conn.execute(
        "SELECT filename, timestamp_ms, width, height, quality_score, is_primary FROM images "
        "WHERE dialogue_id = ? ORDER BY is_primary DESC, quality_score DESC", [dialogue_id]
    ).fetchall()
    return {
        "line": _row_to_result(row),
        "context": lines,
        "images": [{**dict(i), "timestamp": format_timestamp(i["timestamp_ms"], with_ms=True)} for i in images],
    }


def speakers(conn: sqlite3.Connection, min_lines: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT speaker, COUNT(*) n FROM dialogue WHERE speaker IS NOT NULL GROUP BY speaker "
        "HAVING n >= ? ORDER BY n DESC", [min_lines]
    ).fetchall()
    return [{"speaker": r[0], "lines": r[1]} for r in rows]
