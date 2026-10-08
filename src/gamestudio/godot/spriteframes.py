"""Generation of a Godot SpriteFrames resource from sprite sheets.

The ortho render produces one sheet per (clip, direction). It is split into
AtlasTextures -- one per frame -- rather than separate files: a single texture
in memory, a single draw call per character.

Animations are named `<clip>_<direction>` (walk_e, walk_ne...), the convention
an AnimatedSprite2D driven by the movement angle expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .tscn import RawValue, ResourceWriter

# Direction names for 4, 8 and 16 views, counterclockwise from east.
DIRECTION_NAMES: dict[int, tuple[str, ...]] = {
    1: ("",),
    2: ("e", "w"),
    4: ("e", "n", "w", "s"),
    8: ("e", "ne", "n", "nw", "w", "sw", "s", "se"),
    16: ("e", "ene", "ne", "nne", "n", "nnw", "nw", "wnw",
         "w", "wsw", "sw", "ssw", "s", "sse", "se", "ese"),
}


def direction_names(count: int) -> tuple[str, ...]:
    if count not in DIRECTION_NAMES:
        raise ValueError(f"{count} directions not supported (expected: {sorted(DIRECTION_NAMES)})")
    return DIRECTION_NAMES[count]


@dataclass
class SheetSpec:
    """A sheet: one clip, one direction, N frames in a row."""

    clip: str
    direction: str
    texture_path: str      # res:// path
    frame_count: int
    frame_width: int
    frame_height: int
    fps: int = 12
    loop: bool = True
    columns: int = 0       # 0 = every frame on one row

    @property
    def animation_name(self) -> str:
        return f"{self.clip}_{self.direction}" if self.direction else self.clip


def build_spriteframes(sheets: list[SheetSpec], *, resource_key: str = "spriteframes"
                       ) -> ResourceWriter:
    """Assemble the SpriteFrames resource."""
    writer = ResourceWriter("SpriteFrames", resource_key=resource_key)
    animations: list[dict[str, object]] = []

    for sheet in sheets:
        texture = writer.ext_resource("Texture2D", sheet.texture_path,
                                      key=sheet.animation_name)
        columns = sheet.columns or sheet.frame_count
        frames: list[dict[str, object]] = []
        for index in range(sheet.frame_count):
            col, row = index % columns, index // columns
            atlas = writer.sub_resource(
                "AtlasTexture",
                {
                    "atlas": texture,
                    "region": RawValue(
                        f"Rect2({col * sheet.frame_width}, {row * sheet.frame_height}, "
                        f"{sheet.frame_width}, {sheet.frame_height})"
                    ),
                },
                key=f"{sheet.animation_name}:{index}",
            )
            frames.append({"duration": 1.0, "texture": atlas})

        animations.append({
            "frames": frames,
            "loop": sheet.loop,
            "name": RawValue(f'&"{sheet.animation_name}"'),
            "speed": float(sheet.fps),
        })

    writer.properties["animations"] = animations
    return writer


def write_spriteframes(sheets: list[SheetSpec], path: Path, *, resource_key: str = ""
                       ) -> Path:
    return build_spriteframes(sheets, resource_key=resource_key or path.stem).write(path)
