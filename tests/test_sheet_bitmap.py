"""Splitting a bitmap sheet: icon pack, frame grid, plain background.

Nothing is loaded from the project's disk: each sheet is painted here, so the
expected truth is known to the pixel.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from gamestudio.config import Settings
from gamestudio.pipeline.base import Context
from gamestudio.sheet import SheetError, import_sheet, split_bitmap, split_sheet
from gamestudio.sheet.bitmap import _bridged, detect_grid, foreground_mask
from gamestudio.store.assets import AssetStore
from gamestudio.store.db import Database
from gamestudio.store.library import Librarian


def pack(width: int = 240, height: int = 100) -> Image.Image:
    """Four spaced icons; the first one is in two neighboring pieces."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, 40, 40], fill=(255, 80, 80, 255))
    draw.rectangle([16, 44, 34, 48], fill=(255, 80, 80, 255))   # base of icon 1
    draw.ellipse([80, 12, 110, 42], fill=(80, 180, 255, 255))
    draw.polygon([(150, 10), (175, 40), (125, 40)], fill=(120, 220, 120, 255))
    draw.rectangle([200, 15, 225, 40], fill=(240, 200, 60, 255))
    return image


def grid_sheet(columns: int = 4, rows: int = 2, cell: int = 64) -> Image.Image:
    """A frame sheet: equal cells, and a subject that moves inside them.

    This is the case that matters: from one frame to the next the subject
    moves, so cropping each cell to its content would make its pivot jump.
    """
    image = Image.new("RGBA", (columns * cell, rows * cell), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for row in range(rows):
        for column in range(columns):
            x, y = column * cell, row * cell
            drift = 4 * column          # the subject moves forward frame after frame
            draw.ellipse([x + 12 + drift, y + 12, x + cell - 20 + drift, y + cell - 12],
                         fill=(200 - 20 * column, 100 + 30 * row, 160, 255))
    return image


def as_file(image: Image.Image, path: Path, name: str) -> Path:
    target = path / name
    image.save(target)
    return target


def sizes(sheet) -> list[tuple[int, int]]:
    return [(round(piece.width), round(piece.height)) for piece in sheet.pieces]


# -------------------------------------------------------------------- free pack


def test_a_pack_gives_one_icon_per_drawing():
    sheet = split_bitmap(pack())
    assert sheet.strategy == "blobs"
    assert len(sheet.pieces) == 4
    assert [piece.name for piece in sheet.pieces][:2] == ["icon-01", "icon-02"]


def test_neighboring_pieces_form_a_single_icon():
    """The base stuck under the square belongs to the same icon."""
    first = split_bitmap(pack()).pieces[0]
    assert (first.x, first.y) == (10, 10)
    assert first.height == 39, "the box goes down to the base"


def test_the_gap_is_the_only_setting():
    """At zero, the base comes apart; wide, the whole sheet glues back."""
    assert len(split_bitmap(pack(), gap=0.0).pieces) == 5
    assert len(split_bitmap(pack(), gap=60.0).pieces) == 1


def test_tiny_specks_are_dropped():
    image = pack()
    ImageDraw.Draw(image).point((60, 90), fill=(255, 255, 255, 255))
    assert len(split_bitmap(image, min_size=4).pieces) == 4
    assert len(split_bitmap(image, min_size=0).pieces) == 5


def test_icons_come_out_in_reading_order():
    xs = [piece.x for piece in split_bitmap(pack()).pieces]
    assert xs == sorted(xs)


def test_each_icon_is_a_standalone_png():
    piece = split_bitmap(pack()).pieces[0]
    assert piece.suffix == ".png"
    with Image.open(io.BytesIO(piece.data)) as extracted:
        assert extracted.size == (round(piece.width), round(piece.height))
        assert extracted.mode == "RGBA"


# ------------------------------------------------------------------------ grid


def test_a_grid_is_recognized_by_its_gutters():
    assert detect_grid(foreground_mask(grid_sheet())[0]) == (4, 2)
    assert detect_grid(foreground_mask(pack())[0]) is None


def test_frames_all_keep_the_same_size():
    """A single frame for every cell: the subject moves, not the pivot."""
    sheet = split_bitmap(grid_sheet())
    assert sheet.strategy == "grid"
    assert len(sheet.pieces) == 8
    assert len(set(sizes(sheet))) == 1, "every frame has the same size"
    assert [piece.name for piece in sheet.pieces][:2] == ["frame-01", "frame-02"]

    # The shared frame is narrower than the cell, and wide enough to hold the
    # subject in all its positions.
    width, height = sizes(sheet)[0]
    assert width < 64 and height < 64
    assert width >= 33 + 4 * 3, "the subject's movement fits in the frame"


def test_per_cell_cropping_stays_possible_on_request():
    """The escape hatch: each cell cropped to its content, moving pivot."""
    sheet = split_bitmap(grid_sheet(), trim=True)
    assert len(set(sizes(sheet))) == 1  # here the subject keeps the same size
    assert sizes(sheet)[0][0] < sizes(split_bitmap(grid_sheet()))[0][0]


def test_an_adjoining_grid_must_be_given():
    """Without gutters, there is no regularity to read: rows and columns decide."""
    image = Image.new("RGBA", (128, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 63, 63], fill=(200, 60, 60, 255))
    draw.rectangle([64, 0, 127, 63], fill=(60, 60, 200, 255))

    assert len(split_bitmap(image).pieces) == 1, "adjoining cells make a single block"
    assert not foreground_mask(image)[1], "a full sheet has no background to remove"
    sheet = split_bitmap(image, strategy="grid", rows=1, columns=2)
    assert sizes(sheet) == [(64, 64), (64, 64)]

    with pytest.raises(SheetError):
        split_bitmap(pack(), strategy="grid")


def test_empty_cells_are_ignored():
    image = grid_sheet()
    ImageDraw.Draw(image).rectangle([192, 64, 255, 127], fill=(0, 0, 0, 0))
    sheet = split_bitmap(image, strategy="grid", rows=2, columns=4)
    assert len(sheet.pieces) == 7
    assert any("empty" in warning for warning in sheet.warnings)


# ------------------------------------------------------------------ background


def test_a_plain_background_is_guessed_then_removed():
    """A sheet flattened on white comes out cut out."""
    image = Image.new("RGB", (160, 80), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, 50, 50], fill=(20, 20, 20))
    draw.ellipse([100, 15, 145, 60], fill=(200, 30, 30))

    sheet = split_bitmap(image)
    assert len(sheet.pieces) == 2
    assert any("cut out" in warning for warning in sheet.warnings)
    # The disk: the corners of its box were background, they became transparent.
    with Image.open(io.BytesIO(sheet.pieces[1].data)) as extracted:
        assert extracted.getchannel("A").getextrema()[0] == 0


