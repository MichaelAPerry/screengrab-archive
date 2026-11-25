"""Operations behind the CLI commands. Each one is idempotent."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image

from archive import db
from archive.alignment import align
from archive.config import Config
from archive.images import image_relpath, is_near_duplicate, save_webp, score_image, dhash
from archive.models import Origin, TimingSource, parse_episode_code
from archive.sources.base import TranscriptSource
from archive.subtitles import cues_to_episode, extract_embedded_subtitles, load_subtitle_file
from archive.video import CANDIDATES, MIDPOINT, candidate_timestamps, find_video, grab_frame, probe

Log = Callable[[str], None]

SUBTITLE_EXTENSIONS = (".srt", ".vtt")


def import_transcripts(conn: sqlite3.Connection, source: TranscriptSource,
                       wanted: Iterable[tuple[int, int]] | None = None, log: Log = print) -> int:
    count = 0
    for ep in source.iter_episodes(wanted):
        ep_id, changed = db.replace_dialogue(conn, ep)
        msg = f"{ep.episode_code}: {len(ep.lines)} lines from {source.name}" + ("" if changed else " (unchanged)")
        if changed and db.load_cues(conn, ep_id):
            msg += "; " + _align_msg(align_episode(conn, ep_id))
        log(msg)
        count += 1
    return count


def import_subtitle_file(conn: sqlite3.Connection, path: Path, source_name: str | None = None, log: Log = print,
                         *, episode: tuple[int, int] | None = None) -> bool:
    code = episode or parse_episode_code(path.name)
    if not code:
        log(f"skip {path.name}: no SxxEyy code in file name")
        return False
    cues = load_subtitle_file(path)
    if not cues:
        log(f"skip {path.name}: no cues parsed")
        return False
    season, episode = code
    source = source_name or f"file:{path.name}"
    ep_id = db.upsert_episode(conn, season, episode)
    db.record_source(conn, ep_id, "subtitle", source)
    n = db.replace_cues(conn, ep_id, source, cues)
    origin = conn.execute("SELECT origin FROM dialogue WHERE episode_id = ? LIMIT 1", [ep_id]).fetchone()
    code_s = f"S{season:02d}E{episode:02d}"
    if origin is None or origin[0] == Origin.SUBTITLE.value:
        # no transcript yet: the subtitles themselves become the searchable dialogue
        _, changed = db.replace_dialogue(conn, cues_to_episode(season, episode, cues, source))
        log(f"{code_s}: {n} cues from {path.name} -> subtitle-only dialogue" + ("" if changed else " (unchanged)"))
    else:
        log(f"{code_s}: {n} cues from {path.name}; " + _align_msg(align_episode(conn, ep_id)))
    return True


def import_subtitles(conn, path: Path, source_name: str | None = None, log: Log = print) -> int:
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.suffix.lower() in SUBTITLE_EXTENSIONS)
    return sum(import_subtitle_file(conn, f, source_name, log) for f in files)


def align_episode(conn: sqlite3.Connection, episode_id: int) -> dict:
    """Re-run transcript-to-subtitle alignment from stored cues. Manual timings are preserved."""
    rows = conn.execute(
        "SELECT id, text, speaker FROM dialogue WHERE episode_id = ? AND origin = 'transcript' ORDER BY sequence",
        [episode_id],
    ).fetchall()
    cues = db.load_cues(conn, episode_id)
    matches = {m.line_index: m for m in align([r["text"] for r in rows], cues)}
    updates = []
    for idx, r in enumerate(rows):
        m = matches.get(idx)
        if m:
            updates.append((r["id"], m.start, m.end, TimingSource.ALIGNED.value, m.confidence))
            if m.cue_speaker and not r["speaker"]:
                conn.execute("UPDATE dialogue SET speaker = ? WHERE id = ?", [m.cue_speaker, r["id"]])
        else:
            updates.append((r["id"], None, None, TimingSource.NONE.value, None))
    db.update_timing(conn, updates)
    db.relink_images(conn, episode_id)
    conn.commit()
    conf = [m.confidence for m in matches.values()]
    return {"lines": len(rows), "cues": len(cues), "aligned": len(matches),
            "mean_confidence": round(sum(conf) / len(conf), 3) if conf else None}


def _align_msg(stats: dict) -> str:
    pct = 100 * stats["aligned"] / stats["lines"] if stats["lines"] else 0
    return f"aligned {stats['aligned']}/{stats['lines']} lines ({pct:.0f}%, mean confidence {stats['mean_confidence']})"


def index_video(conn: sqlite3.Connection, cfg: Config, path: Path, extract_subs: bool = False, log: Log = print) -> int:
    """Register local video file(s): episode -> path + duration. Optionally import embedded subtitles."""
    from archive.video import VIDEO_EXTENSIONS

    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.suffix.lower() in VIDEO_EXTENSIONS)
    n = 0
    for f in files:
        code = parse_episode_code(f.name)
        if not code:
            log(f"skip {f.name}: no SxxEyy code in file name")
            continue
        info = probe(f)
        ep_id = db.upsert_episode(conn, *code, video_path=str(f.resolve()), duration_ms=info["duration_ms"])
        conn.commit()
        msg = f"S{code[0]:02d}E{code[1]:02d}: {f} ({(info['duration_ms'] or 0) / 60000:.1f} min, {len(info['subtitle_streams'])} subtitle streams)"
        if extract_subs and info["subtitle_streams"]:
            out = cfg.raw_dir / "embedded_subs" / f"S{code[0]:02d}E{code[1]:02d}.srt"
            if extract_embedded_subtitles(f, out):
                msg += "; extracted embedded subtitles"
                log(msg)
                import_subtitle_file(conn, out, source_name=f"embedded:{f.name}", log=log)
                n += 1
                continue
            msg += "; embedded subtitles not text-based, skipped"
        log(msg)
        n += 1
    return n


def extract_frames(conn: sqlite3.Connection, cfg: Config, code: str, *, candidates: bool = False,
                   limit: int | None = None, force: bool = False, min_words: int = 1, log: Log = print) -> dict:
    """For each timed dialogue line without an image, grab frame(s) and store WebP."""
    ep = db.get_episode(conn, code)
    if ep is None:
        raise SystemExit(f"unknown episode {code}")
    video = Path(ep["video_path"]) if ep["video_path"] and Path(ep["video_path"]).exists() else find_video(cfg.media_dir, code)
    if video is None:
        raise SystemExit(f"{code}: no video found under {cfg.media_dir} (expected e.g. {cfg.media_dir / code[:3] / (code + '.mp4')})")
    if not ep["video_path"]:
        db.upsert_episode(conn, ep["season"], ep["episode"], video_path=str(video.resolve()))

    sql = ("SELECT d.id, d.start_ms, d.end_ms FROM dialogue d WHERE d.episode_id = ? AND d.start_ms IS NOT NULL"
           + ("" if force else " AND NOT EXISTS (SELECT 1 FROM images i WHERE i.dialogue_id = d.id)")
           + " ORDER BY d.sequence")
    rows = conn.execute(sql, [ep["id"]]).fetchall()
    if min_words > 1:  # skip "Yeah." / "What?" style lines nobody searches for
        rows = [r for r in rows if len(conn.execute("SELECT normalized_text FROM dialogue WHERE id = ?",
                                                    [r["id"]]).fetchone()[0].split()) >= min_words]
    if limit:
        rows = rows[:limit]
    fractions = CANDIDATES if candidates else MIDPOINT
    stats = {"lines": len(rows), "images": 0, "duplicates_skipped": 0}
    for r in rows:
        kept: list[tuple[int, str, float]] = []  # (image id, hash, score)
        for ts in candidate_timestamps(r["start_ms"], r["end_ms"], fractions, ep["timing_offset_ms"]):
            rel = image_relpath(code, ts)
            out = cfg.images_dir / rel
            reuse = out.exists() and not force
            img = Image.open(out).convert("RGB") if reuse else grab_frame(video, ts)
            h = dhash(img)
            if is_near_duplicate(h, [k[1] for k in kept]):
                stats["duplicates_skipped"] += 1
                continue
            if not reuse:
                save_webp(img, out)
            quality, scores = score_image(img)
            method = "midpoint" if len(fractions) == 1 else "candidates-25-50-75"
            conn.execute(
                "INSERT INTO images (dialogue_id, episode_id, timestamp_ms, filename, width, height, format, "
                "extraction_method, quality_score, scores_json, phash, is_primary) VALUES (?,?,?,?,?,?,?,?,?,?,?,0) "
                "ON CONFLICT (episode_id, timestamp_ms) DO UPDATE SET dialogue_id = excluded.dialogue_id, "
                "width = excluded.width, height = excluded.height, quality_score = excluded.quality_score, "
                "scores_json = excluded.scores_json, phash = excluded.phash",
                [r["id"], ep["id"], ts, rel, img.width, img.height, "webp", method, quality, json.dumps(scores), h],
            )
            img_id = conn.execute("SELECT id FROM images WHERE episode_id = ? AND timestamp_ms = ?", [ep["id"], ts]).fetchone()[0]
            kept.append((img_id, h, quality))
            stats["images"] += 1
        if kept:
            # V1: midpoint is primary; with candidates, the best-scoring frame wins
            best = max(kept, key=lambda k: k[2])[0] if candidates else kept[len(kept) // 2][0]
            conn.execute("UPDATE images SET is_primary = (id = ?) WHERE dialogue_id = ?", [best, r["id"]])
        conn.commit()
    log(f"{code}: {stats['images']} frames for {stats['lines']} lines -> {cfg.images_dir / code[:3] / code}"
        + (f" ({stats['duplicates_skipped']} near-duplicates skipped)" if stats["duplicates_skipped"] else ""))
    return stats


def process_all(conn: sqlite3.Connection, cfg: Config, root: Path, *, whisper: str = "auto",
                whisper_model: str | None = None, season: int | None = None, only: set[str] | None = None,
                min_words: int = 1, candidates: bool = False, limit: int | None = None, log: Log = print) -> list[dict]:
    """The whole chain for every episode file under `root`. Resumable: finished steps are skipped.

    Timing source per episode, first that applies (whisper="auto"):
      existing cues in the DB -> embedded text subtitle track -> sidecar .srt/.vtt -> Whisper
    whisper="always" uses Whisper even when subtitles exist; "never" never runs it.
    """
    import time

    from archive import transcribe
    from archive.manifest import load_manifest, seed_episodes, write_manifest
    from archive.media import discover

    if not root.exists():
        raise SystemExit(f"media path does not exist: {root}")
    episodes = load_manifest(cfg.manifest_path)
    seed_episodes(conn, cfg.manifest_path)
    files = [f for f in discover(root, episodes) if f.code]
    files = [f for f in files if (season is None or int(f.code[1:3]) == season) and (not only or f.code in only)]
    if not files:
        raise SystemExit("no matching video files; use inspect-media to check episode names")
    seen: set[str] = set()
    results = []
    t0 = time.monotonic()
    for n, mf in enumerate(files, 1):
        if mf.code in seen:
            log(f"[{n}/{len(files)}] {mf.code}: duplicate file {mf.path.name}, skipped")
            continue
        seen.add(mf.code)
        res = {"episode": mf.code, "file": str(mf.path), "timing": None, "images": 0, "error": None}
        log(f"[{n}/{len(files)}] {mf.code}  {mf.path.name}")
        try:
            if mf.error:
                raise RuntimeError(mf.error)
            s_, e_ = int(mf.code[1:3]), int(mf.code[4:6])
            ep_id = db.upsert_episode(conn, s_, e_, video_path=str(mf.path.resolve()), duration_ms=mf.duration_ms)
            conn.commit()
            has_cues = bool(conn.execute("SELECT 1 FROM subtitle_cues WHERE episode_id = ? LIMIT 1", [ep_id]).fetchone())
            best = mf.best_text_subtitle()
            if has_cues and whisper != "always":
                res["timing"] = "existing"
            elif best and whisper != "always":
                out = cfg.raw_dir / "embedded_subs" / f"{mf.code}.srt"
                if extract_embedded_subtitles(mf.path, out, stream=best.index) and import_subtitle_file(
                        conn, out, source_name=f"embedded:{mf.path.name}", log=log):
                    res["timing"] = "embedded"
            if res["timing"] is None and mf.sidecars and whisper != "always":
                sidecar = mf.sidecars[0]
                if import_subtitle_file(conn, sidecar, source_name=f"sidecar:{sidecar.name}",
                                        log=log, episode=(s_, e_)):
                    res["timing"] = "sidecar"
            if res["timing"] is None and whisper != "never":
                model = whisper_model or transcribe.DEFAULT_MODEL
                out = cfg.raw_dir / "whisper" / model / f"{mf.code}.srt"
                transcribe.transcribe(mf.path, out, model, log=log)
                if import_subtitle_file(conn, out, source_name=f"whisper:{model}", log=log):
                    res["timing"] = "whisper"
            if res["timing"] is None:
                raise RuntimeError("no usable dialogue timing")
            stats = extract_frames(conn, cfg, mf.code, candidates=candidates, limit=limit,
                                   min_words=min_words, log=log)
            res["images"] = stats["images"]
        except Exception as e:  # keep going; continue processing the remaining files
            res["error"] = f"{type(e).__name__}: {e}"
            log(f"   ERROR {res['error']}")
        results.append(res)
        elapsed = time.monotonic() - t0
        log(f"   done in {elapsed / n:.0f}s avg; ~{elapsed / n * (len(files) - n) / 60:.0f} min left")
    write_manifest(conn, cfg)
    failed = [r for r in results if r["error"]]
    log(f"\nprocessed {len(results)} episodes, {sum(r['images'] for r in results)} new images, {len(failed)} failed")
    for r in failed:
        log(f"   {r['episode']}: {r['error']}")
    return results
