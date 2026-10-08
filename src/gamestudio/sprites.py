"""Assembling sprite sheets from Blender renders.

The trap of sprite packing is cropping: cropping each frame to its own content
makes the character jitter, because its pivot moves from one frame to the next.
So **a single** crop box is computed, the union of all frames of a character,
and applied everywhere -- across every direction and every clip.

Palette reduction follows the same rule, for the same reason: one palette per
frame would change the colors from one direction to the next. It is computed
once over all frames, then applied to all of them.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

Box = tuple[int, int, int, int]


@dataclass
class SheetResult:
    path: Path
    clip: str
    direction: int
    frame_count: int
    frame_width: int
    frame_height: int
    columns: int
    pivot: tuple[float, float] = (0.5, 1.0)


# Beyond this, the adaptive palette gains no accuracy and costs time.
PALETTE_SAMPLE_LIMIT = 200_000


def downsample(paths: list[Path], factor: int) -> None:
    """Shrink supersampled renders to their target size, in place.

    Rendering at 2x then downscaling gives much cleaner edges than the engine's
    antialiasing: that is what separates a crisp pre-rendered sprite from one
    with smeared outlines. Irrelevant for pixel art, which is not supersampled.
    """
    if factor <= 1:
        return
    for path in paths:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            target = (max(1, rgba.width // factor), max(1, rgba.height // factor))
            rgba.resize(target, Image.LANCZOS).save(path)


def quantize_frames(paths: list[Path], colors: int = 32, *,
                    alpha_threshold: int = 128) -> int:
    """Bring every frame down to a single palette. Return its actual size.

    One palette for the whole set: the condition for the character to keep
    exactly the same colors from every angle and on every frame. Alpha is
    binarized along the way -- a half-transparent edge is blur, and pixel art
    does not want it.
    """
    if not paths:
        return 0

    # The palette is computed on opaque pixels only: including the transparent
    # background would waste an entry on a color never shown.
    opaque: list[np.ndarray] = []
    for path in paths:
        with Image.open(path) as image:
            frame = np.array(image.convert("RGBA"))
        opaque.append(frame[frame[..., 3] >= alpha_threshold][:, :3])
    pixels = np.concatenate(opaque) if opaque else np.empty((0, 3), dtype=np.uint8)
    if not len(pixels):
        return 0

    step = max(1, len(pixels) // PALETTE_SAMPLE_LIMIT)
    sample = pixels[::step]
    # Pillow computes an adaptive palette on an image: the sampled pixels are
    # folded into a square, whose shape does not matter here.
    side = max(1, math.ceil(math.sqrt(len(sample))))
    padded = np.resize(sample, (side * side, 3))
    reference = Image.fromarray(padded.reshape(side, side, 3), "RGB")
    palette = reference.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors)

    for path in paths:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
        mask = rgba.getchannel("A").point(
            lambda value: 255 if value >= alpha_threshold else 0)
        # `dither=NONE`: dithering fakes shades missing from the palette by
        # mixing two neighboring colors, which produces exactly the noise pixel
        # art avoids.
        flattened = rgba.convert("RGB").quantize(
            palette=palette, dither=Image.Dither.NONE).convert("RGBA")
        flattened.putalpha(mask)
        flattened.save(path)

    return len(palette.getcolors() or [])


def content_box(image: Image.Image, *, alpha_threshold: int = 8) -> Box | None:
    """Bounding box of the non-transparent pixels."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    alpha = image.getchannel("A")
    return alpha.point(lambda v: 255 if v > alpha_threshold else 0).getbbox()


def union_box(boxes: list[Box | None]) -> Box | None:
    present = [b for b in boxes if b is not None]
    if not present:
        return None
    return (
        min(b[0] for b in present), min(b[1] for b in present),
        max(b[2] for b in present), max(b[3] for b in present),
    )


def padded_box(box: Box, size: tuple[int, int], padding: int = 2) -> Box:
    """Widen the box, staying inside the image.

    The margin keeps an engine's bilinear filtering from sampling pixels of the
    neighboring frame in the atlas.
    """
    width, height = size
    return (
        max(0, box[0] - padding), max(0, box[1] - padding),
        min(width, box[2] + padding), min(height, box[3] + padding),
    )