def test_a_halo_linking_the_drawings_widens_the_tolerance():
    """Regression: an icon sheet on a dark background came out as one block.

    The halo around each drawing is too close to the background to be a
    drawing, too far to be removed: it links the neighbors, and the whole sheet
    becomes a single piece. The tolerance must widen by itself.
    """
    background, halo = (2, 12, 26), (16, 28, 46)   # halo 20 from the background: above 16
    image = Image.new("RGB", (400, 200), background)
    draw = ImageDraw.Draw(image)
    for x in (100, 300):
        draw.ellipse([x - 95, 5, x + 95, 195], fill=halo)      # touching halos
        draw.ellipse([x - 40, 60, x + 40, 140], fill=(240, 200, 60))

    start, _ = foreground_mask(image, tolerance=16)
    assert _bridged(start), "at first, the halo does link both drawings"

    sheet = split_bitmap(image)
    assert len(sheet.pieces) == 2
    assert any("tolerance widened" in warning for warning in sheet.warnings)
    assert set(sizes(sheet)) == {(81, 81)}, "each drawing comes out without its halo"


def test_the_background_can_be_given():
    image = Image.new("RGB", (80, 40), (10, 200, 10))  # saturated green background
    ImageDraw.Draw(image).rectangle([10, 10, 30, 30], fill=(255, 255, 255))
    sheet = split_bitmap(image, background="#0ac80a")
    assert len(sheet.pieces) == 1
    assert sizes(sheet) == [(21, 21)]


def test_alpha_wins_over_color():
    mask, keyed = foreground_mask(pack())
    assert not keyed, "a transparent image needs no guessed background"
    assert mask.any()


