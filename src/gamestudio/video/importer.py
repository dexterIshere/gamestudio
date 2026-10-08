"""Bringing a video into the studio: store, database, manifest, contact sheet.

The module files nothing in the library: it puts the assets in the store with
the metadata that ties them to their batch, and `Librarian.index_project`
files them -- exactly like an icon sheet (`sheet/importer.py`). Filing thus
exists in a single place, and a batch of frames ends up in
`.gamestudio/library/video/<batch>/` without anything here knowing the
library.

Deterministic: ffmpeg returns the same bytes for the same parameters, and the
store addresses its files by the hash of their content. Replaying the same
extraction thus rewrites the same assets, with the same identifiers -- which
makes a batch reproducible and comparable from one session to the next.

Local and free: ffmpeg/ffprobe, no call to Runware.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.models import Asset
from ..sheet.layout import slug
from .contact import CONTACT_NAME, contact_sheet
from .frames import (
    JPEG_QUALITY,
    ExtractionPlan,
    extract,
    plan_extraction,
    target_size,
)
from .probe import VideoError, VideoInfo, probe

# Roles set on the assets of a batch of frames. `Librarian` uses them to group
# a batch -- see `store/library.py::_video_folders`.
FRAME_ROLE = "video_frame"
CONTACT_ROLE = "video_contact"
MANIFEST_ROLE = "video_manifest"

MANIFEST_NAME = "frames.json"

DEFAULT_FPS = 4.0
DEFAULT_MAX_FRAMES = 120
DEFAULT_WIDTH = 480
DEFAULT_CELL_WIDTH = 240

# Hard cap on a batch's frame count, and maximum frame width. The default (120
# frames of 480 px, ~4 MB) is enough to cover a whole capture; the hard cap
# protects the disk from a call that would ask for ten thousand frames.
MAX_FRAMES = 1000
MAX_WIDTH = 1920


@dataclass
class VideoBatch:
    """What an extraction produced: the frames, the sheet, the manifest."""

    name: str
    key: str
    info: VideoInfo
    plan: ExtractionPlan
    width: int
    height: int
    frames: list[Asset] = field(default_factory=list)
    contact: Asset | None = None
    manifest: Asset | None = None
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "batch": self.key,
            "name": self.name,
            "video": self.info.summary(),
            "start": round(self.plan.start, 3),
            "end": round(self.plan.end, 3),
            "fps": round(self.plan.fps, 4),
            "requested_fps": round(self.plan.requested_fps, 4),
            "max_frames": self.plan.max_frames,
            "width": self.width,
            "height": self.height,
            "count": len(self.frames),
            "contact_asset_id": self.contact.id if self.contact else None,
            "manifest_asset_id": self.manifest.id if self.manifest else None,
            "frames": [_frame_summary(asset) for asset in self.frames],
            "warnings": self.warnings,
        }


def _frame_summary(asset: Asset) -> dict[str, Any]:
    return {
        "index": asset.meta.get("index", 0),
        "timestamp": asset.meta.get("timestamp", 0.0),
        "file": f"{asset.meta.get('name', '')}.jpg",
        "asset_id": asset.id,
        "width": asset.meta.get("pixels", [0, 0])[0],
        "height": asset.meta.get("pixels", [0, 0])[1],
    }


def batch_key(name: str, source: Path, plan: ExtractionPlan, width: int) -> str:
    """Name of the batch's folder: the readable name, plus the parameters' fingerprint.

    Two extractions of the same file at two frame rates are two batches:
    without a fingerprint, the second would overwrite the first in the same
    folder, and `frames.json` could no longer say which one it describes. The
    same extraction replayed lands in the same folder -- which lets it be filed
    twice without duplicating anything.

    The separator is a hyphen, like that of `sheet.layout.slug`: the key is
    already a valid folder name, and filing (`store/library.py`) takes it as is
    instead of producing a variant of it.
    """
    readable = slug(name, max_length=32) or "video"
    try:
        size = source.stat().st_size
    except OSError:
        size = 0
    payload = json.dumps({
        "source": str(source.resolve()), "bytes": size,
        "start": round(plan.start, 6), "end": round(plan.end, 6),
        "fps": round(plan.fps, 6), "width": width,
    }, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}"


def _pixels(data: bytes) -> list[int]:
    """Dimensions of the produced file, measured from its header."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return [image.width, image.height]


