"""Reference video: extract a batch of usable frames from it.

Everything is local and free: ffmpeg and ffprobe, no Runware call, no credit
spent. The frames, their contact sheet and their manifest go into the
hash-addressed store, and the batch is filed under
`.gamestudio/library/video/<batch>/` (see `store/library.py`).

This is the only definition of the operation: the CLI, the HTTP API and the
MCP server just call it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..pipeline.base import Context
from ..video import VideoError
from ..video.importer import CONTACT_NAME, MANIFEST_NAME
from ..video.importer import import_video as do_import
from .context import space
from .errors import NotFound, ServiceError

# Default host project, the same as imported images and sheets: a reference
# capture does not necessarily belong to a roster project.
FREE_PROJECT = "imports"


def extract_frames(path: str, name: str = "", project: str = FREE_PROJECT,
                   fps: float = 4.0, start: float = 0.0, end: float | None = None,
                   max_frames: int = 120, width: int = 480, cell_width: int = 240,
                   contact: bool = True) -> dict[str, Any]:
    """Extract frames from a reference video, locally and for free.

    - `fps`: requested rate, in frames per second of video;
    - `start` / `end`: range in seconds (`end` omitted: up to the end);
    - `max_frames`: cap on the batch. When it bites, the rate drops and the
      range stays whole -- a ten-minute capture gives a hundred and twenty
      frames spread over the ten minutes, never the first four seconds;
    - `width`: frame width, never upscaled;
    - `contact`: produce the contact sheet, which shows the whole batch in one
      image.

    The batch is filed under `.gamestudio/library/video/<batch>/`: the frames
    under `frames/`, plus `frames.json` (where each frame and the video come
    from) and `contact.png`. An unreadable file is refused before anything is
    extracted.
    """
    source = Path(path).expanduser()
    if not source.exists():
        raise NotFound(f"file not found: {source}")

    project = project or FREE_PROJECT
    st = space(project)
    with Context(project=project, settings=st.settings, db=st.db,
                 store=st.store) as ctx:
        try:
            batch = do_import(source, ctx, name=name, fps=fps, start=start, end=end,
                            max_frames=max_frames, width=width, cell_width=cell_width,
                            contact=contact)
        except VideoError as exc:
            # ffmpeg or ffprobe cannot read the file, or a parameter makes no
            # sense: a caller mistake, not a studio bug, so it comes back
            # readable like any other.
            raise ServiceError(str(exc)) from exc

    st.librarian.sync_project(project)
    folder = st.librarian.project_dir(project) / "video" / batch.key
    summary = batch.summary()
    summary["project"] = project
    summary["library"] = str(folder)
    summary["frames_folder"] = str(folder / "frames")
    summary["manifest"] = str(folder / MANIFEST_NAME)
    summary["contact_sheet"] = str(folder / CONTACT_NAME) if batch.contact else ""
    return summary
