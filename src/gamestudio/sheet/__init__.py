"""Sheets: a file that holds several elements, split into single files.

Two media, one rule: two pieces that touch belong to the same drawing.
`split_sheet` dispatches on the extension (SVG or bitmap), `import_sheet`
brings the result into the store and the library.
"""

from .bitmap import STRATEGIES as BITMAP_STRATEGIES
from .bitmap import split_bitmap
from .importer import (
    PIECE_ROLE,
    RASTER_SUFFIXES,
    SHEET_ROLE,
    SUFFIXES,
    VECTOR_SUFFIXES,
    SheetImport,
    import_sheet,
    inspect_sheet,
    split_sheet,
)
from .layout import Box, Piece, SheetError, SheetSplit

__all__ = [
    "BITMAP_STRATEGIES",
    "PIECE_ROLE",
    "RASTER_SUFFIXES",
    "SHEET_ROLE",
    "SUFFIXES",
    "VECTOR_SUFFIXES",
    "Box",
    "Piece",
    "SheetError",
    "SheetImport",
    "SheetSplit",
    "import_sheet",
    "inspect_sheet",
    "split_bitmap",
    "split_sheet",
]
