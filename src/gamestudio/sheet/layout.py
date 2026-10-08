"""What does not depend on the medium: boxes, grouping, order, names.

A sheet is a file that holds several elements. Finding *where* to cut depends
on the medium -- XML structure for an SVG, pixels for a PNG -- but everything
after that is identical: two pieces that touch belong to the same drawing,
pieces read in rows from left to right, and each comes out in a file with a
unique name.

This module holds that shared part, and the result type both splits produce.
"""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SheetError(ValueError):
    """The file is not a usable sheet."""


# ----------------------------------------------------------------------- boxes


@dataclass(frozen=True)
class Box:
    """Axis-aligned bounding box.

    In user units for an SVG, in pixels for an image: the grouping geometry is
    the same in both cases.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def union(self, other: Box) -> Box:
        return Box(min(self.x0, other.x0), min(self.y0, other.y0),
                   max(self.x1, other.x1), max(self.y1, other.y1))

    def expand(self, margin: float) -> Box:
        return Box(self.x0 - margin, self.y0 - margin,
                   self.x1 + margin, self.y1 + margin)

    def clamp(self, width: float, height: float) -> Box:
        """Bring the box back inside a frame (useful after a margin)."""
        return Box(max(0.0, self.x0), max(0.0, self.y0),
                   min(width, self.x1), min(height, self.y1))

    def gap_to(self, other: Box) -> float:
        """Distance between two boxes: 0 if they touch or overlap."""
        dx = max(0.0, max(self.x0, other.x0) - min(self.x1, other.x1))
        dy = max(0.0, max(self.y0, other.y0) - min(self.y1, other.y1))
        return max(dx, dy)

    def rounded(self, digits: int = 3) -> Box:
        return Box(round(self.x0, digits), round(self.y0, digits),
                   round(self.x1, digits), round(self.y1, digits))


def bbox_of(points: Iterable[tuple[float, float]]) -> Box | None:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in points:
        if math.isfinite(x) and math.isfinite(y):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return Box(min(xs), min(ys), max(xs), max(ys))


def union_all(boxes: Iterable[Box | None]) -> Box | None:
    result: Box | None = None
    for box in boxes:
        if box is None:
            continue
        result = box if result is None else result.union(box)
    return result


# -------------------------------------------------------------------- grouping


def scale_of(boxes: Sequence[Box]) -> float:
    """Characteristic size of the drawings: median weighted by area.

    A raw median is ruled by numbers, and a bitmap sheet produces hundreds of
    specks -- antialiasing chips, halos, shadows -- for a few dozen drawings.
    They are countless and weigh nothing: weighting by area ignores them.
    """
    entries = sorted((min(box.width, box.height), box.area) for box in boxes
                     if min(box.width, box.height) > 0 and box.area > 0)
    if not entries:
        return 0.0
    half = sum(area for _, area in entries) / 2.0
    accumulated = 0.0
    for size, area in entries:
        accumulated += area
        if accumulated >= half:
            return size
    return entries[-1][0]


def mst_edges(boxes: Sequence[Box]) -> list[tuple[float, int, int]]:
    """Minimum spanning tree of the boxes, by distance between them.

    This is the exact structure of grouping by proximity: cutting every edge
    beyond a threshold gives the same groups as comparing every pair, and
    vectorized Prim's algorithm does it in O(n^2) numpy work rather than O(n^2)
    Python loops -- which matters when a bitmap sheet arrives with thousands of
    chips.
    """
    import numpy as np

    count = len(boxes)
    if count < 2:
        return []
    x0 = np.array([box.x0 for box in boxes], dtype=np.float64)
    y0 = np.array([box.y0 for box in boxes], dtype=np.float64)
    x1 = np.array([box.x1 for box in boxes], dtype=np.float64)
    y1 = np.array([box.y1 for box in boxes], dtype=np.float64)

    def distances(index: int) -> np.ndarray:
        dx = np.maximum(0.0, np.maximum(x0[index], x0) - np.minimum(x1[index], x1))
        dy = np.maximum(0.0, np.maximum(y0[index], y0) - np.minimum(y1[index], y1))
        return np.maximum(dx, dy)

    visited = np.zeros(count, dtype=bool)
    visited[0] = True
    source = np.zeros(count, dtype=np.int64)
    best = distances(0)
    best[0] = np.inf

    edges: list[tuple[float, int, int]] = []
    for _ in range(count - 1):
        target = int(np.argmin(best))
        weight = float(best[target])
        if not np.isfinite(weight):
            break
        edges.append((weight, int(source[target]), target))
        visited[target] = True
        candidate = distances(target)
        # A node already linked must never become a candidate again, or the tree
        # loops on itself instead of covering the sheet.
        candidate[visited] = np.inf
        closer = candidate < best
        best = np.where(closer, candidate, best)
        source = np.where(closer, target, source)
        best[visited] = np.inf
    edges.sort()
    return edges


# Mean ratio from which two clusters of distances are really distinct: the
# gaps inside a drawing on one side, the separations on the other.
JUMP_RATIO = 3.0

# Below this fraction of the drawings' size, even the largest distance is still
# an inner gap: the sheet holds a single drawing.
LONE_DRAWING_RATIO = 0.25


def auto_gap(boxes: Sequence[Box], edges: Sequence[tuple[float, int, int]] | None = None
             ) -> float:
    """Threshold read from the distribution of distances, between their two clusters.

    Distances inside a drawing (between an icon's body and its highlight) and
    distances between two drawings do not mix: sorted, they form two clusters.
    The separation that best tells them apart is wanted -- Otsu's threshold,
    computed on the distances on a log scale.

    Just taking the largest jump would be simpler, and wrong: an isolated speck
    in a corner creates a huge edge that would grab the cut on its own. Otsu
    looks at populations, not extremes.

    Without a clear separation, two readings are possible, settled by scale:
    distances all tiny compared to the drawings' size mean a single drawing in
    pieces; distances all large mean drawings already apart.
    """
    weights = [weight for weight, _, _ in (mst_edges(boxes) if edges is None else edges)]
    if not weights:
        return 0.0

    threshold, separation = _otsu_threshold(weights)
    if separation >= math.log(JUMP_RATIO):
        return threshold

    scale = scale_of(boxes)
    if scale > 0 and weights[-1] < LONE_DRAWING_RATIO * scale:
        return weights[-1]  # everything belongs to the same drawing
    return 0.0              # the drawings touch, or nothing brings them closer


def _otsu_threshold(weights: Sequence[float]) -> tuple[float, float]:
    """Otsu cut on the distances, and the mean distance between both clusters.

    Working on `log(1 + distance)` puts a 2 px gap in a 64 px icon and a 30 px
    gap in a 1000 px icon on the same scale: a ratio separates two drawings,
    not an absolute distance.
    """
    import numpy as np

    values = np.log1p(np.asarray(weights, dtype=np.float64))
    count = values.size
    if count < 2:
        return float(weights[-1]), 0.0

    cumulative = np.cumsum(values)
    total = cumulative[-1]
    index = np.arange(1, count)          # size of the low cluster
    low_mean = cumulative[:-1] / index
    high_mean = (total - cumulative[:-1]) / (count - index)
    # Otsu's between-class variance: highest where the separation is sharpest.
    between = index * (count - index) * (high_mean - low_mean) ** 2
    best = int(np.argmax(between))
    return float(weights[best]), float(high_mean[best] - low_mean[best])


def group_by_proximity(boxes: Sequence[Box], gap: float,
                       edges: Sequence[tuple[float, int, int]] | None = None
                       ) -> list[list[int]]:
    """Grouped indices: two boxes less than `gap` apart join each other."""
    parent = list(range(len(boxes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for weight, left, right in (mst_edges(boxes) if edges is None else edges):
        if weight > gap:
            break  # edges are sorted: beyond this, nothing groups any more
        a, b = find(left), find(right)
        if a != b:
            parent[a] = b

    groups: dict[int, list[int]] = {}
    for index in range(len(boxes)):
        groups.setdefault(find(index), []).append(index)
    return list(groups.values())


def group_with_auto_gap(boxes: Sequence[Box],
                        gap: float | None = None) -> tuple[list[list[int]], float]:
    """Group, deriving the threshold from the sheet unless it is given."""
    if not boxes:
        return [], 0.0
    edges = mst_edges(boxes)
    threshold = float(gap) if gap is not None else auto_gap(boxes, edges)
    return group_by_proximity(boxes, threshold, edges), threshold


def reading_order(boxes: Sequence[Box]) -> list[int]:
    """Indices in reading order: by rows, then left to right."""
    if not boxes:
        return []
    heights = [box.height for box in boxes if box.height > 0]
    tolerance = (statistics.median(heights) / 2.0) if heights else 0.0
    rows: list[list[int]] = []
    for index in sorted(range(len(boxes)), key=lambda i: (boxes[i].y0, boxes[i].x0)):
        if rows and abs(boxes[index].y0 - boxes[rows[-1][0]].y0) <= tolerance:
            rows[-1].append(index)
        else:
            rows.append([index])
    return [index for row in rows for index in sorted(row, key=lambda i: boxes[i].x0)]


# ----------------------------------------------------------------------- names


def slug(value: str, *, max_length: int = 48) -> str:
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:max_length].strip("-")


def is_generic_name(name: str) -> bool:
    """`path12`, `g4`, `layer-1`: a tool's identifier names nothing."""
    return bool(re.fullmatch(r"(path|g|layer|group|rect|shape|svg|vector|frame|image)"
                             r"[-_]?\d*", name))


def unique_names(names: Sequence[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        if name in seen:
            seen[name] += 1
            result.append(f"{name}-{seen[name]}")
        else:
            seen[name] = 1
            result.append(name)
    return result


# ---------------------------------------------------------------------- result


@dataclass(frozen=True)
class Piece:
    """A split piece: its frame in the sheet, and its standalone file."""

    name: str
    index: int
    x: float
    y: float
    width: float
    height: float
    data: bytes
    suffix: str = ".png"
    elements: int = 1
    source_id: str = ""

    @property
    def box(self) -> Box:
        return Box(self.x, self.y, self.x + self.width, self.y + self.height)

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "index": self.index,
                "x": round(self.x, 2), "y": round(self.y, 2),
                "width": round(self.width, 2), "height": round(self.height, 2),
                "elements": self.elements, "source_id": self.source_id or None}

    def write(self, directory: Path) -> Path:
        target = Path(directory) / f"{self.name}{self.suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.data)
        return target


@dataclass
class SheetSplit:
    """The result of a split: what was found, and how."""

    source: str
    strategy: str
    width: float
    height: float
    pieces: list[Piece] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    gap: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {"source": self.source, "strategy": self.strategy,
                "width": round(self.width, 2), "height": round(self.height, 2),
                "gap": round(self.gap, 3), "count": len(self.pieces),
                "pieces": [piece.summary() for piece in self.pieces],
                "warnings": self.warnings}

    def write(self, directory: Path) -> list[Path]:
        return [piece.write(directory) for piece in self.pieces]


def name_pieces(names: Sequence[str], *, prefix: str = "icon") -> list[str]:
    """Fill missing names with their position, then make them unique."""
    filled = [name or f"{prefix}-{index + 1:02d}" for index, name in enumerate(names)]
    return unique_names(filled)
