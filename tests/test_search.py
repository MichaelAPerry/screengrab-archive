from archive.search import context, parse_query, search


def test_parse_combined_query():
    q = parse_query('speaker:finch "Fog Choir" season:4 episode:s4e8 teacups')
    assert q.speaker == "FINCH"
    assert q.phrases == ["fog choir"]
    assert q.season == 4 and q.episode == "S04E08"
    assert q.words == ["teacups"]


def test_parse_is_robust_to_fts_syntax():
    q = parse_query('AND OR NEAR( "unterminated')
    assert q.fts()  # quoted terms, no exception building it


def test_phrase_search(loaded):
    res = search(loaded, '"fog choir"')
    assert res["total"] == 1
    r = res["results"][0]
    assert r["speaker"] == "FINCH" and r["episode_code"] == "S00E01"
    assert r["timestamp"] == "00:00:17" and r["timing_source"] == "aligned"


def test_exact_phrase_and_short_lines_rank_first(loaded):
    results = search(loaded, "fog")["results"]
    assert results[0]["text"] == "The fog kind."


def test_apostrophes_normalised(loaded):
    assert search(loaded, "thats what")["total"] == 1
    assert search(loaded, "that's what")["total"] == 1


def test_speaker_filter_and_speaker_only_browse(loaded):
    assert {r["speaker"] for r in search(loaded, "speaker:LUMEN")["results"]} == {"LUMEN"}
    res = search(loaded, "speaker:FINCH fog")
    assert res["total"] == 2 and all(r["speaker"] == "FINCH" for r in res["results"])


def test_word_does_not_match_speaker_column(loaded):
    # "quill" is a speaker name but never said aloud in the fixture
    assert search(loaded, "quill")["total"] == 0


def test_episode_and_season_filters(loaded):
    assert search(loaded, "kettle episode:S00E02")["total"] == 1
    assert search(loaded, "kettle episode:S00E01")["total"] == 0
    assert search(loaded, "kettle season:0")["total"] == 1


def test_any_word_fallback(loaded):
    res = search(loaded, "fog zzzunknown")
    assert res["mode"] == "any" and res["total"] > 0


def test_context_window_highlights_selected(loaded):
    line_id = search(loaded, '"fog choir"')["results"][0]["id"]
    ctx = context(loaded, line_id, seconds=10)
    sel = [c for c in ctx["context"] if c["selected"]]
    assert len(sel) == 1 and "fog choir" in sel[0]["text"]
    assert all(abs(c["start_ms"] - 17_800) <= 10_000 for c in ctx["context"])
    assert len(ctx["context"]) > 3
