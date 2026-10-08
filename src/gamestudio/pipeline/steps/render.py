"""Rendering a 3D mesh into sprite sheets, one per animation and per direction.

The heavy work happens in Blender (`bl_render_ortho.py`); here live the
decisions that must stay testable: what is computed once for all frames, and
how sheets are named.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ...blender.run import run_script
from ...domain.gltf import LOOP_SUFFIXES, godot_animation, inspect_rig
from ...sprites import (
    build_sheets,
    common_crop,
    downsample,
    quantize_frames,
    write_atlas_metadata,
)
from ..base import Context, Step, StepResult


class RenderSprites(Step):
    """Render a 3D mesh into multi-direction sprite sheets.

    Three styles, which differ by more than an engine setting:

    - `normal`: flat light, exact texture colors. The fastest, and the right
      choice when the art direction already comes from the 2D image.
    - `prerender`: lit EEVEE, shadows and occlusion, rendered at 2x then
      downscaled. Light is baked into the sprite -- classic pre-rendering.
    - `pixel`: rendered without antialiasing at the target size, then a single
      palette for every frame and every direction.

    A GLB carries every animation of its entity (the ones the agent put in it):
    `animations` picks those to render, empty means all. A mesh without
    animation gives a single frame per direction, under the name `label`: a
    turnaround, which is what an object or a set piece needs.

    An animation loops if its name says so (`_loop`, as in Godot); `loop` only
    sets the turnaround, which has no animation name.

    Cropping and palette are computed **once for all animations**: going from
    idle to walk must neither move the pivot nor change the colors.
    """

    name = "render_sprites"

    STYLES = ("normal", "prerender", "pixel")

    def __init__(self, mesh_asset_id: str, label: str, *,
                 animations: list[str] | None = None, directions: int = 8,
                 size: int = 128, elevation: float = 30.0, style: str = "normal",
                 palette: int = 32, max_frames: int = 0, fps: int = 12,
                 loop: bool = True) -> None:
        if style not in self.STYLES:
            raise ValueError(f"unknown render style: {style} (expected {', '.join(self.STYLES)})")
        self.mesh_asset_id = mesh_asset_id
        self.label = label
        self.animations = list(animations or [])
        self.directions = directions
        self.size = size
        self.elevation = elevation
        self.style = style
        self.palette = palette
        self.max_frames = max_frames
        self.fps = fps
        self.loop = loop

    def inputs(self) -> dict[str, Any]:
        return {"mesh": self.mesh_asset_id, "label": self.label,
                "animations": self.animations,
                "directions": self.directions, "size": self.size,
                "elevation": self.elevation, "style": self.style,
                "palette": self.palette if self.style == "pixel" else 0,
                "max_frames": self.max_frames,
                # The atlas writes them: another setting is another result.
                "fps": self.fps, "loop": self.loop}

    def run(self, ctx: Context) -> StepResult:
        source = ctx.store.path_for(self.mesh_asset_id)
        if source is None:
            raise FileNotFoundError(f"mesh {self.mesh_asset_id} not found")

        frames_dir = ctx.work_dir / f"frames_{self.label}_{self.mesh_asset_id[:8]}"
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
        # Sheets follow the order the file declares: Blender stores its NLA
        # tracks in reverse, and the idle sheet must stay first if the agent
        # put it first.
        animations = self.animations
        if not animations and source.suffix.lower() in (".glb", ".gltf"):
            animations = inspect_rig(source).animation_names

        result = run_script(
            "bl_render_ortho.py",
            {"input": str(source), "output_dir": str(frames_dir),
             "directions": self.directions, "size": self.size,
             "elevation": self.elevation, "style": self.style,
             "clip": self.label, "animations": animations,
             "cycle_suffixes": list(LOOP_SUFFIXES), "max_frames": self.max_frames},
            blender_bin=ctx.settings.blender_bin, timeout=3600.0,
        )

        # Post-processing covers all frames at once: downscaling or quantizing
        # one animation at a time would make colors and edges drift from one
        # clip to the next.
        files = result["files"]
        rendered = [Path(entry["path"]) for entry in files]
        downsample(rendered, int(result.get("supersample", 1)))
        colors = 0
        if self.style == "pixel" and self.palette:
            colors = quantize_frames(rendered, self.palette)
        crop = common_crop(rendered)

        # Sheets carry the name Godot gives the animation: `walk_loop` in
        # Blender is `walk` in the AnimationPlayer, and in the sprites.
        sheets_dir = ctx.work_dir / "sheets"
        raw = list(dict.fromkeys(entry["clip"] for entry in files))
        named = {clip: godot_animation(clip) for clip in raw}
        clips = [named[clip][0] for clip in raw]
        sheets = []
        for clip in raw:
            made, _ = build_sheets([entry for entry in files if entry["clip"] == clip],
                                   sheets_dir, clip=named[clip][0], shared_crop=crop)
            sheets.extend(made)
        # An animation loops if its name says so, as in Godot; a turnaround
        # without animation follows the caller's setting.
        looping = {named[clip][0]: named[clip][1] if animations else self.loop
                   for clip in raw}
        metadata = write_atlas_metadata(
            sheets, sheets_dir / f"{self.label}.atlas.json", fps=self.fps, looping=looping)

        assets = [
            ctx.store.put_file(sheet.path, kind="spritesheet",
                               meta={"clip": sheet.clip, "direction": sheet.direction,
                                     "loop": looping[sheet.clip],
                                     "frames": sheet.frame_count,
                                     "frame_width": sheet.frame_width,
                                     "frame_height": sheet.frame_height,
                                     "pivot": list(sheet.pivot)})
            for sheet in sheets
        ]
        assets.append(ctx.store.put_file(metadata, kind="data",
                                         meta={"clip": self.label, "role": "atlas"}))

        shutil.rmtree(frames_dir, ignore_errors=True)
        return StepResult(
            data={"clips": clips, "directions": self.directions,
                  "frames": {entry["name"]: entry["frame_count"]
                             for entry in result.get("animations", [])},
                  "crop": list(crop) if crop else None,
                  "frame_size": [sheets[0].frame_width, sheets[0].frame_height] if sheets else None,
                  "pivot": list(sheets[0].pivot) if sheets else None,
                  "style": self.style, "palette": colors,
                  "animated": bool(result.get("animated")),
                  "engine": result.get("engine")},
            assets=assets,
        )
