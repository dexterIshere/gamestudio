"""Splitting an SVG sheet: exact geometry, no external dependency.

Entering the store and the library is shared by both media: see
`gamestudio.sheet`.
"""

from .split import (
    STRATEGIES,
    SvgDocument,
    SvgError,
    SvgIcon,
    parse_document,
    split_svg,
)

__all__ = [
    "STRATEGIES",
    "SvgDocument",
    "SvgError",
    "SvgIcon",
    "parse_document",
    "split_svg",
]
