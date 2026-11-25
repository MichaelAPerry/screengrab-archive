from archive import db
from archive.models import Episode, Line, Origin
from archive.pipeline import align_episode, import_subtitles, import_transcripts
from archive.sources.plaintext import PlainTextSource
from tests.conftest import FIXTURES

quiet = lambda *_: None  # noqa: E731


def counts(conn):
    return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("episodes", "dialogue", "subtitle_cues", "sources"))


def test_reimport_is_idempotent(loaded):
    before = counts(loaded)
    ids_before = [r[0] for r in loaded.execute("SELECT id FROM dialogue ORDER BY id")]
    import_transcripts(loaded, PlainTextSource(FIXTURES / "transcripts"), log=quiet)
    import_subtitles(loaded, FIXTURES / "subtitles", source_name="fixture", log=quiet)
    assert counts(loaded) == before
    assert [r[0] for r in loaded.execute("SELECT id FROM dialogue ORDER BY id")] == ids_before


def test_unchanged_transcript_keeps_alignment(loaded):
    import_transcripts(loaded, PlainTextSource(FIXTURES / "transcripts"), log=quiet)
    timed = loaded.execute("SELECT COUNT(*) FROM dialogue WHERE timing_source = 'aligned'").fetchone()[0]
    assert timed == 14


def test_timing_kinds_are_distinguished(loaded):
    kinds = dict(loaded.execute(
        "SELECT e.episode_code, GROUP_CONCAT(DISTINCT d.timing_source) FROM dialogue d "
        "JOIN episodes e ON e.id = d.episode_id GROUP BY e.episode_code").fetchall())
    assert kinds == {"S00E01": "aligned", "S00E02": "subtitle"}
    origins = dict(loaded.execute(
        "SELECT e.episode_code, d.origin FROM dialogue d JOIN episodes e ON e.id = d.episode_id GROUP BY 1").fetchall())
    assert origins == {"S00E01": "transcript", "S00E02": "subtitle"}


def test_manual_timing_survives_realignment(loaded):
    row = loaded.execute("SELECT id FROM dialogue WHERE text LIKE '%fog choir%'").fetchone()
    loaded.execute("UPDATE dialogue SET start_ms = 1, end_ms = 2, timing_source = 'manual' WHERE id = ?", [row[0]])
    ep_id = loaded.execute("SELECT id FROM episodes WHERE episode_code = 'S00E01'").fetchone()[0]
    align_episode(loaded, ep_id)
    r = loaded.execute("SELECT start_ms, timing_source FROM dialogue WHERE id = ?", [row[0]]).fetchone()
    assert tuple(r) == (1, "manual")


def test_fts_stays_in_sync_after_replacement(conn):
    ep = Episode(1, 1, title="T", source="x", origin=Origin.TRANSCRIPT, lines=[Line("old words here", 1)])
    db.replace_dialogue(conn, ep)
    ep.lines = [Line("brand new words", 1)]
    db.replace_dialogue(conn, ep)
    hits = lambda term: conn.execute(  # noqa: E731
        "SELECT COUNT(*) FROM dialogue_fts WHERE dialogue_fts MATCH ?", [term]).fetchone()[0]
    assert hits("old") == 0 and hits("brand") == 1


def test_transcript_after_subtitle_only_takes_over(conn):
    import_subtitles(conn, FIXTURES / "subtitles" / "S00E01.srt", source_name="fixture", log=quiet)
    assert conn.execute("SELECT DISTINCT origin FROM dialogue").fetchall()[0][0] == "subtitle"
    import_transcripts(conn, PlainTextSource(FIXTURES / "transcripts"), log=quiet)
    rows = conn.execute("SELECT origin, timing_source FROM dialogue").fetchall()
    assert {tuple(r) for r in rows} == {("transcript", "aligned")}