def test_an_empty_sheet_says_so():
    sheet = split_bitmap(Image.new("RGBA", (32, 32), (0, 0, 0, 0)))
    assert sheet.pieces == []
    assert any("empty" in warning for warning in sheet.warnings)


# ---------------------------------------------------------------- model matting


def opaque() -> Image.Image:
    """Two drawings flattened on white: a square A, an ellipse B on the right."""
    image = Image.new("RGB", (160, 80), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, 50, 50], fill=(20, 20, 20))
    draw.ellipse([100, 15, 145, 60], fill=(90, 140, 190))
    return image


def fake_model(*, erase_b: bool = False, erase_b_cropped: bool = False):
    """Simulated matting: alpha 200 off the white background, adjustable on ellipse B.

    The whole sheet is recognized by its size (160 x 80); any other call is a
    cropped recovery pass. `erase_b` simulates the subtle element the model
    takes for background on the whole sheet, `erase_b_cropped` the case where
    even isolated it does not stand out.
    """
    import numpy as np

    def remove(image):
        arr = np.array(image.convert("RGBA"), dtype=np.uint8)
        background = np.abs(arr[..., :3].astype(int) - 255).max(axis=2) <= 16
        alpha = np.where(background, 0, 200).astype(np.uint8)
        whole = image.size == (160, 80)
        if whole and erase_b:
            alpha[:, 80:] = 0
        if not whole and erase_b_cropped:
            alpha[:] = 0
        arr[..., 3] = alpha
        return Image.fromarray(arr, "RGBA")

    return remove


def test_the_model_cuts_out_an_opaque_sheet(monkeypatch):
    monkeypatch.setattr("gamestudio.vision.detect.remove_background", fake_model())
    sheet = split_bitmap(opaque(), matting=True)
    assert len(sheet.pieces) == 2
    assert any("model" in warning for warning in sheet.warnings)
    # The pieces' alpha is the model's (200), not the binary mask.
    with Image.open(io.BytesIO(sheet.pieces[0].data)) as extracted:
        assert extracted.getchannel("A").getextrema()[1] == 200


def test_an_element_erased_by_the_model_is_recovered(monkeypatch):
    """The subtle element erased on the whole sheet comes out cropped alone."""
    monkeypatch.setattr("gamestudio.vision.detect.remove_background",
                        fake_model(erase_b=True))
    sheet = split_bitmap(opaque(), matting=True)
    assert len(sheet.pieces) == 2
    assert any("cut out again" in warning for warning in sheet.warnings)
    with Image.open(io.BytesIO(sheet.pieces[1].data)) as extracted:
        assert extracted.getchannel("A").getextrema()[1] == 200


def test_what_the_model_refuses_twice_is_dropped(monkeypatch):
    """A clump rejected even when cropped is background: color does not revive it.

    This is the case of the texture clumps of a noisy background, which color
    matting takes for elements.
    """
    monkeypatch.setattr("gamestudio.vision.detect.remove_background",
                        fake_model(erase_b=True, erase_b_cropped=True))
    sheet = split_bitmap(opaque(), matting=True)
    assert len(sheet.pieces) == 1
    assert any("takes for background" in warning for warning in sheet.warnings)


def test_without_the_model_color_cuts_out(monkeypatch):
    from gamestudio.vision.detect import ToolUnavailable

    def unavailable(image):
        raise ToolUnavailable("rembg missing")

    monkeypatch.setattr("gamestudio.vision.detect.remove_background", unavailable)
    sheet = split_bitmap(opaque(), matting=True)
    assert len(sheet.pieces) == 2
    assert any("unavailable" in warning for warning in sheet.warnings)


