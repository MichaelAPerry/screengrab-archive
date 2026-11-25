"""Command line interface: python -m archive <command> ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archive import db
from archive.config import Config
from archive.models import parse_episode_code


def _episodes_arg(value: str | None) -> list[tuple[int, int]] | None:
    if not value:
        return None
    out = []
    for part in value.split(","):
        code = parse_episode_code(part.strip())
        if not code:
            raise SystemExit(f"bad episode code: {part!r} (expected e.g. S01E01)")
        out.append(code)
    return out


def _wanted(source, args) -> list[tuple[int, int]] | None:
    wanted = _episodes_arg(args.episodes)
    if args.season is not None:
        wanted = [c for c in (wanted or source.available()) if c[0] == args.season]
    return wanted


def cmd_init(cfg: Config, conn, args) -> None:
    from archive.manifest import seed_episodes

    n = seed_episodes(conn, cfg.manifest_path)
    print(f"database ready at {cfg.db_path}; {n} episodes loaded from {cfg.manifest_path.name}")


def cmd_import_transcripts(cfg: Config, conn, args) -> None:
    from archive.pipeline import import_transcripts
    from archive.sources import SOURCES

    cls = SOURCES[args.source]
    source = cls(args.path)
    n = import_transcripts(conn, source, _wanted(source, args))
    print(f"{n} episode(s) imported")


def cmd_import_subtitles(cfg: Config, conn, args) -> None:
    from archive.pipeline import import_subtitles

    n = import_subtitles(conn, Path(args.path), args.source_name)
    print(f"{n} subtitle file(s) imported")


def cmd_align(cfg: Config, conn, args) -> None:
    from archive.pipeline import _align_msg, align_episode

    q = "SELECT id, episode_code FROM episodes WHERE subtitle_source IS NOT NULL"
    params: list = []
    if args.episode:
        q += " AND episode_code = ?"
        params.append(args.episode.upper())
    for row in conn.execute(q, params).fetchall():
        print(f"{row['episode_code']}: {_align_msg(align_episode(conn, row['id']))}")


def cmd_index_video(cfg: Config, conn, args) -> None:
    from archive.pipeline import index_video

    n = index_video(conn, cfg, Path(args.path), extract_subs=args.extract_subs)
    print(f"{n} video file(s) indexed")


def cmd_extract_frames(cfg: Config, conn, args) -> None:
    from archive.pipeline import extract_frames

    codes = [args.episode.upper()] if args.episode else [
        r[0] for r in conn.execute(
            "SELECT episode_code FROM episodes WHERE (? IS NULL OR season = ?) ORDER BY season, episode",
            [args.season, args.season]).fetchall()
    ]
    for code in codes:
        try:
            extract_frames(conn, cfg, code, candidates=args.candidates, limit=args.limit, force=args.force,
                           min_words=args.min_words)
        except SystemExit as e:
            if args.episode:
                raise
            print(f"skip {e}")


def cmd_inspect_media(cfg: Config, args) -> None:
    from archive.manifest import load_manifest
    from archive.media import discover, report

    episodes = load_manifest(cfg.manifest_path)
    print(report(discover(Path(args.path), episodes), episodes))


def cmd_transcribe(cfg: Config, conn, args) -> None:
    from archive import transcribe
    from archive.pipeline import import_subtitle_file
    from archive.video import find_video

    ep = db.get_episode(conn, args.episode)
    video = Path(args.video) if args.video else (
        Path(ep["video_path"]) if ep and ep["video_path"] else find_video(cfg.media_dir, args.episode))
    if not video or not video.exists():
        raise SystemExit(f"no video for {args.episode}; pass --video PATH")
    out = cfg.raw_dir / "whisper" / args.model / f"{args.episode.upper()}.srt"
    transcribe.transcribe(video, out, args.model)
    import_subtitle_file(conn, out, source_name=f"whisper:{args.model}")


def cmd_process_all(cfg: Config, conn, args) -> None:
    from archive.pipeline import process_all

    only = {c.strip().upper() for c in args.episodes.split(",")} if args.episodes else None
    process_all(conn, cfg, Path(args.path), whisper=args.whisper, whisper_model=args.model, season=args.season,
                only=only, min_words=args.min_words, candidates=args.candidates, limit=args.limit)


def cmd_export_static(cfg: Config, conn, args) -> None:
    from archive.export import export_static

    share_base = args.share_base
    if args.site_url and not share_base and args.image_base.startswith(("https://", "http://")):
        share_base = args.image_base + "q/"
    export_static(conn, cfg, Path(args.out), image_base=args.image_base, copy_images=args.copy_images,
                  site_url=args.site_url, share_base=share_base,
                  share_out=Path(args.share_out) if args.site_url else None)


def cmd_build_index(cfg: Config, conn, args) -> None:
    from archive.manifest import write_manifest

    db.rebuild_fts(conn)
    conn.execute("VACUUM")
    print(f"search index rebuilt; manifest written to {write_manifest(conn, cfg)}")


def cmd_manifest(cfg: Config, conn, args) -> None:
    from archive.manifest import write_manifest

    print(f"manifest written to {write_manifest(conn, cfg)}")


def cmd_search(cfg: Config, conn, args) -> None:
    from archive.search import search

    res = search(conn, " ".join(args.query), limit=args.limit)
    print(f"{res['total']} match(es)" + (" (any-word fallback)" if res["mode"] == "any" else ""))
    for r in res["results"]:
        when = r["timestamp"] or "--:--:--"
        img = "  [img]" if r["image"] else ""
        print(f"{r['episode_code']} {when}  {(r['speaker'] or '?'):<10} {r['text']}{img}")


def cmd_stats(cfg: Config, conn, args) -> None:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    print(f"episodes with dialogue: {q('SELECT COUNT(DISTINCT episode_id) FROM dialogue')}")
    print(f"dialogue lines:         {q('SELECT COUNT(*) FROM dialogue')}")
    for row in conn.execute("SELECT timing_source, COUNT(*) FROM dialogue GROUP BY timing_source"):
        print(f"  timing {row[0]:<9}      {row[1]}")
    print(f"subtitle cues:          {q('SELECT COUNT(*) FROM subtitle_cues')}")
    print(f"images:                 {q('SELECT COUNT(*) FROM images')}")


def cmd_serve(cfg: Config, conn, args) -> None:
    import uvicorn

    conn.close()
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=False)


def cmd_demo(args) -> None:
    from scripts.demo import run_demo

    run_demo(Path(args.out))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m archive", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database and load episode metadata from the manifest")

    s = sub.add_parser("import-transcripts", help="import transcripts through a source adapter")
    s.add_argument("--source", default="text", choices=["text", "foreverdreaming"])
    s.add_argument("--path", required=True, help="directory of saved pages / text files (foreverdreaming, text)")
    s.add_argument("--season", type=int)
    s.add_argument("--episodes", help="comma-separated codes, e.g. S01E01,S01E02")

    s = sub.add_parser("import-subtitles", help="import .srt/.vtt files (file names must contain SxxEyy)")
    s.add_argument("path")
    s.add_argument("--source-name", help="label stored as the subtitle source")

    s = sub.add_parser("align", help="re-run transcript/subtitle alignment from stored cues")
    s.add_argument("--episode")

    s = sub.add_parser("index-video", help="register local video file(s) for an episode")
    s.add_argument("path", help="a video file or a directory to scan")
    s.add_argument("--extract-subs", action="store_true", help="also import embedded text subtitle tracks")

    s = sub.add_parser("extract-frames", help="extract WebP screengrabs for timed dialogue")
    s.add_argument("--episode")
    s.add_argument("--season", type=int)
    s.add_argument("--candidates", action="store_true", help="sample 25/50/75%% and keep the best, not just the midpoint")
    s.add_argument("--limit", type=int, help="only the first N lines (for testing)")
    s.add_argument("--force", action="store_true", help="re-extract even if an image exists")
    s.add_argument("--min-words", type=int, default=1, help="skip lines shorter than this (e.g. 2 skips 'Yeah.')")

    s = sub.add_parser("inspect-media", help="report what episode files are under a folder (no changes made)")
    s.add_argument("path")

    s = sub.add_parser("transcribe", help="generate timing for one episode from its audio with Whisper")
    s.add_argument("--episode", required=True)
    s.add_argument("--video", help="video file (default: the indexed/located file)")
    s.add_argument("--model", default="small.en")

    s = sub.add_parser("process-all", help="whole pipeline for every episode file under a folder (resumable)")
    s.add_argument("path")
    s.add_argument("--whisper", choices=["auto", "always", "never"], default="auto",
                   help="auto: only when no subtitles are available")
    s.add_argument("--model", default=None, help="Whisper model (default small.en)")
    s.add_argument("--season", type=int)
    s.add_argument("--episodes", help="comma-separated codes to limit the run")
    s.add_argument("--min-words", type=int, default=1)
    s.add_argument("--candidates", action="store_true")
    s.add_argument("--limit", type=int, help="frames per episode (for a quick trial)")

    s = sub.add_parser("export-static", help="build the public static site (search runs in the browser)")
    s.add_argument("--out", default="site")
    s.add_argument("--image-base", default="images/",
                   help="URL prefix for screengrabs, e.g. https://images.example.com/ (default: images/ next to the site)")
    s.add_argument("--copy-images", action="store_true", help="copy images into OUT/images (for local preview)")
    s.add_argument("--site-url", help="public URL of the site, e.g. https://archive.example.com/ (enables share pages)")
    s.add_argument("--share-base", help="public URL prefix of the share pages (default: IMAGE_BASE + 'q/')")
    s.add_argument("--share-out", default="share", help="where to write share pages (upload to SHARE_BASE)")

    sub.add_parser("build-index", help="rebuild the FTS index and regenerate the manifest")
    sub.add_parser("manifest", help="regenerate episode manifest statuses")
    sub.add_parser("stats", help="print database counts")

    s = sub.add_parser("search", help="search from the command line")
    s.add_argument("query", nargs="+")
    s.add_argument("--limit", type=int, default=15)

    s = sub.add_parser("serve", help="run the web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    s = sub.add_parser("demo", help="build the synthetic static demo")
    s.add_argument("--out", default="demo")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load()
    if args.command == "demo":
        cmd_demo(args)
        return 0
    if args.command == "inspect-media":
        cmd_inspect_media(cfg, args)
        return 0
    conn = db.connect(cfg.db_path)
    handler = getattr(args, "func", None) or globals()["cmd_" + args.command.replace("-", "_")]
    try:
        handler(cfg, conn, args)
    except KeyboardInterrupt:
        return 130
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
