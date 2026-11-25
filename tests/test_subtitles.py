from archive.models import Cue
from archive.subtitles import format_timestamp, load_subtitle_file, parse_subtitles, parse_timestamp, split_dialogue_turns
from tests.conftest import FIXTURES


def test_parse_timestamp_variants():
    assert parse_timestamp("00:12:34,100") == 754_100
    assert parse_timestamp("12:34.100") == 754_100
    assert parse_timestamp("1:02:03.5") == 3_723_500
    assert format_timestamp(754_100) == "00:12:34"
    assert format_timestamp(754_100, with_ms=True) == "00:12:34.100"


def test_parse_srt_cleans_markup_and_drops_noise():
    cues = load_subtitle_file(FIXTURES / "subtitles" / "S00E01.srt")
    texts = [c.text for c in cues]
    assert "I brought a tuning fork." in texts            # <i> stripped
    assert "That's what fog choir practice is for." in texts  # multi-line joined
    assert not any("LAUGHS" in t for t in texts)        # sound-only cue dropped
    assert not any("Subtitles by" in t for t in texts)  # credit dropped
    fog = next(c for c in cues if "fog choir" in c.text)
    assert (fog.start, fog.end) == (17_800, 20_300)


def test_parse_vtt_with_speaker_and_header():
    cues = load_subtitle_file(FIXTURES / "subtitles" / "S00E02.vtt")
    assert len(cues) == 2
    assert cues[0].speaker == "FINCH" and cues[0].text == "Emergency teacups!"
    assert cues[1].text == "The kettle is wearing a paper crown."
    assert cues[1].start == 4000


def test_srt_speaker_prefix():
    cues = parse_subtitles("1\n00:12:34,100 --> 00:12:37,500\nFINCH: That's a fog choir.\n")
    assert cues[0].speaker == "FINCH" and cues[0].text == "That's a fog choir."


def test_dash_cue_split_shares_interval():
    parts = split_dialogue_turns(Cue(1, 1000, 3000, "- Are we cool? - We're cool!"))
    assert [p.text for p in parts] == ["Are we cool?", "We're cool!"]
    assert parts[0].start == 1000 and parts[-1].end == 3000
    assert parts[0].end == parts[1].start


def test_hyphen_inside_sentence_is_not_a_turn():
    parts = split_dialogue_turns(Cue(1, 0, 1000, "I - I don't know"))
    assert len(parts) == 1