def common_crop(paths: list[Path], *, padding: int = 2) -> Box | None:
    """Crop box shared by all the given images."""
    boxes: list[Box | None] = []
    size = (0, 0)
    for path in paths:
        with Image.open(path) as image:
            size = image.size
            boxes.append(content_box(image))
    box = union_box(boxes)
    return padded_box(box, size, padding) if box else None


def pack_sheet(
    paths: list[Path],
    output: Path,
    *,
    crop: Box | None = None,
    columns: int = 0,
    max_width: int = 4096,
) -> tuple[Path, int, int, int]:
    """Assemble the images into a sheet. Return (path, width, height, columns)."""
    if not paths:
        raise ValueError("no image to assemble")

    frames: list[Image.Image] = []
    for path in paths:
        with Image.open(path) as image:
            frame = image.convert("RGBA")
            frames.append(frame.crop(crop) if crop else frame.copy())

    frame_width, frame_height = frames[0].size
    if columns <= 0:
        # A single row as long as the sheet fits the maximum width.
        columns = min(len(frames), max(1, max_width // max(frame_width, 1)))
    rows = math.ceil(len(frames) / columns)

    sheet = Image.new("RGBA", (columns * frame_width, rows * frame_height), (0, 0, 0, 0))
    for index, frame in enumerate(frames):
        col, row = index % columns, index // columns
        sheet.paste(frame, (col * frame_width, row * frame_height))

    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "PNG", optimize=True)
    for frame in frames:
        frame.close()
    return output, frame_width, frame_height, columns


def pivot_in_frame(crop: Box, source_size: tuple[int, int]) -> tuple[float, float]:
    """Position of the pivot (ground center) in the cropped frame, as a fraction.

    The render places the character centered horizontally, feet toward the
    bottom of the frame. After cropping, the engine needs to know where that
    point is to put the sprite on the ground without making it float.
    """
    width, height = source_size
    x = (width / 2.0 - crop[0]) / max(crop[2] - crop[0], 1)
    y = (height - crop[1]) / max(crop[3] - crop[1], 1)
    return (round(x, 5), round(min(y, 1.0), 5))


def build_sheets(
    render_files: list[dict],
    output_dir: Path,
    *,
    clip: str,
    padding: int = 2,
    shared_crop: Box | None = None,
) -> tuple[list[SheetResult], Box | None]:
    """Group the renders by direction and produce one sheet per direction."""
    by_direction: dict[int, list[Path]] = {}
    for entry in render_files:
        by_direction.setdefault(int(entry["direction"]), []).append(Path(entry["path"]))
    for paths in by_direction.values():
        paths.sort()

    all_paths = [p for paths in by_direction.values() for p in paths]
    crop = shared_crop or common_crop(all_paths, padding=padding)

    source_size = (0, 0)
    if all_paths:
        with Image.open(all_paths[0]) as probe:
            source_size = probe.size

    results: list[SheetResult] = []
    for direction, paths in sorted(by_direction.items()):
        target = output_dir / f"{clip}_d{direction:02d}.png"
        path, frame_width, frame_height, columns = pack_sheet(
            paths, target, crop=crop, columns=len(paths)
        )
        results.append(SheetResult(
            path=path, clip=clip, direction=direction, frame_count=len(paths),
            frame_width=frame_width, frame_height=frame_height, columns=columns,
            pivot=pivot_in_frame(crop, source_size) if crop else (0.5, 1.0),
        ))
    return results, crop


def write_atlas_metadata(sheets: list[SheetResult], path: Path, *, fps: int,
                         looping: dict[str, bool]) -> Path:
    """Companion file describing the sheets, for the engine-side import.

    Looping is set per sheet, from its animation (`looping`, by clip name): an
    idle loops, an attack does not. A single flag for the whole atlas would
    make the attack loop along with the idle.
    """
    payload = {
        "version": 2,
        "fps": fps,
        "sheets": [
            {
                "file": sheet.path.name,
                "clip": sheet.clip,
                "loop": looping[sheet.clip],
                "direction": sheet.direction,
                "frames": sheet.frame_count,
                "frame_width": sheet.frame_width,
                "frame_height": sheet.frame_height,
                "columns": sheet.columns,
                "pivot": list(sheet.pivot),
            }
            for sheet in sheets
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
