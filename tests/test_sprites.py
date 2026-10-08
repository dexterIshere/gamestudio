"""Post-processing of renders: shared palette and downscaling.

The rule tested here is the one that makes a sprite usable rather than
jittery: what is computed once for the whole character -- the crop, the
palette -- must never be computed frame by frame.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from gamestudio.sprites import downsample, quantize_frames


def _frame(path: Path, *, hue: int, size: int = 48) -> Path:
    """A frame with a clean gradient: many neighboring colors."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for y in range(8, size - 8):
        draw.line([(8, y), (size - 8, y)],
                  fill=(200 - y, 80 + y // 2, 40 + hue, 255))
    # A half-transparent edge, as antialiasing produces.
    draw.line([(8, 6), (size - 8, 6)], fill=(255, 255, 255, 90))
    image.save(path)
    return path


@pytest.fixture()
def frames(tmp_path: Path) -> list[Path]:
    return [_frame(tmp_path / f"f{i}.png", hue=i * 30) for i in range(3)]


def _colors(path: Path) -> set[tuple[int, ...]]:
    with Image.open(path) as image:
        pixels = np.array(image.convert("RGBA"))
    return {tuple(px) for px in pixels[pixels[..., 3] > 128][:, :3]}


def test_the_palette_is_shared_by_all_frames(frames):
    """Otherwise colors would jump from one direction to the next."""
    before = set().union(*(_colors(path) for path in frames))
    entries = quantize_frames(frames, colors=12)

    after = [_colors(path) for path in frames]
    shared = set().union(*after)
    assert len(shared) <= 12 < len(before)
    for colors in after:
        assert colors <= shared, "a frame uses a color outside the palette"
    assert 0 < entries <= 12


def test_quantization_binarizes_alpha(frames):
    """A half-transparent edge is blur: pixel art does not want it."""
    with Image.open(frames[0]) as image:
        before = set(np.unique(np.array(image.convert("RGBA"))[..., 3]).tolist())
    assert before - {0, 255}, "the starting frame must have soft edges"

    quantize_frames(frames, colors=12)
    for path in frames:
        with Image.open(path) as image:
            alphas = set(np.unique(np.array(image.convert("RGBA"))[..., 3]).tolist())
        assert alphas <= {0, 255}


def test_a_fully_transparent_sheet_breaks_nothing(tmp_path):
    blank = tmp_path / "blank.png"
    Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(blank)
    assert quantize_frames([blank], colors=8) == 0


def test_without_frames_quantization_does_nothing():
    assert quantize_frames([], colors=8) == 0


def test_downscaling_divides_the_size(tmp_path):
    path = tmp_path / "large.png"
    Image.new("RGBA", (128, 96), (200, 40, 40, 255)).save(path)
    downsample([path], 2)
    with Image.open(path) as image:
        assert image.size == (64, 48)


def test_a_factor_of_one_leaves_the_image_untouched(tmp_path):
    """The pixel style does not supersample: downscaling must do nothing."""
    path = tmp_path / "pixel.png"
    Image.new("RGBA", (32, 32), (10, 20, 30, 255)).save(path)
    downsample([path], 1)
    with Image.open(path) as image:
        assert image.size == (32, 32)


def test_the_atlas_states_each_sheets_looping(tmp_path):
    """Idle loops, attack does not: a single flag would make both loop."""
    import json

    from gamestudio.sprites import SheetResult, write_atlas_metadata

    sheets = [SheetResult(path=tmp_path / f"{clip}_d00.png", clip=clip, direction=0,
                          frame_count=4, frame_width=32, frame_height=48, columns=4)
              for clip in ("idle", "attack")]
    atlas = write_atlas_metadata(sheets, tmp_path / "golem.atlas.json", fps=12,
                                 looping={"idle": True, "attack": False})

    payload = json.loads(atlas.read_text(encoding="utf-8"))
    assert "loop" not in payload
    assert {sheet["clip"]: sheet["loop"] for sheet in payload["sheets"]} == \
        {"idle": True, "attack": False}
