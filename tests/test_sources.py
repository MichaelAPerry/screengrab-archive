from archive.sources.foreverdreaming import ForeverDreamingSource
from tests.conftest import FIXTURES


def test_foreverdreaming_saved_page():
    src = ForeverDreamingSource(FIXTURES / "foreverdreaming")
    assert src.available() == [(0, 3)]
    ep = src.load(0, 3)
    assert ep.title == "The Clock Cabinet"
    assert [(ln.speaker, ln.text) for ln in ep.lines] == [
        ("ORBIT", "The clock has filed a complaint."), ("FINCH", "Put it in the drawer marked Tuesday.")]
    assert ep.source_url == "https://example.com/topic"
