"""Frame extraction: the plan first, ffmpeg next.

Two rules carry everything else:

- range and cap translate into an **effective frame rate**, never a
  truncation. A ten-minute capture capped at 120 frames gives 120 frames spread
  over the ten minutes, not the first four seconds;
- the manifest's timestamps are those of ffmpeg's `fps` filter, not an assumed
  sequence: both come from the same rate, so they match.

Local, free and deterministic: the same file and parameters give the same
bytes, hence the same assets in the hash-addressed store.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .probe import VideoError, VideoInfo, tail, tool

# Hard cap on a batch's frame count: the disk's safeguard, above the cap that
# can be requested (see importer.MAX_FRAMES).
MAX_COUNT = 1000

# ffmpeg's JPEG quality: 2 is the best, 31 the worst. 3 gives sharp frames for
# a few dozen kilobytes.
JPEG_QUALITY = 3

FRAME_PATTERN = "frame_%04d.jpg"


@dataclass(frozen=True)
class ExtractionPlan:
    """What will be extracted: the range, the effective frame rate and the timestamps."""

    start: float
    end: float
    fps: float
    requested_fps: float
    max_frames: int
    count: int
    timestamps: tuple[float, ...]

    @property
    def window(self) -> float:
        return self.end - self.start

    def summary(self) -> dict[str, float]:
        return {"start": round(self.start, 3), "end": round(self.end, 3),
                "fps": round(self.fps, 4), "requested_fps": round(self.requested_fps, 4),
                "max_frames": self.max_frames, "count": self.count,
                "window": round(self.window, 3)}


def plan_extraction(info: VideoInfo, *, fps: float = 4.0, start: float = 0.0,
                    end: float | None = None,
                    max_frames: int = 120) -> ExtractionPlan:
    """Turn frame rate, range and cap into an extraction plan.

    The cap does not cut the end of the range: it lowers the frame rate. That is
    the difference between a decimated reference -- showing the whole video --
    and a truncated one, which only shows its start.
    """
    if fps <= 0:
        raise VideoError(f"invalid frame rate: {fps} (it must be positive)")
    if max_frames < 1:
        raise VideoError(f"invalid cap: {max_frames} (at least one frame is needed)")
    if max_frames > MAX_COUNT:
        raise VideoError(f"cap too high: {max_frames} (maximum {MAX_COUNT})")
    if start < 0:
        raise VideoError(f"invalid start: {start} (it cannot be negative)")

    # A range requested beyond the video is brought back to what it contains: a
    # convenience, not an error -- the caller does not always know the exact
    # duration of the capture.
    begin = min(float(start), info.duration)
    finish = info.duration if end is None else min(float(end), info.duration)
    if finish <= begin:
        raise VideoError(
            f"empty range: {begin:.3f}s .. {finish:.3f}s "
            f"(the video lasts {info.duration:.3f}s)")

    window = finish - begin
    count = max(1, min(math.ceil(window * fps), max_frames))
    effective = count / window
    timestamps = tuple(round(begin + index / effective, 6) for index in range(count))
    return ExtractionPlan(start=begin, end=finish, fps=effective, requested_fps=fps,
                          max_frames=max_frames, count=count, timestamps=timestamps)


def target_size(info: VideoInfo, width: int) -> tuple[int, int]:
    """Size of the extracted frames: the requested width, without upscaling.

    Height follows the source's ratio and stays even: a yuv420p JPEG encoder
    refuses an odd dimension, and every other frame at a different size would
    make the subject move from one cell to the next on the contact sheet. The
    size is thus set once for the whole batch, like the cropping of a sprite
    sheet.
    """
    if width <= 0 or width >= info.width:
        return (info.width, info.height)
    target_width = max(2, width - (width % 2))
    target_height = max(2, round(info.height * target_width / info.width))
    return (target_width, target_height - (target_height % 2))


def extract(source: str | Path, out_dir: str | Path, plan: ExtractionPlan, *,
            size: tuple[int, int], quality: int = JPEG_QUALITY,
            timeout: int = 600) -> list[Path]:
    """Write a plan's frames into `out_dir`. Return what actually exists.

    A single ffmpeg invocation: `-ss` before `-i` (fast, yet exact, seeking),
    `-t` after (the range's duration), then an `fps` filter that samples and a
    `scale` that sets the size. The effective rate is the plan's, to six
    decimals: that is what makes the manifest's timestamps true.
    """
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    width, height = size
    source = Path(source)

    argv = [
        tool("ffmpeg"), "-v", "error", "-nostdin", "-y",
        "-ss", f"{plan.start:.6f}", "-i", str(source),
        "-t", f"{plan.window:.6f}",
        "-an", "-vf", f"fps={plan.fps:.6f},scale={width}:{height}",
        "-q:v", str(quality), "-frames:v", str(plan.count),
        "-f", "image2", "-start_number", "1",
        str(target / FRAME_PATTERN),
    ]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)

    frames = extracted(target)
    if not frames:
        # The return code alone says little: the absence of frames is what
        # matters, and ffmpeg's last line explains it.
        reason = tail(result.stderr) if result.returncode else "no frame"
        raise VideoError(f"{source.name}: nothing was extracted from "
                         f"{plan.start:.3f}s .. {plan.end:.3f}s ({reason})")
    return frames


def extracted(out_dir: str | Path) -> list[Path]:
    """The frames actually written, in batch order."""
    return sorted(Path(out_dir).glob(FRAME_PATTERN.replace("%04d", "*")))
