"""Import of outside files: a single image, or a sheet to split.

Everything is local and free: no Runware call. Matting an opaque sheet uses the
local model (BiRefNet through rembg) when it is installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..pipeline.base import Context
from .context import space
from .errors import NotFound


def _source(path: str) -> Path:
    source = Path(path).expanduser()
    if not source.exists():
        raise NotFound(f"file not found: {source}")
    return source


def import_image(path: str, matting: bool = False,
                 project: str = "imports") -> dict[str, Any]:
    """Import an outside image into the store (matted locally if `matting`).

    The new asset is then handled like a concept the studio drew: its pose can
    be measured, it can be chosen for a card, it can go to 3D.
    """
    from ..vision.detect import import_image as do_import

    source = _source(path)
    st = space(project)
    with Context(project=project, settings=st.settings, db=st.db,
                 store=st.store) as ctx:
        asset = do_import(source, ctx, matting=matting)
        ctx.db.save_asset(asset)
    st.librarian.sync_project(project)
    return {"asset_id": asset.id, "path": str(asset.path), "project": project}


def import_bytes(data: bytes, filename: str, matting: bool = False,
                 project: str = "imports") -> dict[str, Any]:
    """The same from an upload, without a local path.

    The interface runs in a web view: it has bytes, not a path the server could
    read. The file is written to a temporary location, then imported through
    the same code path so the matting rule is not duplicated.
    """
    import tempfile

    suffix = Path(filename).suffix or ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    try:
        return import_image(str(temporary), matting=matting, project=project)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_sheet(path: str, strategy: str = "auto", gap: float | None = None,
                  min_size: float | None = None, rows: int = 0, columns: int = 0,
                  matting: bool = True) -> dict[str, Any]:
    """Free preview of how a sheet splits, writing nothing.

    If the element count is wrong, `gap` fixes it: raising it rejoins a drawing
    that fell apart, lowering it separates two drawings that touch.
    """
    from ..sheet import inspect_sheet as do_inspect

    return do_inspect(_source(path), strategy=strategy, gap=gap, min_size=min_size,
                      rows=rows, columns=columns, matting=matting).summary()


def import_sheet(path: str, multi: bool = True, project: str = "imports",
                 strategy: str = "auto", gap: float | None = None,
                 min_size: float | None = None, rows: int = 0, columns: int = 0,
                 keep: list[str] | None = None, raster_size: int = 0,
                 matting: bool = True) -> dict[str, Any]:
    """Import a sheet: one file per element if `multi`.

    Each element becomes a standalone asset, filed under
    `.gamestudio/library/icons/<sheet>/`.
    """
    from ..sheet import import_sheet as do_import

    source = _source(path)
    st = space(project)
    with Context(project=project, settings=st.settings, db=st.db,
                 store=st.store) as ctx:
        result = do_import(source, ctx, multi=multi, strategy=strategy, gap=gap,
                           min_size=min_size, rows=rows, columns=columns,
                           keep=keep, raster_size=raster_size, matting=matting)
    st.librarian.sync_project(project)
    summary = result.summary()
    summary["library"] = str(
        st.librarian.project_dir(project) / "icons" / result.sheet_name)
    summary["project"] = project
    return summary
