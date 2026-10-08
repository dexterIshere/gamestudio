"""Preview rendering: an asset image scaled down to a reasonable size.

Shared by the MCP server (which returns the image to an agent) and the API
(which serves it to the interface). An SVG is rasterized on the way: an icon
split from a sheet is vector art, and nothing displays it without rendering.
"""

from __future__ import annotations

import io
from pathlib import Path

from .catalog import asset_path
from .errors import ServiceError

MAX_PREVIEW = 640  # default longest side of a preview

RASTER_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def preview_png(asset_id: str, size: int = MAX_PREVIEW) -> bytes:
    """A preview PNG of an image asset, resized if needed."""
    return render_png(asset_path(asset_id), size)


def render_png(path: Path, size: int = MAX_PREVIEW) -> bytes:
    """The same preview, from a path -- including one outside the store."""
    from PIL import Image

    suffix = path.suffix.lower()
    if suffix == ".svg":
        from ..svg.raster import rasterize
        from ..vision.detect import ToolUnavailable

        try:
            return rasterize(path.read_text(encoding="utf-8"), size)
        except ToolUnavailable as exc:
            # Rasterizing is an optional dependency. A caller that displays
            # images can almost always render an SVG itself: telling it to fall
            # back on the raw file beats a stack trace.
            raise ServiceError(f"{exc} -- serve the raw SVG through /file") from exc
    if suffix not in RASTER_SUFFIXES:
        raise ServiceError(f"{path.name} is not an image ({path.suffix})")

    with Image.open(path) as img:
        return _png(img, size)


def webp_to_png(data: bytes, size: int = MAX_PREVIEW) -> bytes:
    """An in-memory image (the lookdev bench's WebP) as a preview PNG."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        return _png(img, size)


def _png(img, size: int) -> bytes:
    """Encode an image as PNG, its longest side brought down to `size`."""
    from PIL import Image

    img = img.convert("RGBA")
    if max(img.size) > size:
        ratio = size / max(img.size)
        img = img.resize((max(1, int(img.width * ratio)),
                          max(1, int(img.height * ratio))), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
