"""Splitting a multi-icon SVG sheet, and bringing the icons into the studio.

Everything is local: XML parsing, geometry, store. No network call, no render.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.pipeline.base import Context
from gamestudio.sheet import SheetError, import_sheet
from gamestudio.store.assets import AssetStore
from gamestudio.store.db import Database
from gamestudio.store.library import Librarian
from gamestudio.svg import split_svg
from gamestudio.svg.geometry import bbox_of, path_points

SVG_NS = "{http://www.w3.org/2000/svg}"

# An Illustrator-style sheet: a wrapping layer, a background, one group per
# icon, a shared gradient and an unused gradient.
SHEET = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">
  <defs>
    <linearGradient id="gold"><stop offset="0" stop-color="#fc0"/></linearGradient>
    <linearGradient id="unused"><stop offset="0" stop-color="#f00"/></linearGradient>
  </defs>
  <rect width="200" height="100" fill="#111"/>
  <g id="Layer_1" fill="none" stroke="#fff" stroke-width="2">
    <g id="heart" transform="translate(10,10)">
      <path d="M0 0 h20 v20 h-20 z"/>
      <circle cx="10" cy="10" r="4"/>
    </g>
    <g id="star" transform="translate(60,10)">
      <path d="M0 0 L20 0 L10 18 Z" fill="url(#gold)"/>
    </g>
    <path id="bolt" d="M110 12 l10 0 l-6 8 l8 0 l-14 16 l4 -12 l-8 0 z"/>
    <g data-name="Gift" transform="translate(160,10) scale(2)">
      <rect width="10" height="10"/>
    </g>
  </g>
</svg>
"""

# A <symbol> sprite sheet: the whole document is hidden.
SYMBOLS = """<svg xmlns="http://www.w3.org/2000/svg" style="display:none">
  <symbol id="ic-home" viewBox="0 0 24 24"><path d="M2 12 L12 2 L22 12 Z"/></symbol>
  <symbol id="ic-star" viewBox="0 0 24 24"><title>Star</title>
    <path d="M12 2 L14 10 L22 10 L12 22 Z"/></symbol>
</svg>
"""

# An optimized sheet: no group left, two icons in two pieces each.
FLAT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">
  <path id="path12" d="M4 4h16v16H4z"/><path d="M8 8h8v3H8z"/>
  <path d="M64 4h16v16H64z"/><path d="M68 8h8v3H68z"/>
