import shutil

import pytest
from PIL import Image

from archive.images import dhash, hamming, image_relpath, is_near_duplicate, score_image
from archive.video import candidate_timestamps


def test_candidate_timestamps_from_interval():
    start, end = 754_100, 757_500  # 12:34.100 -> 12:37.500
    assert candidate_timestamps(start, end) == [755_800]  # midpoint 12:35.800
    assert candidate_timestamps(start, end, (0.25, 0.5, 0.75)) == [754_950, 755_800, 756_650]
    assert candidate_timestamps(start, end, offset_ms=-1000) == [754_800]


def test_image_path_is_stable():
    assert image_relpath("s04e08", 872_345) == "S04/S04E08/S04E08_000872345.webp"


def test_dhash_detects_near_duplicates():
    a = Image.linear_gradient("L").convert("RGB")
    a = a.transpose(Image.Transpose.ROTATE_90)       # horizontal gradient: dhash compares columns
    b = a.point(lambda v: min(255, v + 3))           # tiny brightness change
    c = a.transpose(Image.Transpose.FLIP_LEFT_RIGHT)  # different picture
    assert hamming(dhash(a), dhash(b)) <= 6
    assert is_near_duplicate(dhash(b), [dhash(a)])
    assert not is_near_duplicate(dhash(c), [dhash(a)])


def test_quality_scores_in_range():
    q, parts = score_image(Image.new("RGB", (64, 36), (0, 0, 0)))
    assert 0 <= q <= 1 and parts["exposure"] == 0.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_end_to_end_frame_extraction(loaded, tmp_path):
    from archive.config import Config
    from archive.pipeline import extract_frames
    from archive.search import search
    from scripts.demo import make_test_video

    cfg = Config(db_path=tmp_path / "x.db", media_dir=tmp_path / "media", images_dir=tmp_path / "images",
                 raw_dir=tmp_path / "raw", manifest_path=tmp_path / "episodes.json", serve_video=False)
    make_test_video(cfg.media_dir / "S00" / "S00E01.mp4", seconds=40)
    stats = extract_frames(loaded, cfg, "S00E01", log=lambda *_: None)
    assert stats["images"] == 14
    r = search(loaded, '"fog choir"')["results"][0]
    assert r["image"] == "S00/S00E01/S00E01_000019050.webp"  # midpoint of 17.8s-20.3s
    img = Image.open(cfg.images_dir / r["image"])
    assert img.format == "WEBP" and img.size == (854, 480)
    # rerun: nothing new extracted, no duplicate rows
    assert extract_frames(loaded, cfg, "S00E01", log=lambda *_: None)["images"] == 0
    assert loaded.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 14
