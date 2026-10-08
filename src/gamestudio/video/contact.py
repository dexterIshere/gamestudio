"""Contact sheet: all frames of a batch in a single image.

It serves to judge a batch at a glance: two hundred frames are not looked at
file by file. Assembly follows the same rule as sprite sheets (`sprites.py`):
**a single cell size, computed once for the whole batch**, and no cropping. A
cell cropped or resized to its own content would make the subject jump from
one cell to the next -- and that is exactly what one looks at: where the
subject is from one frame to the next.

The assembly itself is that of `pack_sheet`, so that the rule exists only once.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageOps

from ..sprites import pack_sheet
from .probe import VideoError

CONTACT_NAME = "contact.png"

# Color of the line between two cells: a mid gray, visible on a dark frame as
# on a light one.
BORDER_COLOR = (90, 90, 90, 255)


def common_size(frames: list[Path]) -> tuple[int, int]:
    """The batch's cell size: the largest frame, with even width and height."""
    sizes = []
    for frame in frames:
        with Image.open(frame) as image:
            sizes.append(image.size)
    width = max(size[0] for size in sizes)
    height = max(size[1] for size in sizes)
    return (max(2, width - width % 2), max(2, height - height % 2))


def grid_columns(count: int, capacity: int) -> int:
    """Columns of the grid: a divisor of the count, if possible.

    `pack_sheet` would fill the last row with empty cells whenever the count is
    not a multiple of the column count -- and an empty cell looks like a frame
    that does not exist, and a half-empty last row reads badly. So the largest
    divisor of the count that fits the width is sought, but only if it does not
    stray too far from the maximum fill: for a prime count, one empty cell beats
    a column twenty rows long.
    """
    limit = max(1, min(count, capacity))
    for columns in range(limit, limit // 2, -1):
        if count % columns == 0:
            return columns
    return limit


def contact_sheet(frames: list[Path], output: str | Path, *, columns: int = 0,
                  cell_width: int = 240, border: int = 1,
                  max_width: int = 4096) -> Path:
    """Assemble the frames into a grid: one image, one cell per frame.

    `cell_width` brings cells down to a readable width (0 keeps their size); the
    factor is computed once on the common size, so every cell gets it
    identically. `max_width` bounds the sheet's width and so its number of
    columns -- when `columns` is omitted, it is chosen so that the grid is
    complete (see `grid_columns`).
    """
    if not frames:
        raise VideoError("no frame to assemble")

    size = common_size(frames)
    if cell_width and cell_width < size[0]:
        factor = cell_width / size[0]
        width = max(2, cell_width - cell_width % 2)
        height = max(2, round(size[1] * factor))
        size = (width, height - height % 2)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Cells go through the disk because `pack_sheet` reads files: the price of
    # reusing the rule rather than rewriting it.
    with tempfile.TemporaryDirectory(prefix="gamestudio-contact-") as work:
        cells: list[Path] = []
        for index, frame in enumerate(frames):
            with Image.open(frame) as image:
                cell = image.convert("RGBA").resize(size, Image.LANCZOS)
            if border > 0:
                cell = ImageOps.expand(cell, border=border, fill=BORDER_COLOR)
            path = Path(work) / f"cell-{index:04d}.png"
            cell.save(path)
            cells.append(path)
        if columns <= 0:
            # A cell's width, line included: what `pack_sheet` measures to know
            # how many columns fit.
            cell = size[0] + 2 * max(border, 0)
            columns = grid_columns(len(frames), max_width // max(cell, 1))
        # `crop=None`: no cell is cropped, their size is already right.
        pack_sheet(cells, output, crop=None, columns=columns, max_width=max_width)
    return output
