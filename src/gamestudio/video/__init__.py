"""Reference video: what it takes to draw usable frames from it.

A filmed walk cycle, a captured run: images wanted to time or judge an
animation. This module turns a video into frames, locally and for free
(ffmpeg/ffprobe), without ever calling Runware:

- `probe` says what the file is (duration, frame rate, dimensions, codec) and
  plainly refuses what it cannot read;
- `plan_extraction` turns frame rate, range and cap into a plan -- the cap
  becomes a frame rate, never a truncation;
- `extract` writes the frames as JPEG, in a single ffmpeg invocation;
- `import_video` puts everything in the store with the metadata that ties each
  frame to its batch, and `Librarian.index_project` files the batch in
  `.gamestudio/library/video/<batch>/`;
- `contact_sheet` assembles the batch into a single image, to judge it at a
  glance.

Deterministic: the same file and parameters give the same bytes, hence the
same assets in the hash-addressed store.
"""

from __future__ import annotations

from .contact import CONTACT_NAME, contact_sheet, grid_columns
from .frames import ExtractionPlan, extract, extracted, plan_extraction, target_size
from .importer import (
    CONTACT_ROLE,
    DEFAULT_CELL_WIDTH,
    DEFAULT_FPS,
    DEFAULT_MAX_FRAMES,
    DEFAULT_WIDTH,
    FRAME_ROLE,
    MANIFEST_NAME,
    MANIFEST_ROLE,
    MAX_FRAMES,
    MAX_WIDTH,
    VideoBatch,
    batch_key,
    import_video,
)
from .probe import VideoError, VideoInfo, probe

__all__ = [
    "CONTACT_NAME",
    "CONTACT_ROLE",
    "DEFAULT_CELL_WIDTH",
    "DEFAULT_FPS",
    "DEFAULT_MAX_FRAMES",
    "DEFAULT_WIDTH",
    "FRAME_ROLE",
    "MANIFEST_NAME",
    "MANIFEST_ROLE",
    "MAX_FRAMES",
    "MAX_WIDTH",
    "ExtractionPlan",
    "VideoBatch",
    "VideoError",
    "VideoInfo",
    "batch_key",
    "contact_sheet",
    "extract",
    "extracted",
    "grid_columns",
    "import_video",
    "plan_extraction",
    "probe",
    "target_size",
]
