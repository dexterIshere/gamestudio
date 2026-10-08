"""Splitting a PNG sheet into one file per element.

An SVG carries its structure; a PNG only has pixels. The separation is thus
read from the image: the background is isolated, what remains is labeled into
connected components, and the vector split's rule applies exactly -- two
pieces that touch belong to the same drawing.

Two kinds of sheets, and two resulting regimes:

- **grid** (animation frames): all cells have the same size, and that size
  must be kept. Cropping each frame to its content would make the character
  jitter, its pivot changing from one frame to the next -- the same trap
  `sprites.py` avoids when assembling.
- **free pack** (icons): each element is cropped to its own content, since it
  will be used alone.

The background is guessed: an alpha channel when there is one, otherwise the
dominant color of the borders, removed with a tolerance -- the element then
comes out cut out, ready to be placed on any backdrop.

Removal by color is enough on a clean flat fill, but a textured background
leaves grain everywhere and a halo around each drawing. `matting=True` then
hands the cut-out to the studio's local model (rembg/BiRefNet, free, cached
after the first download): a single inference on the whole sheet, the color
only serving to spot the elements the model would have erased -- each is then
cut out again on its own, from largest to smallest, and what the model refuses
even when cropped is taken as background. Edges are finally decontaminated:
the known background color is removed from semi-transparent pixels, otherwise
each element would keep a thin film of background (`_defringe`). Without the
option (this function's default), the split stays purely geometric: no model,
no network.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from .layout import (
    Box,
    Piece,
    SheetError,
    SheetSplit,
    group_with_auto_gap,
    name_pieces,
    reading_order,
    union_all,
)

STRATEGIES = ("auto", "grid", "blobs", "single")

# Beyond this, the sheet is no longer a sheet: stop rather than produce
# thousands of one-pixel files.
MAX_PIECES = 2000

# Default tolerance of a plain background, as a Chebyshev distance on 0-255.
DEFAULT_TOLERANCE = 16

# Share of the border the dominant color must cover to be a background. A fully
# filled sheet -- adjoining cells, for example -- has no background, and its
# most frequent color must certainly not be removed.
BACKGROUND_SHARE = 0.6

# Regularity required of the bands to recognize a grid (12% of the pitch).
GRID_TOLERANCE = 0.12

# Share of the sheet a single piece should not cover: beyond it, a halo links
# several drawings together, and the background was poorly removed.
BRIDGE_SHARE = 0.25

# Tolerances tried in turn when the background bridges, as multiples of the
# requested one. A halo is closer to the background than a drawing is.
BRIDGE_STEPS = (2, 3, 4, 6)

# Recovery pass of model matting: a subtle element (a dark nebula on a dark
# background) may be taken for background on the whole sheet, while cropped
# alone it stands out again. The margin leaves it some context, and the cap
# bounds the number of inferences.
MATTING_MARGIN = 24
MATTING_RETRIES = 8

# Share of an element's pixels (as seen by color) the model must keep for the
# element to count as kept; below it, the element is recovered.
MATTING_KEPT_SHARE = 0.05

# An element smaller than this is not offered to recovery: cropped, its window
# is almost only background, and the model -- bound to find something there --
# would keep any clump of texture.
MATTING_FLOOR = 24

# Below this alpha, a pixel in the background's colors is a veil carrying no
# information: it goes away. Above it, it may be a shadow or glow the drawing
# intends.
VEIL_ALPHA = 32


def split_bitmap(
    source: str | Path | Image.Image,
    *,
    strategy: str = "auto",
    gap: float | None = None,
    min_size: float = 4.0,
    padding: float = 0.0,
    background: str | tuple[int, int, int] | None = None,
    alpha_threshold: int = 8,
    tolerance: int = DEFAULT_TOLERANCE,
    rows: int = 0,
    columns: int = 0,
    trim: bool | None = None,
    matting: bool = False,
) -> SheetSplit:
    """Split a bitmap sheet into standalone elements.

    - `strategy`: `auto` (grid if one is recognized, otherwise proximity),
      `grid`, `blobs`, `single` (the whole sheet, cropped).
    - `gap`: maximum distance, in pixels, between two pieces of the same
      element. `None` derives it from the pieces' median size.
    - `min_size`: drop the specks none of whose sides reaches this size.
    - `background`: imposed background color (`"#ffffff"` or a triplet) when
      the image has no transparency; otherwise it is derived from the borders.
    - `rows`/`columns`: impose the grid, when cells touch and no gutter allows
      guessing it.
    - `trim`: crop each element to its content. By default, yes for an icon
      pack, no for a frame grid.
    - `matting`: when the background must be removed (opaque sheet), hand it
      to the local matting model rather than to color (see the header).
    """
    if strategy not in STRATEGIES:
        raise SheetError(f"unknown strategy: {strategy} (among {', '.join(STRATEGIES)})")

    image, name = _load(source)
    width, height = image.size
    warnings: list[str] = []

    mask, keyed, notes = _resolve_background(
        image, alpha_threshold=alpha_threshold, background=background,
        tolerance=tolerance)
    warnings.extend(notes)
    if keyed:
        refined = _matting_mask(image, mask, alpha_threshold=alpha_threshold,
                                background=background,
                                tolerance=tolerance) if matting else None
        if refined is not None:
            image, mask, matting_notes = refined
            warnings.extend(matting_notes)
        else:
            if matting:
                warnings.append("color matting only: local model unavailable "
                                "or not responding")
            image = _apply_mask(image, mask)
    if not mask.any():
        return SheetSplit(source=name, strategy=strategy, width=width, height=height,
                          warnings=["the image is empty: no foreground pixel"])

    used = strategy
    grid: tuple[int, int] | None = None
    if strategy in ("auto", "grid"):
        grid = (int(columns), int(rows)) if columns > 0 and rows > 0 else detect_grid(mask)
        if grid is None:
            if strategy == "grid":
                raise SheetError("no recognizable grid: specify rows and columns, or use "
                                 "strategy='blobs'")
            used = "blobs"
        else:
            used = "grid"

    effective_gap = 0.0
    if used == "single":
        content = _mask_box(mask)
        boxes = [content] if content is not None else []
        prefix = "sheet"
    elif used == "grid":
        assert grid is not None
        boxes, empty = _grid_boxes(mask, width, height, grid)
        if empty:
            warnings.append(f"{empty} empty cell(s) ignored")
        warnings.append(f"{grid[0]} x {grid[1]} grid recognized")
        prefix = "frame"
    else:
        boxes = _components(mask)
        kept = [box for box in boxes
                if max(box.width, box.height) >= min_size]
        if len(kept) < len(boxes):
            warnings.append(f"{len(boxes) - len(kept)} speck(s) under {min_size:g} px "
                            "dropped")
        if len(kept) > MAX_PIECES:
            warnings.append(f"{len(kept)} shapes: only the first {MAX_PIECES}")
            kept = kept[:MAX_PIECES]
        groups, effective_gap = group_with_auto_gap(kept, gap)
        boxes = [union_all(kept[i] for i in group) or kept[group[0]]
                 for group in groups]
        prefix = "icon"

    if trim is None:
        trim = used != "grid"

    order = reading_order(boxes)
    boxes = [boxes[index] for index in order]
    if trim:
        boxes = [_trim(mask, box) or box for box in boxes]
    elif used == "grid":
        boxes = _shared_frame(mask, boxes, width, height)
    if padding:
        boxes = [box.expand(padding).clamp(width, height) for box in boxes]

    names = name_pieces([""] * len(boxes), prefix=prefix)
    pieces = [
        Piece(name=name, index=index,
              x=box.x0, y=box.y0, width=box.width, height=box.height,
              data=_crop(image, box), suffix=".png")
        for index, (box, name) in enumerate(zip(boxes, names, strict=True))
    ]
    if not pieces:
        warnings.append("no element found in the sheet")
    elif len(pieces) == 1 and used != "single":
        warnings.append("a single element detected: the sheet may hold only one, "
                        "or lowering `gap` would split it")

    return SheetSplit(source=name, strategy=used, width=width, height=height,
                      pieces=pieces, warnings=warnings, gap=effective_gap)


# ---------------------------------------------------------------------- loading


def _load(source: str | Path | Image.Image) -> tuple[Image.Image, str]:
    if isinstance(source, Image.Image):
        return source.convert("RGBA"), "<memory>"
    path = Path(source)
    try:
        with Image.open(path) as opened:
            return opened.convert("RGBA"), path.name
    except OSError as exc:
        raise SheetError(f"unreadable image: {exc}") from exc


# ------------------------------------------------------------------- background


def _resolve_background(
    image: Image.Image, *, alpha_threshold: int,
    background: str | tuple[int, int, int] | None, tolerance: int,
) -> tuple[np.ndarray, bool, list[str]]:
    """Foreground mask, widening the tolerance if the background bridges.

    A dark flat fill often carries a halo around each drawing. Too close to
    the background to be a drawing, too far to be removed, it links neighbors
    together: the whole sheet becomes a single piece. The symptom is clear -- a
    piece covering a large share of the sheet -- and so is the fix: widen the
    tolerance until the drawings come apart. If none separates them, the
    requested value is kept rather than eating into a drawing that really does
    fill the whole sheet.
    """
    mask, keyed = foreground_mask(image, alpha_threshold=alpha_threshold,
                                  background=background, tolerance=tolerance)
    if not keyed:
        return mask, keyed, []
    notes = ["plain background removed: the elements come out cut out"]
    if background is not None or not _bridged(mask):
        return mask, keyed, notes

    for step in BRIDGE_STEPS:
        widened = min(96, tolerance * step)
        candidate, _ = foreground_mask(image, alpha_threshold=alpha_threshold,
                                       background=None, tolerance=widened)
        if not _bridged(candidate):
            notes.append(f"background halo: tolerance widened to {widened} to "
                         "separate the drawings")
            return candidate, True, notes
        if widened >= 96:
            break
    notes.append("the background links the drawings together: raise `tolerance`, "
                 "or set the background color")
    return mask, keyed, notes


def _bridged(mask: np.ndarray) -> bool:
    """Does a single piece cover an abnormal share of the sheet?"""
    height, width = mask.shape
    surface = float(height * width)
    if surface <= 0:
        return False
    return any(box.area >= BRIDGE_SHARE * surface for box in _components(mask))


def foreground_mask(
    image: Image.Image,
    *,
    alpha_threshold: int = 8,
    background: str | tuple[int, int, int] | None = None,
    tolerance: int = DEFAULT_TOLERANCE,
) -> tuple[np.ndarray, bool]:
    """Foreground mask, and whether the background had to be guessed by color.

    Alpha wins: when it exists, it *is* the answer. Otherwise the dominant
    color of the borders is removed -- that is how sheets exported as JPEG or
    flattened on white are made.
    """
    array = np.array(image.convert("RGBA"), dtype=np.uint8)
    alpha = array[..., 3]
    if background is None and (alpha < 255).any():
        return alpha > alpha_threshold, False

    if background is None:
        color, share = _background_color(array, tolerance)
        if share < BACKGROUND_SHARE:
            # The border is motley: there is no background to remove.
            return np.ones(alpha.shape, dtype=bool), False
    else:
        color = _parse_color(background)
    distance = np.abs(array[..., :3].astype(np.int16) - np.array(color, dtype=np.int16))
    mask = distance.max(axis=2) > tolerance
    if (alpha < 255).any():  # partial transparency stays decisive
        mask &= alpha > alpha_threshold
    return mask, True


def _background_color(array: np.ndarray,
                      tolerance: int) -> tuple[tuple[int, int, int], float]:
    """Color of the border, and the share of the border that resembles it.

    The background necessarily touches the edges: that is where it reads. The
    median finds it even if a few icons bite into the edge, and the share is
    counted *within the tolerance* -- a flat fill exported as PNG is never
    perfectly uniform, and counting exact colors would wrongly conclude that
    there is no background at all.
    """
    border = np.concatenate([
        array[0, :, :3], array[-1, :, :3], array[:, 0, :3], array[:, -1, :3],
    ]).astype(np.int16)
    color = np.median(border, axis=0)
    share = float((np.abs(border - color).max(axis=1) <= tolerance).mean())
    return (int(color[0]), int(color[1]), int(color[2])), share


def _parse_color(value: str | tuple[int, int, int]) -> tuple[int, int, int]:
    if isinstance(value, str):
        text = value.strip().lstrip("#")
        if len(text) == 3:
            text = "".join(c * 2 for c in text)
        if len(text) != 6:
            raise SheetError(f"unreadable background color: {value}")
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    return (int(value[0]), int(value[1]), int(value[2]))


def _apply_mask(image: Image.Image, mask: np.ndarray) -> Image.Image:
    """Make the background transparent, once it has been identified."""
    array = np.array(image, dtype=np.uint8)
    array[..., 3] = np.where(mask, array[..., 3], 0)
    return Image.fromarray(array, "RGBA")


def _matting_mask(
    image: Image.Image, keyed: np.ndarray, *, alpha_threshold: int,
    background: str | tuple[int, int, int] | None, tolerance: int,
) -> tuple[Image.Image, np.ndarray, list[str]] | None:
    """Cut out the sheet with the local model, recovering what it erases.

    A single inference on the whole sheet gives the split's alpha -- soft on
    the edges, insensitive to the grain of a textured background. The color
    mask only serves to spot the elements the model erased: each is cut out
    again cropped alone (isolated, it stands out again), from largest to
    smallest -- real drawings come before the clumps of texture that color
    also takes for elements. The cropped model is the arbiter: what it still
    refuses is taken as background, and anything smaller than `MATTING_FLOOR`
    is not offered at all.

    Returns None when the model is missing or cut out nothing at all: color
    matting then takes over.
    """
    from ..vision.detect import ToolUnavailable, remove_background

    try:
        cut = remove_background(image)
    except ToolUnavailable:
        return None
    width, height = image.size
    alpha = np.array(cut.resize(image.size) if cut.size != image.size else cut,
                     dtype=np.uint8)[..., 3]
    notes = ["background removed by the local cut-out model"]

    candidates = []
    for box in _components(keyed):
        if max(box.width, box.height) < MATTING_FLOOR:
            continue
        x0, y0, x1, y1 = int(box.x0), int(box.y0), int(box.x1), int(box.y1)
        piece_keyed = keyed[y0:y1, x0:x1]
        kept = (alpha[y0:y1, x0:x1] > alpha_threshold)[piece_keyed]
        if kept.size and float(kept.mean()) < MATTING_KEPT_SHARE:
            candidates.append(box)
    candidates.sort(key=lambda box: box.area, reverse=True)

    recovered = dropped = 0
    for index, box in enumerate(candidates):
        if index >= MATTING_RETRIES:
            dropped += len(candidates) - index
            break
        x0, y0, x1, y1 = int(box.x0), int(box.y0), int(box.x1), int(box.y1)
        cx0, cy0 = max(0, x0 - MATTING_MARGIN), max(0, y0 - MATTING_MARGIN)
        cx1 = min(width, x1 + MATTING_MARGIN)
        cy1 = min(height, y1 + MATTING_MARGIN)
        crop = remove_background(image.crop((cx0, cy0, cx1, cy1)))
        # The margin may bite into a neighbor: only the inside of the original
        # box is carried over into the sheet's alpha.
        local = np.array(crop, dtype=np.uint8)[y0 - cy0:y1 - cy0,
                                               x0 - cx0:x1 - cx0, 3]
        if (local > alpha_threshold).any():
            alpha[y0:y1, x0:x1] = np.maximum(alpha[y0:y1, x0:x1], local)
            recovered += 1
        else:
            dropped += 1
    if recovered:
        notes.append(f"{recovered} element(s) cut out again on their own")
    if dropped:
        notes.append(f"{dropped} speck(s) the model takes for background")

    out = np.array(image, dtype=np.uint8)
    out[..., 3] = alpha
    if background is not None:
        color = _parse_color(background)
    else:
        color, share = _background_color(out, tolerance)
        if share < BACKGROUND_SHARE:
            color = None
    if color is not None:
        out = _defringe(out, color, alpha_threshold=alpha_threshold,
                        tolerance=tolerance)
    mask = out[..., 3] > alpha_threshold
    if not mask.any():
        return None
    return Image.fromarray(out, "RGBA"), mask, notes


def _defringe(array: np.ndarray, background: tuple[int, int, int], *,
              alpha_threshold: int, tolerance: int) -> np.ndarray:
    """Remove the background color that seeped into semi-transparent pixels.

    An edge pixel is a blend `C = a*F + (1-a)*B`: the background B being known,
    the drawing's true color is recovered by `F = (C - (1-a)B) / a`. Without
    this, each edge keeps a thin film in the background's colors, visible as
    soon as the element is placed on a lighter backdrop. The nearly invisible
    veil (`VEIL_ALPHA`) in the background's colors is removed: whatever its
    transparency, it only shows background.
    """
    out = array.astype(np.float32)
    bg = np.array(background, dtype=np.float32)
    distance = np.abs(out[..., :3] - bg).max(axis=2)
    veil = (out[..., 3] < VEIL_ALPHA) & (distance <= tolerance)
    out[..., 3] = np.where(veil, 0.0, out[..., 3])
    opacity = out[..., 3:4] / 255.0
    visible = out[..., 3] >= alpha_threshold
    truth = np.clip((out[..., :3] - (1.0 - opacity) * bg)
                    / np.maximum(opacity, 1e-3), 0.0, 255.0)
    out[..., :3] = np.where(visible[..., None], truth, out[..., :3])
    return out.astype(np.uint8)


# -------------------------------------------------------- connected components


def _components(mask: np.ndarray) -> list[Box]:
    """Boxes of the connected components (8-connectivity), by row segments.

    Labeling pixel by pixel in Python is out of the question; the horizontal
    *segments* are labeled instead, which numpy extracts from a row in one
    operation, and merged between neighboring rows. An icon sheet has a few
    thousand of them, not a few million.
    """
    height = mask.shape[0]
    parent: list[int] = []
    bounds: list[list[int]] = []  # per label: x0, y0, x1, y1

    def find(label: int) -> int:
        while parent[label] != label:
            parent[label] = parent[parent[label]]
            label = parent[label]
        return label

    def union(a: int, b: int) -> int:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
        return ra

    previous: list[tuple[int, int, int]] = []
    for y in range(height):
        row = mask[y]
        if not row.any():
            previous = []
            continue
        edges = np.diff(row.astype(np.int8), prepend=np.int8(0), append=np.int8(0))
        starts = np.flatnonzero(edges == 1)
        ends = np.flatnonzero(edges == -1)  # exclusive bound

        current: list[tuple[int, int, int]] = []
        pointer = 0
        for x0, x1 in zip(starts.tolist(), ends.tolist(), strict=True):
            label = -1
            # Segments are sorted: the cursor moves forward and never back.
            while pointer < len(previous) and previous[pointer][1] < x0:
                pointer += 1
            probe = pointer
            while probe < len(previous) and previous[probe][0] <= x1:
                other = previous[probe][2]
                label = other if label < 0 else union(label, other)
                probe += 1
            if label < 0:
                label = len(parent)
                parent.append(label)
                bounds.append([x0, y, x1, y + 1])
            else:
                label = find(label)
                box = bounds[label]
                box[0] = min(box[0], x0)
                box[1] = min(box[1], y)
                box[2] = max(box[2], x1)
                box[3] = max(box[3], y + 1)
            current.append((x0, x1, label))
        previous = current

    merged: dict[int, list[int]] = {}
    for label in range(len(parent)):
        root = find(label)
        box = bounds[label]
        if root in merged:
            target = merged[root]
            target[0] = min(target[0], box[0])
            target[1] = min(target[1], box[1])
            target[2] = max(target[2], box[2])
            target[3] = max(target[3], box[3])
        else:
            merged[root] = list(box)
    return [Box(float(b[0]), float(b[1]), float(b[2]), float(b[3]))
            for b in merged.values()]


# ------------------------------------------------------------------------ grid


def detect_grid(mask: np.ndarray) -> tuple[int, int] | None:
    """Number of columns and rows, if the gutters draw a grid.

    A frame sheet leaves regular empty bands between its cells: their
    regularity is the signature of a grid. Adjoining cells leave none -- then
    `rows` and `columns` must be given.
    """
    columns = _regular_bands(mask.any(axis=0))
    rows = _regular_bands(mask.any(axis=1))
    if columns is None or rows is None or columns * rows < 2:
        return None
    return columns, rows


def _regular_bands(occupancy: np.ndarray) -> int | None:
    """Number of occupied bands, if they are evenly spaced."""
    edges = np.diff(occupancy.astype(np.int8), prepend=np.int8(0), append=np.int8(0))
    starts = np.flatnonzero(edges == 1)
    if len(starts) == 0:
        return None
    if len(starts) == 1:
        return 1
    pitches = np.diff(starts)
    mean = float(pitches.mean())
    if mean <= 0:
        return None
    if float(np.abs(pitches - mean).max()) > GRID_TOLERANCE * mean:
        return None  # irregular spacing: this is not a grid
    # The pitch must also match the image's dimensions, or the last cell would
    # overflow.
    expected = len(occupancy) / len(starts)
    if abs(expected - mean) > GRID_TOLERANCE * expected:
        return None
    return int(len(starts))


def _grid_boxes(mask: np.ndarray, width: int, height: int,
                grid: tuple[int, int]) -> tuple[list[Box], int]:
    """Cells of equal size; cells without a single pixel are ignored."""
    columns, rows = grid
    if columns < 1 or rows < 1:
        raise SheetError("invalid grid: rows and columns must be positive")
    cell_width, cell_height = width / columns, height / rows
    boxes: list[Box] = []
    empty = 0
    for row in range(rows):
        for column in range(columns):
            x0, y0 = round(column * cell_width), round(row * cell_height)
            x1, y1 = round((column + 1) * cell_width), round((row + 1) * cell_height)
            if not mask[y0:y1, x0:x1].any():
                empty += 1
                continue
            boxes.append(Box(float(x0), float(y0), float(x1), float(y1)))
    return boxes, empty


# ------------------------------------------------------------------ extraction


def _mask_box(mask: np.ndarray) -> Box | None:
    rows = np.flatnonzero(mask.any(axis=1))
    columns = np.flatnonzero(mask.any(axis=0))
    if len(rows) == 0 or len(columns) == 0:
        return None
    return Box(float(columns[0]), float(rows[0]),
               float(columns[-1] + 1), float(rows[-1] + 1))


def _trim(mask: np.ndarray, box: Box) -> Box | None:
    """Shrink a box to the content it actually holds."""
    x0, y0 = int(box.x0), int(box.y0)
    inner = _mask_box(mask[y0:int(box.y1), x0:int(box.x1)])
    if inner is None:
        return None
    return Box(x0 + inner.x0, y0 + inner.y0, x0 + inner.x1, y0 + inner.y1)


def _shared_frame(mask: np.ndarray, boxes: list[Box],
                  width: int, height: int) -> list[Box]:
    """A single frame for every cell: the union of their contents.

    Cropping each cell to its own content would move the subject from one
    frame to the next; cropping nothing leaves the sheet's margins. So a single
    box is computed, expressed in the cell's frame, and applied everywhere --
    exactly what `sprites.py` does when assembling renders.
    """
    insets: list[Box] = []
    for box in boxes:
        inner = _trim(mask, box)
        if inner is not None:
            insets.append(Box(inner.x0 - box.x0, inner.y0 - box.y0,
                              inner.x1 - box.x0, inner.y1 - box.y0))
    common = union_all(insets)
    if common is None:
        return boxes
    return [Box(box.x0 + common.x0, box.y0 + common.y0,
                box.x0 + common.x1, box.y0 + common.y1).clamp(width, height)
            for box in boxes]


def _crop(image: Image.Image, box: Box) -> bytes:
    piece = image.crop((int(box.x0), int(box.y0), int(box.x1), int(box.y1)))
    buffer = io.BytesIO()
    piece.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
