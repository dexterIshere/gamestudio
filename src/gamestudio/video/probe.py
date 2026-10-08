"""Probing a video: what ffprobe says about the file, and what is refused.

Nothing is guessed: duration, frame rate and dimensions come from ffprobe. A
file it cannot read is refused here, with a readable reason, rather than
making the extraction fail halfway -- after filling the disk with unusable
frames.

Local and free: ffprobe, no network call.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VideoError(ValueError):
    """The file is not a usable video, or the local tool is missing."""


@dataclass(frozen=True)
class VideoInfo:
    """What a video is: enough to plan an extraction without decoding it."""

    path: str
    duration: float
    fps: float
    width: int
    height: int
    codec: str
    frames: int

    def summary(self) -> dict[str, Any]:
        return {"path": self.path, "duration": round(self.duration, 3),
                "fps": round(self.fps, 3), "width": self.width,
                "height": self.height, "codec": self.codec, "frames": self.frames}


def tool(name: str) -> str:
    """Path of a required binary, or a refusal that says which one is missing.

    Extraction is local and has no fallback: without ffmpeg, there are no
    frames at all. Saying so here beats a `FileNotFoundError` deep inside
    `subprocess`.
    """
    binary = shutil.which(name)
    if binary is None:
        raise VideoError(f"{name} not found on the PATH: frame extraction is "
                         "local and depends on it")
    return binary


def _fraction(value: Any) -> float:
    """`30000/1001` -> 29.97; 0 when ffprobe says nothing usable."""
    if not isinstance(value, str) or not value:
        return 0.0
    numerator, _, denominator = value.partition("/")
    try:
        top, bottom = float(numerator), float(denominator or 1)
    except ValueError:
        return 0.0
    return top / bottom if bottom else 0.0


def _seconds(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def tail(text: str, limit: int = 200) -> str:
    """Last non-empty line of a tool's output: the only one that gives the real reason."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "no message")[:limit]


def probe(path: str | Path) -> VideoInfo:
    """Probe a video with ffprobe. Plainly refuse what it cannot read.

    Duration drives everything else -- range, cap, timestamps: it is therefore
    required, and derived from the frame count and frame rate when the header
    does not give it.
    """
    source = Path(path).expanduser()
    if not source.is_file():
        raise VideoError(f"file not found: {source}")

    result = subprocess.run(
        [tool("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(source)],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise VideoError(f"{source.name}: unreadable ({tail(result.stderr)})")
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise VideoError(f"{source.name}: ffprobe returned no JSON") from exc

    streams = [s for s in payload.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise VideoError(f"{source.name}: no video track")
    stream = streams[0]

    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise VideoError(f"{source.name}: unreadable dimensions")

    # `avg_frame_rate` is the actual rate of a variable-rate file; `r_frame_rate`
    # that of a raw stream, which does not declare the former.
    fps = _fraction(stream.get("avg_frame_rate")) or _fraction(stream.get("r_frame_rate"))
    if fps <= 0:
        raise VideoError(f"{source.name}: unreadable frame rate")

    frames = int(stream.get("nb_frames") or 0)
    duration = _seconds(payload.get("format", {}).get("duration"))
    if duration <= 0:
        duration = _seconds(stream.get("duration"))
    if duration <= 0 and frames > 0:
        duration = frames / fps
    if duration <= 0:
        raise VideoError(f"{source.name}: unreadable duration")
    if frames <= 0:
        frames = round(duration * fps)

    return VideoInfo(path=str(source.resolve()), duration=duration, fps=fps,
                     width=width, height=height,
                     codec=str(stream.get("codec_name") or ""), frames=frames)