def test_edges_are_cleaned_of_the_background(monkeypatch):
    """The background film goes away: edge color corrected, veil erased.

    The dark square is ringed by a band already blended with the white
    background, like a real sheet's antialiasing: `C = a*F + (1-a)*B`. Once the
    model declares the band semi-transparent, its color must become the
    square's again -- otherwise, placed on a light backdrop, it would keep a
    fringe.
    """
    import numpy as np

    image = Image.new("RGB", (160, 80), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([9, 9, 51, 51], fill=(137, 137, 137))    # band half background
    draw.rectangle([10, 10, 50, 50], fill=(20, 20, 20))

    def model(pil):
        arr = np.array(pil.convert("RGBA"), dtype=np.uint8)
        alpha = np.zeros(arr.shape[:2], np.uint8)
        alpha[9:52, 9:52] = 128                             # the band, at half
        alpha[10:51, 10:51] = 255
        alpha[60:70, 100:120] = 20                          # veil over bare background
        arr[..., 3] = alpha
        return Image.fromarray(arr, "RGBA")

    monkeypatch.setattr("gamestudio.vision.detect.remove_background", model)
    sheet = split_bitmap(image, matting=True)
    assert len(sheet.pieces) == 1, "the veil does not become an element"
    with Image.open(io.BytesIO(sheet.pieces[0].data)) as extracted:
        corner = extracted.getpixel((0, 0))
    # (137 - 0.5 * 255) / 0.5 = 19: the band gets the square's color back.
    assert corner[3] == 128
    assert all(abs(c - 19) <= 2 for c in corner[:3]), corner


def test_background_dust_is_not_recovered(monkeypatch):
    """A grain of the textured background goes away with the model, without recovery.

    The fake model restores everything cropped for it: if the grain were
    recovered, it would come back. It must not even be offered to the model.
    """
    import numpy as np

    image = opaque()
    # Far from both drawings, beyond the automatic grouping gap.
    ImageDraw.Draw(image).rectangle([70, 65, 74, 69], fill=(200, 30, 30))

    def keeps_the_drawings(pil):
        arr = np.array(pil.convert("RGBA"), dtype=np.uint8)
        alpha = np.zeros(arr.shape[:2], np.uint8)
        if pil.size == (160, 80):
            alpha[10:51, 10:51] = 200     # square A
            alpha[15:61, 100:146] = 200   # ellipse B, grain excluded
        else:
            alpha[:] = 200                # a recovery pass would return everything
        arr[..., 3] = alpha
        return Image.fromarray(arr, "RGBA")

    monkeypatch.setattr("gamestudio.vision.detect.remove_background", keeps_the_drawings)
    assert len(split_bitmap(image, matting=True).pieces) == 2
    assert len(split_bitmap(image, matting=False).pieces) == 3, \
        "without the model, the grain gets through color matting"


# ---------------------------------------------------------------------- import


@pytest.fixture()
def ctx(tmp_path: Path) -> Context:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    settings.ensure_dirs()
    return Context(project="icons", settings=settings, db=Database(settings.db_path),
                   store=AssetStore(settings.assets_dir))


def test_the_import_files_frames_in_their_sheet(ctx: Context, tmp_path: Path):
    source = as_file(grid_sheet(), tmp_path, "walk.png")
    result = import_sheet(source, ctx, multi=True)

    assert len(result.assets) == 8
    assert {asset.kind for asset in result.assets} == {"image"}
    meta = result.assets[0].meta
    assert (meta["role"], meta["sheet"], meta["strategy"]) == ("icon", "walk", "grid")

    librarian = Librarian(ctx.db, ctx.store, ctx.settings.library_dir)
    librarian.sync_project("icons")
    folder = librarian.project_dir("icons") / "icons" / "walk"
    assert len(list(folder.glob("*.png"))) == 8
    assert (folder / "sheet.json").exists()


def test_an_import_without_multi_keeps_the_sheet(ctx: Context, tmp_path: Path):
    source = as_file(pack(), tmp_path, "pack.png")
    result = import_sheet(source, ctx, multi=False)
    assert len(result.assets) == 1
    assert result.assets[0].meta["role"] == "sheet"
    assert result.assets[0].path.read_bytes() == source.read_bytes()


def test_dispatch_picks_the_split_by_file(ctx: Context, tmp_path: Path):
    """The same entry point handles an SVG and an image."""
    image = as_file(pack(), tmp_path, "pack.png")
    vector = tmp_path / "pack.svg"
    vector.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20">'
        '<rect x="2" y="2" width="10" height="10"/>'
        '<rect x="26" y="2" width="10" height="10"/></svg>', encoding="utf-8")

    assert split_sheet(image).pieces[0].suffix == ".png"
    assert split_sheet(vector).pieces[0].suffix == ".svg"
    with pytest.raises(SheetError):
        split_sheet(tmp_path / "sheet.txt")
