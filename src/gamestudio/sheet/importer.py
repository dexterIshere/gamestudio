"""Bringing a sheet into the studio: store, database, library.

The medium decides the split method -- XML structure for an SVG, pixels for a
PNG -- but not what follows: in both cases each extracted element becomes a
standalone asset, filed in `.gamestudio/library/icons/<sheet>/`.

Two paths, chosen by the `multi` option -- the one the interface ticks before
choosing the file:

- `multi=False`: the file comes in as is, one more sheet;
- `multi=True`: the sheet is split, and *each element becomes a file*.

Everything is local in both cases: geometry and storage, plus the local matting
model when an opaque sheet calls for it (`matting`).
"""

from __future__ import annotations

import inspect
import io
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.models import Asset
from .layout import Piece, SheetError, SheetSplit, slug

# What each medium can read.
VECTOR_SUFFIXES = frozenset({".svg"})
RASTER_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
SUFFIXES = VECTOR_SUFFIXES | RASTER_SUFFIXES

# Roles set on the assets: a split piece, or the whole sheet.
PIECE_ROLE = "icon"
SHEET_ROLE = "sheet"


@dataclass
class SheetImport:
    """What an import produced: the split, and the assets created."""

    sheet: SheetSplit
    multi: bool
    sheet_name: str
    assets: list[Asset] = field(default_factory=list)
    rasters: list[Asset] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        by_name = {asset.meta.get("name", ""): asset.id for asset in self.assets}
        raster_by_name = {asset.meta.get("name", ""): asset.id for asset in self.rasters}
        return {
            "sheet": self.sheet_name,
            "source": self.sheet.source,
            "multi": self.multi,
            "strategy": self.sheet.strategy,
            "count": len(self.assets),
            "pieces": [
                {**piece.summary(),
                 "asset_id": by_name.get(piece.name),
                 "png_asset_id": raster_by_name.get(piece.name)}
                for piece in self.sheet.pieces
            ],
            "warnings": self.sheet.warnings,
        }


def inspect_sheet(path: str | Path, **options: Any) -> SheetSplit:
    """Dry-run split: what would be extracted, without writing anything anywhere."""
    return split_sheet(Path(path).expanduser(), **options)


def split_sheet(path: Path, **options: Any) -> SheetSplit:
    """Dispatch to the medium's split, by the file's extension.

    Options a split does not know are ignored rather than refused: the
    interface offers the settings of both media, and need not know which apply
    to the chosen file.
    """
    suffix = path.suffix.lower()
    if suffix in VECTOR_SUFFIXES:
        from ..svg.split import split_svg as splitter
    elif suffix in RASTER_SUFFIXES:
        from .bitmap import split_bitmap as splitter
    else:
        raise SheetError(f"{path.name}: unsupported format ({', '.join(sorted(SUFFIXES))})")
    accepted = set(inspect.signature(splitter).parameters)
    return splitter(path, **{k: v for k, v in options.items()
                             if k in accepted and v is not None})


def import_sheet(
    path: str | Path,
    ctx,
    *,
    multi: bool = True,
    keep: Iterable[str] | None = None,
    raster_size: int = 0,
    sheet_name: str = "",
    **options: Any,
) -> SheetImport:
    """Bring a sheet into the store, split if `multi`.

    - `keep`: keep only these elements (by name), to confirm a selection after
      an `inspect_sheet`;
    - `raster_size`: for a vector sheet, also produce one PNG per icon (longest
      side, in pixels; requires `pip install -e .[vector]`).
    """
    source = Path(path).expanduser()
    if not source.exists():
        raise SheetError(f"file not found: {source}")
    name = slug(sheet_name or source.stem) or "sheet"

    if not multi:
        return _import_whole(source, ctx, name, raster_size=raster_size)

    sheet = split_sheet(source, **options)
    pieces = list(sheet.pieces)
    if keep is not None:
        wanted = set(keep)
        pieces = [piece for piece in pieces if piece.name in wanted]
    if not pieces:
        raise SheetError(f"no element to import from {source.name} (strategy “{sheet.strategy}”)")

    result = SheetImport(sheet=sheet, multi=True, sheet_name=name)
    for piece in pieces:
        asset = ctx.store.put_bytes(
            piece.data, piece.suffix,
            meta=_piece_meta(ctx, source, name, sheet, piece),
        )
        ctx.db.save_asset(asset)
        result.assets.append(asset)
        raster = _rasterize(ctx, piece, asset, raster_size)
        if raster is not None:
            result.rasters.append(raster)
    return result


