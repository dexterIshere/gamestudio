"""Minimal SVG geometry: matrices, lengths, bounding boxes.

Enough to place each shape of an icon sheet in the root's user space, which is
all it takes to decide *where* to cut. Nothing is rasterized and no library is
needed: reading an SVG is XML parsing, and placing a shape comes down to a 2x3
matrix -- deterministic code, in the same vein as the 2D split.

Two deliberate approximations, both *upper bounds* (a box can never be too
small, only too large):

- Bezier curves are bounded by their control polygon;
- an elliptical arc is bounded by the (rx, ry) disk around its midpoint.

A box that is too large loses no pixel in the split: it only brings two
neighboring icons closer, which the grouping threshold absorbs.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from xml.etree.ElementTree import Element

from ..sheet.layout import Box, bbox_of, union_all

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# SVG affine matrix: (a, b, c, d, e, f) -> x' = a x + c y + e, y' = b x + d y + f.
Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# Elements that paint something, or contain something that does.
SHAPE_TAGS = frozenset({"path", "rect", "circle", "ellipse", "line", "polyline",
                        "polygon", "text", "image", "use", "foreignObject"})
CONTAINER_TAGS = frozenset({"g", "svg", "symbol", "a", "switch", "marker"})
DRAWABLE_TAGS = SHAPE_TAGS | CONTAINER_TAGS
# Elements that never paint directly: they define, they describe.
NON_DRAWABLE_TAGS = frozenset({"defs", "style", "title", "desc", "metadata",
                               "linearGradient", "radialGradient", "pattern",
                               "clipPath", "mask", "filter", "script"})

# Inherited stroke state: (paint, width). Without paint, nothing is stroked.
StrokeState = tuple[str, float]
DEFAULT_STROKE: StrokeState = ("", 1.0)

_NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
_NUMBERS_RE = re.compile(_NUMBER)
_TRANSFORM_RE = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")


def local_name(element: Element) -> str:
    """A tag's name without its namespace (`{...}path` -> `path`)."""
    tag = element.tag
    if not isinstance(tag, str):  # comments, processing instructions
        return ""
    return tag.rsplit("}", 1)[-1]


def is_drawable(element: Element) -> bool:
    name = local_name(element)
    return bool(name) and name not in NON_DRAWABLE_TAGS


# --------------------------------------------------------------------- lengths


def parse_length(value: str | None, default: float = 0.0) -> float:
    """SVG length in user units. Percentages are ignored.

    Absolute units (px, pt, mm...) are not converted: in an icon sheet
    everything is in user units, and an approximate conversion would skew the
    box more surely than it would help.
    """
    if value is None:
        return default
    match = _NUMBERS_RE.search(value)
    if match is None or "%" in value:
        return default
    return float(match.group())


def parse_points(value: str | None) -> list[tuple[float, float]]:
    """The `points` attribute of a polyline/polygon."""
    numbers = [float(n) for n in _NUMBERS_RE.findall(value or "")]
    return [(numbers[i], numbers[i + 1]) for i in range(0, len(numbers) - 1, 2)]


# -------------------------------------------------------------------- matrices


