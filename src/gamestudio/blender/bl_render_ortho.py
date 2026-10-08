# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-direction orthographic render of an animated 3D character.

This is the bridge between 3D and 2D: the character is rendered from N angles,
on every frame of each of its animations, as transparent PNGs. The resulting
frames are then assembled into sprite sheets, one per animation and per
direction.

Two invariants make the difference between a usable sprite and a jittery one:

* the camera is **fixed** -- same framing for every frame and every
  direction. The character turns, not the camera, and the ortho scale is
  computed once;
* the render is in whole pixels and centered on the same point, so that the
  ground pivot stays in place from one frame to the next.

Three render styles, which differ by engine as much as by light:

* `normal`   -- Workbench under flat light: texture colors pass through
                unchanged. What you want when the style already comes from the
                generated image, and the fastest.
* `prerender`-- EEVEE, three-point lighting, shadows and occlusion. Light is
                baked into the sprite: classic pre-rendering.
* `pixel`    -- Workbench without any antialiasing, rendered directly at the
                target size. Antialiasing is the enemy of pixel art: it invents
                in-between colors that read as blur. Palette reduction happens
                downstream, on all frames at once.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy
from mathutils import Euler, Vector

from bl_common import (character_meshes, find_armature, find_meshes, import_model,
                       live_objects, main, purge_objects, reset_scene, scene_bounds)


# Legacy shading names, still found in recipes or caches.
STYLE_ALIASES = {"flat": "normal", "lit": "prerender", "": "normal"}

# EEVEE needs samples for clean shadows; Workbench ignores them, but the value
# still drives its antialiasing.
DEFAULT_SAMPLES = {"normal": 8, "pixel": 1, "prerender": 64}


def animation_names(objects: list) -> list[str]:
    """The file's animations, in the order it declares them.

    Blender's glTF import turns each animation into an action, and puts on each
    object it animates -- an armature's bones or a building's part -- a muted
    NLA track of the same name. A file without tracks but with an active action
    (an old export, a converted `.blend`) has a single one.
    """
    names: list[str] = []
    for obj in objects:
        data = obj.animation_data
        if data is None:
            continue
        for track in data.nla_tracks:
            if track.strips and track.name not in names:
                names.append(track.name)
        if not data.nla_tracks and data.action is not None and data.action.name not in names:
            names.append(data.action.name)
    return names


def pose_animation(objects: list, name: str) -> tuple[int, int] | None:
    """Play animation `name` on each object it animates; return its frame range.

    Tracks stay muted and the action is set as active: a single animation is
    evaluated, the one being rendered. An object the animation does not touch
    returns to rest. Since Blender 4.4 an action carries one slot per object:
    one is picked among the suitable ones when the assignment did not.
    """
    ranges: list[tuple[float, float]] = []
    for obj in objects:
        data = obj.animation_data
        if data is None:
            continue
        chosen = None
        for track in data.nla_tracks:
            track.mute = True
            if track.name == name and track.strips:
                chosen = track.strips[0].action
        if chosen is None and not data.nla_tracks and data.action is not None \
                and data.action.name == name:
            chosen = data.action
        data.action = chosen
        if chosen is None:
            continue
        if getattr(data, "action_slot", True) is None:
            suitable = list(getattr(data, "action_suitable_slots", None) or [])
            if suitable:
                data.action_slot = suitable[0]
        ranges.append(tuple(chosen.frame_range))
    if not ranges:
        return None
    return (int(round(min(low for low, _ in ranges))),
            int(round(max(high for _, high in ranges))))


def sampled(start: int, end: int, *, step: int, limit: int) -> list[int]:
    """The frames to render: all of them, or `limit` evenly spaced.

    A walk in 8 evenly spaced frames beats a truncated walk.
    """
    frames = list(range(start, end + 1, max(step, 1)))
    if limit and len(frames) > limit:
        stride = len(frames) / limit
        frames = [frames[int(i * stride)] for i in range(limit)]
    return frames


def is_cycle(name: str, suffixes: list[str]) -> bool:
    """A cycle name (`walk_loop`). The rule is that of `domain/gltf.py`: the
    caller passes its suffixes, so that it is written only once."""
    lowered = name.lower()
    return any(lowered.endswith(suffix) and len(name) > len(suffix) for suffix in suffixes)


