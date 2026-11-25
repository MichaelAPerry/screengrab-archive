from pathlib import Path

import pytest

from archive import db
from archive.pipeline import import_subtitles, import_transcripts
from archive.sources.plaintext import PlainTextSource

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def loaded(conn):
    """Fixture transcript (S00E01) aligned to fixture subtitles, plus subtitle-only S00E02."""
    quiet = lambda *_: None  # noqa: E731
    import_transcripts(conn, PlainTextSource(FIXTURES / "transcripts"), log=quiet)
    import_subtitles(conn, FIXTURES / "subtitles", source_name="fixture", log=quiet)
    return conn
