from archive.alignment import align, similarity
from archive.models import Cue
from archive.normalize import normalize


def test_speaker_markup_does_not_prevent_match():
    t = normalize("That's what fog choir practice is for.")
    s = normalize("<i>That's what fog choir\npractice is for.</i>")
    assert similarity(t, s) > 0.95


def test_align_handles_wording_splits_and_missing_lines():
    lines = [
        "Please unfold the meeting rug.",
        "Our cabinet is humming again.",
        "This line never made it into the subtitles at all.",
        "I collect jars of fog, and I label them by pitch.",
        "That's what fog choir practice is for.",
        "Yeah.",
    ]
    cues = [
        Cue(1, 1000, 4000, "Please unfold the meeting rug. Our cabinet is humming again."),  # two lines in one cue
        Cue(2, 9000, 11000, "I collect jars of fog"),                                 # one line over two cues
        Cue(3, 11000, 13000, "and I label them by pitch."),
        Cue(4, 13500, 15500, "That's what fog choir practice is for!"),
        Cue(5, 16000, 16500, "Yeah."),
    ]
    by_line = {m.line_index: m for m in align(lines, cues)}
    assert 2 not in by_line                      # no false match for the missing line
    assert by_line[0].start == 1000 and by_line[1].end == 4000
    assert by_line[0].end <= by_line[1].start
    assert (by_line[3].start, by_line[3].end) == (9000, 13000)
    assert (by_line[4].start, by_line[4].end) == (13500, 15500)
    assert by_line[4].confidence > 0.9
    assert by_line[5].start == 16000


def test_alignment_is_monotonic():
    lines = ["Yeah.", "No.", "Yeah."]
    cues = [Cue(1, 0, 500, "Yeah."), Cue(2, 600, 900, "No."), Cue(3, 1000, 1500, "Yeah.")]
    starts = [m.start for m in align(lines, cues)]
    assert starts == sorted(starts) == [0, 600, 1000]


def test_fixture_episode_fully_aligned(loaded):
    rows = loaded.execute(
        "SELECT timing_source, speaker, start_ms FROM dialogue d JOIN episodes e ON e.id = d.episode_id "
        "WHERE episode_code = 'S00E01'").fetchall()
    assert len(rows) == 14
    assert all(r["timing_source"] == "aligned" and r["start_ms"] is not None for r in rows)
    assert all(r["speaker"] for r in rows)  # transcript speakers kept