def multiply(outer: Matrix, inner: Matrix) -> Matrix:
    """Composition: apply `inner` then `outer` (nested SVG convention)."""
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def apply(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def is_identity(matrix: Matrix, *, tol: float = 1e-9) -> bool:
    return all(abs(m - i) <= tol for m, i in zip(matrix, IDENTITY, strict=True))


def matrix_attribute(matrix: Matrix) -> str:
    """Serialize a matrix for the `transform` attribute."""
    return "matrix(" + ",".join(f"{v:.6g}" for v in matrix) + ")"


def parse_transform(value: str | None) -> Matrix:
    """List of SVG transforms -> a single matrix, applied left to right."""
    if not value:
        return IDENTITY
    result = IDENTITY
    for name, raw in _TRANSFORM_RE.findall(value):
        args = [float(n) for n in _NUMBERS_RE.findall(raw)]
        result = multiply(result, _single_transform(name, args))
    return result


def _single_transform(name: str, args: list[float]) -> Matrix:
    if name == "matrix" and len(args) >= 6:
        return (args[0], args[1], args[2], args[3], args[4], args[5])
    if name == "translate" and args:
        return (1.0, 0.0, 0.0, 1.0, args[0], args[1] if len(args) > 1 else 0.0)
    if name == "scale" and args:
        sx = args[0]
        return (sx, 0.0, 0.0, args[1] if len(args) > 1 else sx, 0.0, 0.0)
    if name == "rotate" and args:
        angle = math.radians(args[0])
        cos, sin = math.cos(angle), math.sin(angle)
        rotation: Matrix = (cos, sin, -sin, cos, 0.0, 0.0)
        if len(args) >= 3:  # rotation around a center
            cx, cy = args[1], args[2]
            return multiply(multiply((1.0, 0.0, 0.0, 1.0, cx, cy), rotation),
                            (1.0, 0.0, 0.0, 1.0, -cx, -cy))
        return rotation
    if name == "skewX" and args:
        return (1.0, 0.0, math.tan(math.radians(args[0])), 1.0, 0.0, 0.0)
    if name == "skewY" and args:
        return (1.0, math.tan(math.radians(args[0])), 0.0, 1.0, 0.0, 0.0)
    return IDENTITY


def element_matrix(element: Element) -> Matrix:
    """An element's own transform, nested `<svg>` included."""
    matrix = parse_transform(element.get("transform"))
    if local_name(element) in ("svg", "symbol"):
        matrix = multiply(matrix, viewport_matrix(element))
    return matrix


def viewport_matrix(element: Element) -> Matrix:
    """viewBox -> viewport mapping of a nested `<svg>`/`<symbol>`.

    Without a viewBox, only the (x, y) offset counts. With one, the default
    uniform scale of `preserveAspectRatio` (meet) is honored.
    """
    x = parse_length(element.get("x"))
    y = parse_length(element.get("y"))
    offset: Matrix = (1.0, 0.0, 0.0, 1.0, x, y)
    box = parse_viewbox(element.get("viewBox"))
    if box is None:
        return offset
    vb_x, vb_y, vb_w, vb_h = box
    width = parse_length(element.get("width"), vb_w)
    height = parse_length(element.get("height"), vb_h)
    if vb_w <= 0 or vb_h <= 0 or width <= 0 or height <= 0:
        return offset
    scale = min(width / vb_w, height / vb_h)
    return multiply(offset, multiply((scale, 0.0, 0.0, scale, 0.0, 0.0),
                                     (1.0, 0.0, 0.0, 1.0, -vb_x, -vb_y)))


def parse_viewbox(value: str | None) -> tuple[float, float, float, float] | None:
    numbers = [float(n) for n in _NUMBERS_RE.findall(value or "")]
    if len(numbers) < 4:
        return None
    return (numbers[0], numbers[1], numbers[2], numbers[3])


# ----------------------------------------------------------------------- boxes

# Bounding boxes and grouping do not depend on the medium: they are shared
# with the bitmap sheet split (see sheet/layout.py).
BBox = Box

# ---------------------------------------------------------------------- shapes


class _PathScanner:
    """A `d` reader aware of the current command.

    A naive tokenizer breaks on arcs compressed by SVGO -- `a5 5 0 0110 0`
    where both flags and the x coordinate are glued together. Flags are thus
    read character by character, as the SVG grammar requires.
    """

    def __init__(self, data: str) -> None:
        self.data = data
        self.pos = 0

    def _skip(self) -> None:
        while self.pos < len(self.data) and self.data[self.pos] in " \t\r\n,":
            self.pos += 1

    def command(self) -> str | None:
        self._skip()
        if self.pos < len(self.data) and self.data[self.pos].isalpha():
            letter = self.data[self.pos]
            self.pos += 1
            return letter
        return None

    def number(self) -> float | None:
        self._skip()
        match = _NUMBERS_RE.match(self.data, self.pos)
        if match is None:
            return None
        self.pos = match.end()
        return float(match.group())

    def flag(self) -> float | None:
        self._skip()
        if self.pos < len(self.data) and self.data[self.pos] in "01":
            value = float(self.data[self.pos])
            self.pos += 1
            return value
        return None

    def at_number(self) -> bool:
        self._skip()
        return _NUMBERS_RE.match(self.data, self.pos) is not None


def path_points(data: str) -> list[tuple[float, float]]:
    """Absolute points of a `d`: vertices *and* control points.

    The control polygon contains the curve: the resulting box is an exact upper
    bound in the Bezier sense. A truncated command stops reading instead of
    inventing coordinates.
    """
    scanner = _PathScanner(data or "")
    points: list[tuple[float, float]] = []
    x = y = start_x = start_y = 0.0
    command = ""

    def numbers(count: int) -> list[float] | None:
        values: list[float] = []
        for _ in range(count):
            value = scanner.number()
            if value is None:
                return None
            values.append(value)
        return values

    while True:
        letter = scanner.command()
        if letter is not None:
            command = letter
        else:
            if not command or not scanner.at_number():
                break
            # Implicit argument repetition; after a moveto, the following pairs
            # are linetos (SVG rule).
            command = {"M": "L", "m": "l"}.get(command, command)

        upper = command.upper()
        relative = command.islower()

        if upper == "Z":
            x, y = start_x, start_y
            points.append((x, y))
            continue
        if upper in ("M", "L", "T"):
            args = numbers(2)
            if args is None:
                break
            x, y = (x + args[0], y + args[1]) if relative else (args[0], args[1])
            points.append((x, y))
            if upper == "M":
                start_x, start_y = x, y
        elif upper in ("H", "V"):
            args = numbers(1)
            if args is None:
                break
            if upper == "H":
                x = x + args[0] if relative else args[0]
            else:
                y = y + args[0] if relative else args[0]
            points.append((x, y))
        elif upper in ("C", "S", "Q"):
            need = 6 if upper == "C" else 4
            args = numbers(need)
            if args is None:
                break
            base_x, base_y = (x, y) if relative else (0.0, 0.0)
            for i in range(0, need, 2):
                points.append((base_x + args[i], base_y + args[i + 1]))
            x, y = base_x + args[need - 2], base_y + args[need - 1]
        elif upper == "A":
            radii = numbers(3)  # rx, ry, rotation
            if radii is None or scanner.flag() is None or scanner.flag() is None:
                break
            end = numbers(2)
            if end is None:
                break
            rx, ry = abs(radii[0]), abs(radii[1])
            end_x, end_y = (x + end[0], y + end[1]) if relative else (end[0], end[1])
            # Upper bound: the arc stays within the (rx, ry) disk centered on the chord.
            mid_x, mid_y = (x + end_x) / 2.0, (y + end_y) / 2.0
            points.extend([(mid_x - rx, mid_y - ry), (mid_x + rx, mid_y + ry),
                           (end_x, end_y)])
            x, y = end_x, end_y
        else:
            break  # unknown command: the rest can no longer be interpreted
    return points


def shape_points(element: Element) -> list[tuple[float, float]]:
    """Characteristic points of a shape, in its own coordinate system."""
    name = local_name(element)
    get = element.get
    if name == "path":
        return path_points(get("d", ""))
    if name in ("rect", "image", "foreignObject"):
        x, y = parse_length(get("x")), parse_length(get("y"))
        w, h = parse_length(get("width")), parse_length(get("height"))
        return [(x, y), (x + w, y + h)]
    if name == "circle":
        cx, cy, r = parse_length(get("cx")), parse_length(get("cy")), parse_length(get("r"))
        return [(cx - r, cy - r), (cx + r, cy + r)]
    if name == "ellipse":
        cx, cy = parse_length(get("cx")), parse_length(get("cy"))
        rx, ry = parse_length(get("rx")), parse_length(get("ry"))
        return [(cx - rx, cy - ry), (cx + rx, cy + ry)]
    if name == "line":
        return [(parse_length(get("x1")), parse_length(get("y1"))),
                (parse_length(get("x2")), parse_length(get("y2")))]
    if name in ("polyline", "polygon"):
        return parse_points(get("points"))
    if name == "text":
        # Without font metrics, only the anchor point is certain. A label under
        # an icon thus joins it by proximity, which is the intended behavior;
        # it never inflates the box by an invented width.
        return [(parse_length(get("x")), parse_length(get("y")))]
    return []


def element_bbox(
    element: Element,
    matrix: Matrix = IDENTITY,
    resolve: Callable[[str], Element | None] | None = None,
    stroke: StrokeState = DEFAULT_STROKE,
    _seen: frozenset[int] = frozenset(),
) -> BBox | None:
    """Box of an element and its descendants, expressed after `matrix`.

    `stroke` is the stroke state inherited from the ancestors: a
    `<g stroke="#fff">` thickens every shape it contains, and that half width
    is part of the icon -- forgetting it would crop the icon on extraction.

    `resolve` gives the target element of a `<use href="#id">`; without it,
    `use` elements are ignored. Recursion is protected against circular
    references.
    """
    name = local_name(element)
    if not name or name in NON_DRAWABLE_TAGS or element.get("display") == "none":
        return None
    if id(element) in _seen:
        return None
    seen = _seen | {id(element)}

    local = multiply(matrix, element_matrix(element))
    state = stroke_state(element, stroke)

    if name == "use":
        if resolve is None:
            return None
        target = resolve(_href(element) or "")
        if target is None:
            return None
        offset: Matrix = (1.0, 0.0, 0.0, 1.0,
                          parse_length(element.get("x")), parse_length(element.get("y")))
        used = multiply(local, offset)
        if local_name(target) in ("svg", "symbol"):
            # A referenced symbol is not drawn where it is defined: its content
            # is, in the `use` element's frame.
            return union_all(element_bbox(child, multiply(used, element_matrix(target)),
                                          resolve, state, seen)
                             for child in target)
        return element_bbox(target, used, resolve, state, seen)

    boxes: list[BBox | None] = []
    own = bbox_of(apply(local, x, y) for x, y in shape_points(element))
    if own is not None:
        margin = stroke_margin(state) * _scale_factor(local)
        boxes.append(own.expand(margin) if margin else own)
    if name in CONTAINER_TAGS:
        boxes.extend(element_bbox(child, local, resolve, state, seen) for child in element)
    return union_all(boxes)


def _href(element: Element) -> str | None:
    return element.get("href") or element.get(f"{{{XLINK_NS}}}href")


def style_value(element: Element, name: str) -> str | None:
    """A property's value: the inline `style` wins over the attribute."""
    style = element.get("style")
    if style and name in style:
        match = re.search(rf"(?:^|;)\s*{re.escape(name)}\s*:\s*([^;]+)", style)
        if match:
            return match.group(1).strip()
    return element.get(name)


def stroke_state(element: Element, inherited: StrokeState = DEFAULT_STROKE) -> StrokeState:
    """An element's effective stroke state, inherited then overridden."""
    paint = style_value(element, "stroke")
    width = style_value(element, "stroke-width")
    return (inherited[0] if paint is None else paint,
            inherited[1] if width is None else parse_length(width, inherited[1]))


def stroke_margin(state: StrokeState) -> float:
    """How far the stroke extends on each side of the geometry."""
    paint, width = state
    if not paint or paint in ("none", "transparent"):
        return 0.0
    return max(0.0, width) / 2.0


def _scale_factor(matrix: Matrix) -> float:
    """Average scale of a matrix, to convert a stroke width."""
    a, b, c, d, _, _ = matrix
    return math.sqrt(abs(a * d - b * c)) or 1.0