def import_video(source: str | Path, ctx, *, name: str = "", fps: float = DEFAULT_FPS,
                 start: float = 0.0, end: float | None = None,
                 max_frames: int = DEFAULT_MAX_FRAMES, width: int = DEFAULT_WIDTH,
                 cell_width: int = DEFAULT_CELL_WIDTH, contact: bool = True,
                 timeout: int = 600) -> VideoBatch:
    """Extract a video's frames and bring them into the studio.

    - `fps`: requested frame rate, in images per second of video;
    - `start` / `end`: range in seconds (`end=None`: to the end);
    - `max_frames`: the batch's cap -- if it bites, the frame rate drops and
      the range stays whole;
    - `width`: width of the frames, without upscaling;
    - `contact`: produce the batch's contact sheet.
    """
    if width > MAX_WIDTH:
        raise VideoError(f"width too large: {width} (maximum {MAX_WIDTH})")

    path = Path(source).expanduser()
    info = probe(path)
    plan = plan_extraction(info, fps=fps, start=start, end=end, max_frames=max_frames)
    size = target_size(info, width)
    label = name or path.stem
    key = batch_key(label, path, plan, size[0])
    batch_name = slug(label, max_length=48) or "video"

    with tempfile.TemporaryDirectory(prefix="gamestudio-video-") as work:
        files = extract(path, Path(work), plan, size=size, timeout=timeout)
        batch = VideoBatch(name=batch_name, key=key, info=info, plan=plan,
                       width=size[0], height=size[1])
        for index, file in enumerate(files, start=1):
            data = file.read_bytes()
            asset = ctx.store.put_bytes(data, ".jpg", meta={
                "role": FRAME_ROLE,
                "project": ctx.project,
                "video": key,
                "name": f"{index:04d}",
                "index": index,
                "timestamp": _stamp(plan, index),
                "source": path.name,
                "pixels": _pixels(data),
                "quality": JPEG_QUALITY,
            })
            ctx.db.save_asset(asset)
            batch.frames.append(asset)

        if contact:
            sheet = contact_sheet(files, Path(work) / CONTACT_NAME,
                                  cell_width=cell_width)
            data = sheet.read_bytes()
            batch.contact = ctx.store.put_bytes(data, ".png", meta={
                "role": CONTACT_ROLE,
                "project": ctx.project,
                "video": key,
                "name": CONTACT_NAME,
                "source": path.name,
                "pixels": _pixels(data),
            })
            ctx.db.save_asset(batch.contact)

        if len(batch.frames) != plan.count:
            batch.warnings.append(
                f"{len(batch.frames)} frames extracted out of {plan.count} planned "
                f"(the requested range exceeds what the video contains)")
        batch.manifest = _save_manifest(ctx, batch, path)
    return batch


def _stamp(plan: ExtractionPlan, index: int) -> float:
    """Timestamp of frame `index` (1-based), in seconds."""
    position = index - 1
    if position >= len(plan.timestamps):
        return round(plan.end, 3)
    return round(plan.timestamps[position], 3)


def _save_manifest(ctx, batch: VideoBatch, source: Path) -> Asset:
    """Write `frames.json`: where each frame comes from, and where the video comes from."""
    payload = {
        "batch": batch.key,
        "name": batch.name,
        "source": batch.info.path,
        "source_name": source.name,
        "video": batch.info.summary(),
        "start": round(batch.plan.start, 3),
        "end": round(batch.plan.end, 3),
        "fps": round(batch.plan.fps, 4),
        "requested_fps": round(batch.plan.requested_fps, 4),
        "max_frames": batch.plan.max_frames,
        "width": batch.width,
        "height": batch.height,
        "count": len(batch.frames),
        "contact": CONTACT_NAME if batch.contact else "",
        "frames": [_frame_summary(asset) for asset in batch.frames],
        "warnings": batch.warnings,
    }
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    asset = ctx.store.put_bytes(data, ".json", kind="data", meta={
        "role": MANIFEST_ROLE,
        "project": ctx.project,
        "video": batch.key,
        "name": MANIFEST_NAME,
        "source": source.name,
        "count": len(batch.frames),
    })
    ctx.db.save_asset(asset)
    return asset