def _import_whole(source: Path, ctx, name: str, *, raster_size: int) -> SheetImport:
    """`multi=False`: the file comes in whole, without a split."""
    data = source.read_bytes()
    # The "single" split only measures the sheet's actual content, which its
    # declared dimensions do not always tell.
    sheet = split_sheet(source, strategy="single")
    measured = sheet.pieces[0] if sheet.pieces else None
    piece = Piece(
        name=name, index=0,
        x=measured.x if measured else 0.0,
        y=measured.y if measured else 0.0,
        width=measured.width if measured else sheet.width,
        height=measured.height if measured else sheet.height,
        data=data, suffix=source.suffix.lower(),
        elements=measured.elements if measured else 0,
    )
    sheet.pieces = [piece]

    meta = {
        "role": SHEET_ROLE, "project": ctx.project, "sheet": name, "name": name,
        "source": source.name, "index": 0,
        "bbox": [round(piece.x, 3), round(piece.y, 3),
                 round(piece.width, 3), round(piece.height, 3)],
    }
    pixels = _measure(data, piece.suffix)
    if pixels is not None:
        meta["pixels"] = pixels
    asset = ctx.store.put_bytes(data, source.suffix.lower(), meta=meta)
    ctx.db.save_asset(asset)
    result = SheetImport(sheet=sheet, multi=False, sheet_name=name, assets=[asset])
    raster = _rasterize(ctx, piece, asset, raster_size)
    if raster is not None:
        result.rasters.append(raster)
    return result


def _piece_meta(ctx, source: Path, sheet_name: str, sheet: SheetSplit,
                piece: Piece) -> dict[str, Any]:
    meta = {
        "role": PIECE_ROLE,
        "project": ctx.project,
        "sheet": sheet_name,
        "source": source.name,
        "name": piece.name,
        "index": piece.index,
        "strategy": sheet.strategy,
        "elements": piece.elements,
        "bbox": [round(piece.x, 3), round(piece.y, 3),
                 round(piece.width, 3), round(piece.height, 3)],
    }
    pixels = _measure(piece.data, piece.suffix)
    if pixels is not None:
        meta["pixels"] = pixels
    return meta


def _measure(data: bytes, suffix: str) -> list[int] | None:
    """Pixel dimensions of a bitmap file; None for a vector one.

    The frame in the sheet (`bbox`) says where the element was taken from, not
    the size of the produced file: cropping rounds to the pixel, and a
    rasterized SVG no longer has its original's dimensions at all. Only the
    image header is read -- measuring a thousand icons costs nothing.
    """
    if suffix.lower() == ".svg":
        return None
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            return [image.width, image.height]
    except OSError:
        return None


def _rasterize(ctx, piece: Piece, origin: Asset, size: int) -> Asset | None:
    """Optional PNG of a vector icon, linked to its SVG."""
    if size <= 0 or piece.suffix != ".svg":
        return None
    from ..svg.raster import rasterize

    data = rasterize(piece.data.decode("utf-8"), size,
                     width=piece.width, height=piece.height)
    asset = ctx.store.put_bytes(data, ".png", kind="image", meta={
        **origin.meta, "format": "png", "size": size, "source_asset": origin.id,
        "pixels": _measure(data, ".png"),
    })
    ctx.db.save_asset(asset)
    return asset
