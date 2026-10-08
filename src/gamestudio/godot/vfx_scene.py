"""Godot scenes of an effect: a flipbook, and optionally an emitter that uses it.

An effect rendered by the studio is a sheet of frames. Godot consumes it in two
ways, and the studio writes both native scenes:

- `<effect>.tscn`: an `AnimatedSprite2D` that plays the sheet (a cast spell, an
  impact, a standing flame) -- the scene's pivot is the effect's (0, 0) point,
  not the frame's corner;
- `<effect>_particles.tscn` (if the spec declares `godot.particles`): a
  `GPUParticles2D` whose every particle plays the sheet over its life (embers,
  wisps of smoke, animated drops).

No script is attached: what triggers the effect, what owns it and what frees
it belong to the game and to the effect's contract (skill `vfx`), never to the
generated scene -- it is rewritten on every render.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .spriteframes import SheetSpec, write_spriteframes
from .tscn import RawValue, SceneWriter

# CanvasItemMaterial.blend_mode: 0 mix, 1 add, 2 sub, 3 mul, 4 premult_alpha.
BLEND_MODES = {"mix": 0, "add": 1, "screen": 1}


@dataclass
class EffectExport:
    scene: Path
    spriteframes: Path
    texture: Path
    particles: Path | None
    res_scene: str
    res_particles: str


def _node_name(name: str) -> str:
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_")) or "Effect"


def _vec3(value: Any, default: tuple[float, float]) -> RawValue:
    x, y = value if isinstance(value, (list, tuple)) and len(value) == 2 else default
    return RawValue(f"Vector3({float(x):g}, {float(y):g}, 0)")


def _pair(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return float(value[0]), float(value[1])
    if isinstance(value, (int, float)):
        return float(value), float(value)
    return default


def write_effect(project_root: Path, folder: Path, name: str, sheet_png: bytes,
                 meta: dict[str, Any], godot: dict[str, Any]) -> EffectExport:
    """Write the sheet, the SpriteFrames resource and the scene(s)."""
    folder.mkdir(parents=True, exist_ok=True)
    texture = folder / f"{name}.png"
    texture.write_bytes(sheet_png)

    def res(path: Path) -> str:
        return "res://" + path.relative_to(project_root).as_posix()

    frames_path = write_spriteframes(
        [SheetSpec(clip=name, direction="", texture_path=res(texture),
                   frame_count=meta["frames"], frame_width=meta["frame_width"],
                   frame_height=meta["frame_height"], fps=int(round(meta["fps"])),
                   loop=bool(meta["loop"]), columns=meta["columns"])],
        folder / f"{name}.tres")

    blend = BLEND_MODES.get(str(meta.get("blend", "mix")), 0)
    ox, oy = meta["origin"]
    width, height = meta["frame_width"], meta["frame_height"]

    scene = SceneWriter(_node_name(name), "Node2D", scene_key=name)
    sprite_frames = scene.ext_resource("SpriteFrames", res(frames_path))
    properties: dict[str, Any] = {
        "sprite_frames": sprite_frames,
        "animation": RawValue(f'&"{name}"'),
        "autoplay": name,
        # The scene's pivot is the effect's origin, not the frame's center.
        "offset": RawValue(f"Vector2({width / 2 - ox:g}, {height / 2 - oy:g})"),
    }
    if blend:
        properties["material"] = scene.sub_resource("CanvasItemMaterial", {"blend_mode": blend},
                                                    key="material")
    scene.node("Flipbook", "AnimatedSprite2D", ".", **properties)
    scene_path = scene.write(folder / f"{name}.tscn")

    particles_path = None
    options = godot.get("particles")
    if isinstance(options, dict):
        particles_path = folder / f"{name}_particles.tscn"
        emitter = SceneWriter(_node_name(name) + "Particles", "Node2D",
                              scene_key=f"{name}_particles")
        tex = emitter.ext_resource("Texture2D", res(texture))
        material = emitter.sub_resource("CanvasItemMaterial", {
            "blend_mode": blend,
            "particles_animation": True,
            "particles_anim_h_frames": meta["columns"],
            "particles_anim_v_frames": meta["rows"],
            "particles_anim_loop": bool(meta["loop"]),
        }, key="material")
        velocity = _pair(options.get("velocity"), (0.0, 0.0))
        scale = _pair(options.get("scale"), (1.0, 1.0))
        process = emitter.sub_resource("ParticleProcessMaterial", {
            "direction": _vec3(options.get("direction"), (0.0, -1.0)),
            "spread": float(options.get("spread", 45.0)),
            "initial_velocity_min": velocity[0],
            "initial_velocity_max": velocity[1],
            "gravity": _vec3(options.get("gravity"), (0.0, 0.0)),
            "scale_min": scale[0],
            "scale_max": scale[1],
            # An animation speed of 1: the whole sheet over the particle's life.
            "anim_speed_min": 1.0,
            "anim_speed_max": 1.0,
        }, key="process")
        emitter.node("Emitter", "GPUParticles2D", ".",
                     amount=int(options.get("amount", 8)),
                     lifetime=float(options.get("lifetime", meta["duration"])),
                     one_shot=bool(options.get("one_shot", False)),
                     explosiveness=float(options.get("explosiveness", 0.0)),
                     texture=tex, material=material, process_material=process)
        emitter.write(particles_path)

    return EffectExport(scene=scene_path, spriteframes=frames_path, texture=texture,
                        particles=particles_path, res_scene=res(scene_path),
                        res_particles=res(particles_path) if particles_path else "")