</svg>
"""


def icons_of(sheet):
    return {piece.name: piece for piece in sheet.pieces}


# -------------------------------------------------------------------- geometry


def test_a_compressed_arc_reads_like_a_spaced_arc():
    """Regression: `a5 5 0 0110 0` glues the flags and the x coordinate.

    A naive tokenizer reads the number 0110 there and puts the icon anywhere.
    """
    compressed = path_points("M0 0a5 5 0 0110 0")
    spaced = path_points("M0 0 a5 5 0 0 1 10 0")
    assert compressed == spaced
    # The arc is bounded by the (rx, ry) disk centered on its chord: never too
    # small, which matters so that nothing is cropped on extraction.
    box = bbox_of(compressed)
    assert box is not None
    assert (box.x0, box.y0, box.x1, box.y1) == (0.0, -5.0, 10.0, 5.0)


def test_the_box_accounts_for_the_inherited_stroke():
    """A `stroke` set on the sheet thickens every icon it holds."""
    sheet = split_svg(SHEET)
    heart = icons_of(sheet)["heart"]
    # Group at (10,10), 20x20 shape, stroke of 2 -> 1 unit on each side.
    assert (heart.x, heart.y) == (9.0, 9.0)
    assert (heart.width, heart.height) == (22.0, 22.0)


# ----------------------------------------------------------------------- split


def test_a_sheet_gives_one_icon_per_group():
    sheet = split_svg(SHEET)
    assert sheet.strategy == "clusters"
    assert [icon.name for icon in sheet.pieces] == ["heart", "star", "bolt", "gift"]
    assert any("background" in warning for warning in sheet.warnings)


def test_icons_come_in_reading_order():
    sheet = split_svg(SHEET)
    xs = [icon.x for icon in sheet.pieces]
    assert xs == sorted(xs)


def test_an_extracted_icon_is_a_standalone_svg():
    """Fitted viewBox, flattened transforms, inherited styles copied."""
    star = icons_of(split_svg(SHEET))["star"]
    root = ET.fromstring(star.svg)

    assert root.tag == f"{SVG_NS}svg"
    viewbox = [float(v) for v in root.get("viewBox").split()]
    assert viewbox == pytest.approx([star.x, star.y, star.width, star.height])
    assert root.get("width") == f"{star.width:.6g}"

    group = root.find(f"{SVG_NS}g")
    assert group.get("stroke") == "#fff", "the layer's stroke must survive"
    assert group.get("fill") == "none"
    # The original group keeps its own transform, the ancestor is flattened.
    assert group.find(f"{SVG_NS}g").get("transform") == "translate(60,10)"


def test_only_referenced_definitions_are_copied():
    """Copying the whole <defs> would bloat each icon with the whole sheet."""
    icons = icons_of(split_svg(SHEET))
    assert 'id="gold"' in icons["star"].svg
    assert "unused" not in icons["star"].svg
    assert "linearGradient" not in icons["bolt"].svg


def test_a_transformed_group_stays_whole():
    """`scale(2)`: the box doubles, and the group is not broken into pieces."""
    gift = icons_of(split_svg(SHEET))["gift"]
    # 10x10 at scale 2, plus the inherited stroke (2 units, so 1 on each side)
    # also at scale 2.
    assert (gift.width, gift.height) == (24.0, 24.0)


# --------------------------------------------------------------------- symbols


def test_symbols_become_visible_icons():
    sheet = split_svg(SYMBOLS)
    assert sheet.strategy == "symbols"
    assert [icon.name for icon in sheet.pieces] == ["ic-home", "ic-star"]

    home = ET.fromstring(sheet.pieces[0].svg)
    assert home.find(f".//{SVG_NS}symbol") is None, "a <symbol> is not drawn"
    assert "display:none" not in sheet.pieces[0].svg, (
        "the sprite sheet hides itself, not the icons extracted from it")


def test_the_symbols_strategy_is_refused_without_a_symbol():
    with pytest.raises(SheetError):
        split_svg(FLAT, strategy="symbols")


# ------------------------------------------------------------------- proximity


def test_close_pieces_form_a_single_icon():
    sheet = split_svg(FLAT)
    assert len(sheet.pieces) == 2
    assert all(icon.elements == 2 for icon in sheet.pieces)


def test_the_gap_is_the_splits_only_setting():
    """Wide, it glues the whole sheet back; it is the escape hatch."""
    assert len(split_svg(FLAT, gap=60.0).pieces) == 1
    assert len(split_svg(FLAT, gap=1.0).pieces) == 2


def test_a_tool_identifier_does_not_name_an_icon():
    """`path12` says nothing: the position in the sheet is better."""
    names = [piece.name for piece in split_svg(FLAT, strategy="groups").pieces]
    assert "path12" not in names
    assert names == ["icon-01", "icon-02", "icon-03", "icon-04"]


def test_the_groups_strategy_groups_nothing():
    assert len(split_svg(FLAT, strategy="groups").pieces) == 4


def test_the_single_strategy_keeps_the_whole_sheet():
    sheet = split_svg(SHEET, strategy="single")
    assert len(sheet.pieces) == 1
    assert (sheet.pieces[0].width, sheet.pieces[0].height) == (200.0, 100.0)


def test_an_unreadable_file_is_reported(tmp_path: Path):
    with pytest.raises(SheetError):
        split_svg("<svg><g></svg>")          # malformed XML
    with pytest.raises(SheetError):
        split_svg("<html><body/></html>")    # not an SVG
    with pytest.raises(SheetError):
        split_svg(tmp_path / "missing.svg")  # path that does not exist


def test_write_one_file_per_icon(tmp_path: Path):
    written = split_svg(SHEET).write(tmp_path)
    assert sorted(p.name for p in written) == [
        "bolt.svg", "gift.svg", "heart.svg", "star.svg"]
    assert all(ET.fromstring(p.read_text(encoding="utf-8")) is not None for p in written)


# ---------------------------------------------------------------------- import


@pytest.fixture()
def ctx(tmp_path: Path) -> Context:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    settings.ensure_dirs()
    return Context(project="icons", settings=settings, db=Database(settings.db_path),
                   store=AssetStore(settings.assets_dir))


@pytest.fixture()
def sheet_file(tmp_path: Path) -> Path:
    path = tmp_path / "my-sheet.svg"
    path.write_text(SHEET, encoding="utf-8")
    return path


def test_a_multi_import_creates_one_asset_per_icon(ctx: Context, sheet_file: Path):
    result = import_sheet(sheet_file, ctx, multi=True)

    assert len(result.assets) == 4
    assert {a.kind for a in result.assets} == {"vector"}
    meta = result.assets[0].meta
    assert meta["role"] == "icon"
    assert meta["sheet"] == "my-sheet"
    assert meta["project"] == "icons"
    assert meta["source"] == "my-sheet.svg"
    # Each asset really is its icon's SVG, not the whole sheet.
    for asset in result.assets:
        assert "viewBox" in asset.path.read_text(encoding="utf-8")
    assert result.summary()["count"] == 4


def test_the_import_keeps_only_the_selection(ctx: Context, sheet_file: Path):
    result = import_sheet(sheet_file, ctx, multi=True, keep=["heart", "bolt"])
    assert sorted(a.meta["name"] for a in result.assets) == ["bolt", "heart"]


def test_an_import_without_multi_keeps_the_whole_file(ctx: Context, sheet_file: Path):
    result = import_sheet(sheet_file, ctx, multi=False)
    assert len(result.assets) == 1
    asset = result.assets[0]
    assert asset.meta["role"] == "sheet"
    assert asset.path.read_text(encoding="utf-8") == SHEET


def test_an_empty_selection_is_refused(ctx: Context, sheet_file: Path):
    with pytest.raises(SheetError):
        import_sheet(sheet_file, ctx, multi=True, keep=["missing"])


def test_the_library_files_icons_by_sheet(ctx: Context, sheet_file: Path):
    import_sheet(sheet_file, ctx, multi=True)
    librarian = Librarian(ctx.db, ctx.store, ctx.settings.library_dir)
    manifest = librarian.sync_project("icons")

    assert manifest["icon_sheets"] == ["my-sheet"]
    folder = librarian.project_dir("icons") / "icons" / "my-sheet"
    assert sorted(p.name for p in folder.glob("*.svg")) == [
        "bolt.svg", "gift.svg", "heart.svg", "star.svg"]

    document = json.loads((folder / "sheet.json").read_text(encoding="utf-8"))
    assert document["source"] == "my-sheet.svg"
    assert len(document["icons"]) == 4
    assert document["icons"][0]["bbox"] is not None
