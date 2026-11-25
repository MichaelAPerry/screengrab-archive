"""Create a synthetic episode and run the normal media pipeline and static export."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from archive import db
from archive.config import Config
from archive.export import export_static
from archive.pipeline import process_all
from archive.video import require_ffmpeg

DEMO_DATA = Path(__file__).with_name("demo_data")


def make_test_video(path: Path, seconds: int = 45, size: str = "854x480") -> Path:
    """Generate a silent episode with a moving FFmpeg test pattern."""
    require_ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
             "-i", f"testsrc=size={size}:rate=24:duration={seconds}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
            check=True,
        )
    return path


def run_demo(out: Path) -> None:
    with TemporaryDirectory(prefix="screengrab-demo-") as folder:
        root = Path(folder)
        cfg = Config(root / "archive.db", root / "media", root / "images", root / "raw",
                     root / "episodes.json", False)
        shutil.copyfile(DEMO_DATA / "episodes.json", cfg.manifest_path)
        video = make_test_video(cfg.media_dir / "S01E01.mp4", seconds=96, size="480x270")
        shutil.copyfile(DEMO_DATA / "S01E01.srt", video.with_suffix(".srt"))
        with closing(db.connect(cfg.db_path)) as conn:
            results = process_all(conn, cfg, cfg.media_dir, whisper="never")
            if results[0]["error"] or results[0]["images"] != 24:
                raise SystemExit("demo build failed to extract all 24 frames")
            export_static(conn, cfg, out, copy_images=True)
        (out / ".nojekyll").touch()