def setup_camera(center: Vector, ortho_scale: float, *, elevation_deg: float,
                 distance: float) -> bpy.types.Object:
    """Orthographic camera aimed at `center`, tilted by `elevation_deg`.

    An elevation of 0 gives a side view (platformer), 30 to 45 an RPG-style
    isometric view, 90 a top-down view.
    """
    camera_data = bpy.data.cameras.new("Camera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = ortho_scale
    camera = bpy.data.objects.new("Camera", camera_data)
    bpy.context.collection.objects.link(camera)

    elevation = math.radians(elevation_deg)
    # The camera stands south of the character (-Y), which therefore faces the lens.
    camera.location = center + Vector((
        0.0,
        -distance * math.cos(elevation),
        distance * math.sin(elevation),
    ))
    camera.rotation_euler = Euler((math.pi / 2 - elevation, 0.0, 0.0), "XYZ")
    bpy.context.scene.camera = camera
    return camera


def setup_lighting(center: Vector, style: str) -> None:
    """Lighting of the render, depending on the requested style.

    The flat styles (`normal`, `pixel`) disable shading to keep the texture's
    exact colors. `prerender` adds three-point lighting: that is what gives the
    volume baked into the sprite.
    """
    world = bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes["Background"]

    if style != "prerender":
        background.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        background.inputs[1].default_value = 1.0
        return

    background.inputs[0].default_value = (0.05, 0.06, 0.08, 1.0)
    background.inputs[1].default_value = 0.35

    for name, offset, energy, size in (
        ("Key", Vector((-2.5, -3.0, 3.5)), 700.0, 3.0),
        ("Fill", Vector((3.0, -2.0, 1.5)), 220.0, 4.0),
        ("Rim", Vector((0.0, 3.5, 2.5)), 400.0, 2.0),
    ):
        light_data = bpy.data.lights.new(name, type="AREA")
        light_data.energy = energy
        light_data.size = size
        light = bpy.data.objects.new(name, light_data)
        light.location = center + offset
        direction = (center - light.location).normalized()
        light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        bpy.context.collection.objects.link(light)


def configure_render(size: int, *, samples: int, style: str) -> None:
    scene = bpy.context.scene
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.compression = 15

    if style != "prerender":
        # Workbench in "flat texture" mode: fast and with no lighting
        # interpretation, the sprite keeps the material's exact colors.
        scene.render.engine = "BLENDER_WORKBENCH"
        shading_settings = scene.display.shading
        shading_settings.light = "FLAT"
        shading_settings.color_type = "TEXTURE"
        if style == "pixel":
            # No antialiasing and no dithering: each rendered pixel must be a
            # plain color of the material, not an average of two.
            scene.display.render_aa = "OFF"
            scene.render.dither_intensity = 0.0
        else:
            scene.display.render_aa = "FXAA" if samples <= 8 else "32"
        return

    # EEVEE depending on the version: the engine's name changed in 4.2.
    engines = {item.identifier for item in
               bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else (
        "BLENDER_EEVEE" if "BLENDER_EEVEE" in engines else "CYCLES")
    if scene.render.engine.startswith("BLENDER_EEVEE"):
        try:
            scene.eevee.taa_render_samples = samples
        except AttributeError:
            pass
        # Shadows and ambient occlusion: what sets a pre-render apart from a
        # merely lit render. These settings were renamed across EEVEE versions,
        # hence the silent attempts.
        for attribute, value in (("use_shadows", True), ("use_gtao", True),
                                 ("gtao_distance", 0.4), ("use_raytracing", True)):
            try:
                setattr(scene.eevee, attribute, value)
            except (AttributeError, TypeError):
                pass
    else:
        scene.cycles.samples = samples


def handler(args: dict) -> dict:
    source = args["input"]
    output_dir = args["output_dir"]
    directions = int(args.get("directions", 8))
    size = int(args.get("size", 128))
    elevation = float(args.get("elevation", 30.0))
    style = args.get("style") or STYLE_ALIASES.get(args.get("shading", ""), "normal")
    samples = int(args.get("samples", 0)) or DEFAULT_SAMPLES.get(style, 16)
    margin = float(args.get("margin", 1.12))
    clip = args.get("clip", "clip")
    wanted = [str(name) for name in args.get("animations") or []]
    cycles = [str(suffix).lower() for suffix in args.get("cycle_suffixes") or []]
    frame_step = int(args.get("frame_step", 1))
    max_frames = int(args.get("max_frames", 0))

    # Supersampling only makes sense where edges must be soft: in pixel art it
    # would destroy exactly what is wanted.
    supersample = int(args.get("supersample", 0)) or (2 if style == "prerender" else 1)
    render_size = size * supersample

    reset_scene()
    imported = import_model(source)
    armature = find_armature(imported)
    meshes, dropped = character_meshes(imported, armature)
    purge_objects([obj for obj in find_meshes(imported) if obj not in meshes])
    imported = live_objects(imported)
    if not meshes:
        raise RuntimeError("no mesh to render")

    low, high = scene_bounds(meshes)
    center = Vector(((low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0,
                     (low[2] + high[2]) / 2.0))
    # The ortho scale covers the largest horizontal or vertical dimension, so
    # that no direction frames the character differently.
    extent = max(high[2] - low[2], high[0] - low[0], high[1] - low[1])
    ortho_scale = max(extent * margin, 1e-3)
    distance = extent * 4.0 + 2.0

    setup_camera(center, ortho_scale, elevation_deg=elevation, distance=distance)
    setup_lighting(center, style)
    configure_render(render_size, samples=samples, style=style)

    scene = bpy.context.scene
    available = animation_names(imported)
    missing = [name for name in wanted if name not in available]
    if missing:
        raise RuntimeError(f"animation(s) missing from the file: {', '.join(missing)} "
                           f"(present: {', '.join(available) or 'none'})")
    # Each animation is rendered in turn, under its name. A mesh without
    # animation has a single pose: one frame per direction, under the name
    # `clip`, which gives a turnaround. Without this case, Blender's default
    # range -- 250 frames -- would apply and the render would never end.
    plan: list[tuple[str, list[int]]] = []
    for name in wanted or available:
        span = pose_animation(imported, name)
        if span is not None:
            start, end = span
            # A cycle is keyed with identical first and last poses, so that it
            # loops smoothly in Godot; on a sheet, that last frame would repeat
            # the first one where it wraps.
            if end > start and is_cycle(name, cycles):
                end -= 1
            plan.append((name, sampled(start, end, step=frame_step, limit=max_frames)))
    if not plan:
        pose_animation(imported, "")
        plan = [(clip, [scene.frame_start])]

    # The character turns rather than the camera: the framing stays strictly
    # identical from one direction to the next.
    pivot = bpy.data.objects.new("Pivot", None)
    bpy.context.collection.objects.link(pivot)
    pivot.location = (center.x, center.y, 0.0)
    for obj in imported:
        if obj.parent is None and obj.name in bpy.data.objects:
            obj.parent = pivot
            obj.matrix_parent_inverse = pivot.matrix_world.inverted()

    os.makedirs(output_dir, exist_ok=True)
    rendered: list[dict] = []
    for name, frames in plan:
        pose_animation(imported, name)
        for direction in range(directions):
            angle = 2.0 * math.pi * direction / directions
            pivot.rotation_euler = Euler((0.0, 0.0, angle), "XYZ")
            for index, frame in enumerate(frames):
                scene.frame_set(frame)
                path = os.path.join(output_dir,
                                    f"{name}_d{direction:02d}_f{index:03d}.png")
                scene.render.filepath = path
                bpy.ops.render.render(write_still=True)
                rendered.append({"clip": name, "direction": direction, "frame": index,
                                 "path": path})

    return {
        "output_dir": output_dir,
        "clip": clip,
        "animations": [{"name": name, "frame_count": len(frames)} for name, frames in plan],
        "directions": directions,
        "frame_count": max(len(frames) for _, frames in plan),
        "size": size,
        "render_size": render_size,
        "supersample": supersample,
        "style": style,
        "animated": any(len(frames) > 1 for _, frames in plan),
        "ortho_scale": ortho_scale,
        "elevation": elevation,
        "dropped_objects": dropped,
        "files": rendered,
        "engine": scene.render.engine,
    }


if __name__ == "__main__":
    main(handler)
