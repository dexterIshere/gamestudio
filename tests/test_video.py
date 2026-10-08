"""Reference video: probe, extraction, manifest, contact sheet.

Nothing is shipped in the repository: each video is made here by ffmpeg
(lavfi's `testsrc`), hence without network and without a heavy binary to
version. Without ffmpeg, these tests are explicitly skipped -- never green by
accident.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from gamestudio import service
from gamestudio.config import Settings
from gamestudio.pipeline.base import Context
from gamestudio.service import NotFound, ServiceError
from gamestudio.store.assets import AssetStore
from gamestudio.store.db import Database
from gamestudio.store.library import Librarian
from gamestudio.video import (
    VideoError,
    contact_sheet,
    grid_columns,
    import_video,
    plan_extraction,
    probe,
    target_size,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

pytestmark = pytest.mark.skipif(
    not (FFMPEG and FFPROBE),
    reason="ffmpeg/ffprobe missing: frame extraction is local and depends on them")


def synthetic(path: Path, *, seconds: float = 2.0, rate: int = 25,
              size: str = "160x120") -> Path:
    """Make a synthetic video: an animated test pattern, deterministic, offline."""
    source = f"testsrc=size={size}:rate={rate}:duration={seconds}"
    # libx264 is not guaranteed everywhere; mpeg4 is always built into ffmpeg
    # and is enough -- what is tested is the processing, not the codec.
    for codec in ("libx264", "mpeg4"):
        result = subprocess.run(
            [FFMPEG, "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", source,
             "-c:v", codec, "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
            capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and path.exists():
            return path
    raise RuntimeError(f"ffmpeg could not produce the video: {result.stderr}")


@pytest.fixture()
def video(tmp_path: Path) -> Path:
    return synthetic(tmp_path / "run.mp4")


@pytest.fixture()
def ctx(tmp_path: Path) -> Context:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    settings.ensure_dirs()
    return Context(project="captures", settings=settings,
                   db=Database(settings.db_path),
                   store=AssetStore(settings.assets_dir))


@pytest.fixture()
def studio(tmp_path: Path):
    """The whole studio, substituted: what the service sees."""
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    with service.using(service.build(settings)) as current:
        yield current


# ----------------------------------------------------------------------- probe


def test_the_probe_says_what_the_video_is(video: Path):
    info = probe(video)
    assert info.duration == pytest.approx(2.0, abs=0.05)
    assert info.fps == pytest.approx(25.0, abs=0.01)
    assert (info.width, info.height) == (160, 120)
    assert info.frames == pytest.approx(50, abs=2)
    assert info.codec, "the codec is always named"
    assert info.path == str(video.resolve())


def test_an_unreadable_file_is_refused_before_any_work(tmp_path: Path):
    """The refusal comes before extraction, not in the middle of it."""
    fake = tmp_path / "not-a-video.mp4"
    fake.write_text("this is not a video", encoding="utf-8")
    with pytest.raises(VideoError):
        probe(fake)
    with pytest.raises(VideoError):
        probe(tmp_path / "missing.mp4")


# ------------------------------------------------------------------------ plan


def test_the_cap_lowers_the_frame_rate_instead_of_truncating(video: Path):
    """The batch covers the whole range, even capped: the frame rate drops."""
    info = probe(video)
    free = plan_extraction(info, fps=25.0, max_frames=1000)
    assert free.count == 50, "two seconds at 25 fps"

    capped = plan_extraction(info, fps=25.0, max_frames=10)
    assert capped.count == 10
    assert capped.fps == pytest.approx(5.0)
    assert (capped.start, capped.end) == (0.0, 2.0), "the range stays whole"
    assert capped.timestamps[-1] == pytest.approx(1.8), "the last frame is at the end"


def test_the_range_bounds_the_timestamps(video: Path):
    plan = plan_extraction(probe(video), fps=10.0, start=0.5, end=1.0)
    assert plan.count == 5
    assert plan.timestamps[0] == pytest.approx(0.5)
    assert max(plan.timestamps) < 1.0
    assert all(0.5 <= stamp < 1.0 for stamp in plan.timestamps)


def test_an_empty_range_is_refused(video: Path):
    info = probe(video)
    with pytest.raises(VideoError):
        plan_extraction(info, start=5.0)
    with pytest.raises(VideoError):
        plan_extraction(info, start=1.0, end=1.0)
    with pytest.raises(VideoError):
        plan_extraction(info, fps=0.0)
    with pytest.raises(VideoError):
        plan_extraction(info, max_frames=0)


def test_the_width_never_enlarges_the_video(video: Path):
    info = probe(video)
    assert target_size(info, 480) == (160, 120), "no upscaling"
    assert target_size(info, 80) == (80, 60)
    assert target_size(info, 0) == (160, 120), "0: the source's size"


# ----------------------------------------------------------------------- batch


def test_the_batch_is_filed_in_the_library(video: Path, ctx: Context):
    batch = import_video(video, ctx, name="run", fps=8.0, max_frames=8)
    assert len(batch.frames) == 8
    assert [a.meta["index"] for a in batch.frames] == list(range(1, 9))
    stamps = [a.meta["timestamp"] for a in batch.frames]
    assert stamps == sorted(stamps), "frames are timestamped in order"
    assert all(a.meta["role"] == "video_frame" for a in batch.frames)

    librarian = Librarian(ctx.db, ctx.store, ctx.settings.library_dir)
    librarian.sync_project("captures")
    folder = librarian.project_dir("captures") / "video" / batch.key
    assert len(sorted((folder / "frames").glob("*.jpg"))) == 8
    assert (folder / "frames" / "0001.jpg").is_file()
    assert (folder / "contact.png").is_file()

    manifest = json.loads((folder / "frames.json").read_text(encoding="utf-8"))
    assert manifest["source"] == str(video.resolve()), "where the video comes from"
    assert manifest["video"]["fps"] == pytest.approx(25.0), "source frame rate"
    assert manifest["video"]["duration"] == pytest.approx(2.0, abs=0.05)
    assert manifest["count"] == 8
    assert [f["file"] for f in manifest["frames"]][:2] == ["0001.jpg", "0002.jpg"]
    assert all(f["timestamp"] >= 0 for f in manifest["frames"])
    assert all((f["width"], f["height"]) == (160, 120) for f in manifest["frames"])

    # The project manifest lists the batch, as it lists icon sheets.
    project = json.loads((librarian.project_dir("captures") / "manifest.json")
                         .read_text(encoding="utf-8"))
    assert project["video_batches"] == [batch.key]


def test_the_same_extraction_gives_the_same_assets(video: Path, tmp_path: Path):
    """Determinism: same video, same parameters, same identifiers."""
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    settings.ensure_dirs()

    def run() -> tuple[str, list[str], str | None]:
        ctx = Context(project="captures", settings=settings,
                      db=Database(settings.db_path),
                      store=AssetStore(settings.assets_dir))
        batch = import_video(video, ctx, name="run", fps=8.0, max_frames=6)
        manifest = batch.manifest.id if batch.manifest else None
        return batch.key, [a.id for a in batch.frames], manifest

    first = run()
    second = run()
    assert first[0] == second[0], "the same batch"
    assert first[1] == second[1], "the same frames, byte for byte"
    assert first[2] == second[2], "the same manifest"


def test_two_frame_rates_are_two_batches(video: Path, ctx: Context):
    """Otherwise the second extraction would overwrite the first in its folder."""
    slow = import_video(video, ctx, name="run", fps=4.0, max_frames=4)
    fast = import_video(video, ctx, name="run", fps=8.0, max_frames=8)
    assert slow.key != fast.key
    assert len(slow.frames) != len(fast.frames)


def test_the_contact_sheet_keeps_one_cell_per_frame(video: Path, tmp_path: Path):
    """A single cell size for the whole batch: the subject does not jump.

    This is the sprite sheets' rule (`sprites.py`): cropping or resizing each
    cell to its own content would move the subject from one cell to the next.
    The grid must therefore be exactly divisible by the cell size, and count
    one cell per frame.
    """
    ctx = Context(project="captures",
                  settings=Settings(data_dir=tmp_path / "data", project_root=tmp_path))
    batch = import_video(video, ctx, name="run", fps=8.0, max_frames=6,
                         width=80, cell_width=40)
    assert batch.contact is not None
    assert all(a.meta["pixels"] == [80, 60] for a in batch.frames)

    with Image.open(batch.contact.path) as sheet:
        size = sheet.size
    cell_width, cell_height = 40 + 2, 30 + 2   # the cell, plus its border line
    assert size[0] % cell_width == 0
    assert size[1] % cell_height == 0
    columns, rows = size[0] // cell_width, size[1] // cell_height
    assert columns * rows == len(batch.frames), "one cell per frame, not one more"


def test_the_grid_is_complete_when_a_divisor_exists():
    """An empty cell would read as a frame that does not exist.

    The grid thus falls back on a divisor of the count -- but not at the price
    of a column twenty rows long: a prime count keeps the maximum fill.
    """
    assert grid_columns(24, 16) == 12
    assert grid_columns(6, 4) == 3
    assert grid_columns(120, 16) == 15
    assert grid_columns(7, 4) == 4, "prime count: one empty cell, not twenty rows"
    assert grid_columns(1, 8) == 1
    assert grid_columns(5, 0) == 1, "no width: a single column"


def test_the_sheet_falls_back_on_a_complete_grid(tmp_path: Path):
    """Six cells in a four-wide width: 3 x 2, not 4 + 2 empty ones."""
    frames = []
    for index in range(6):
        path = tmp_path / f"g{index}.png"
        Image.new("RGB", (80, 60), (10 * index, 40, 90)).save(path)
        frames.append(path)
    sheet = contact_sheet(frames, tmp_path / "grid.png", cell_width=40, max_width=168)
    with Image.open(sheet) as image:
        assert image.size == (3 * 42, 2 * 32), "three columns, two full rows"


def test_the_sheet_can_be_asked_for_alone(tmp_path: Path, video: Path):
    """`contact_sheet` knows neither the store nor the video: it assembles."""
    frames = []
    for index in range(4):
        path = tmp_path / f"f{index}.png"
        Image.new("RGB", (60, 40), (10 * index, 40, 90)).save(path)
        frames.append(path)
    sheet = contact_sheet(frames, tmp_path / "sheet.png", cell_width=30)
    with Image.open(sheet) as image:
        assert image.size == (4 * 32, 22), "four 30x20 cells, border line included"
    with pytest.raises(VideoError):
        contact_sheet([], tmp_path / "empty.png")


# --------------------------------------------------------------------- service


def test_the_service_files_the_batch_and_returns_existing_paths(
        video: Path, studio):
    """The returned paths come from the actual filing, not from a recomputed name."""
    result = service.video.extract_frames(str(video), name="run", fps=8.0,
                                          max_frames=6)
    assert result["project"] == "imports"
    assert result["count"] == 6
    assert Path(result["library"]).is_dir(), result["library"]
    assert Path(result["frames_folder"]).is_dir()
    assert Path(result["manifest"]).is_file()
    assert Path(result["contact_sheet"]).is_file()
    assert len(list(Path(result["frames_folder"]).glob("*.jpg"))) == 6


def test_the_service_honors_the_range(video: Path, studio):
    result = service.video.extract_frames(str(video), fps=10.0, start=0.5, end=1.0)
    assert result["count"] == 5
    assert result["start"] == pytest.approx(0.5)
    assert result["end"] == pytest.approx(1.0)


def test_the_service_translates_refusals(tmp_path: Path, studio):
    """A missing file is a 404, an unreadable file a caller's mistake."""
    with pytest.raises(NotFound):
        service.video.extract_frames(str(tmp_path / "missing.mp4"))

    fake = tmp_path / "fake.mp4"
    fake.write_text("not a video", encoding="utf-8")
    with pytest.raises(ServiceError):
        service.video.extract_frames(str(fake))

    with pytest.raises(ServiceError):
        service.video.extract_frames(str(fake), max_frames=0)
