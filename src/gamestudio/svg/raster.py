"""Optional rasterization of an SVG icon to PNG.

A 2D game engine consumes textures, not XML: rasterizing an extracted icon
makes it directly usable, and gives it a thumbnail in the library. It is never
required, though -- the SVG stays the source, and the split does not depend on
it.

`cairosvg` is therefore an optional dependency (`pip install -e .[vector]`).
Without it, splitting and importing work normally; only an explicit PNG
request raises `ToolUnavailable`, with the command to run.
"""

from __future__ import annotations

import importlib.util

from ..vision.detect import ToolUnavailable


def available() -> bool:
    return importlib.util.find_spec("cairosvg") is not None


def rasterize(svg: str, size: int, *, width: float = 0.0, height: float = 0.0) -> bytes:
    """Render an SVG to an RGBA PNG whose longest side is `size` pixels.

    The icon's proportions are kept: a wide icon gives a wide PNG, never a
    distorted square.
    """
    try:
        import cairosvg
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ToolUnavailable(
            "cairosvg missing: pip install -e .[vector] (rasterizes SVG icons to PNG)"
        ) from exc

    if width <= 0 or height <= 0:
        width, height = svg_size(svg)
    if width > 0 and height > 0:
        ratio = size / max(width, height)
        target = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    else:
        target = (size, size)
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                            output_width=target[0], output_height=target[1])


def svg_size(svg: str) -> tuple[float, float]:
    """Declared dimensions of an SVG: viewBox, otherwise width/height."""
    import xml.etree.ElementTree as ET

    from .geometry import parse_length, parse_viewbox

    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return (0.0, 0.0)
    box = parse_viewbox(root.get("viewBox"))
    if box is not None and box[2] > 0 and box[3] > 0:
        return (box[2], box[3])
    return (parse_length(root.get("width")), parse_length(root.get("height")))
