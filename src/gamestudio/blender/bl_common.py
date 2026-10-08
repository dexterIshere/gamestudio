# SPDX-License-Identifier: GPL-3.0-or-later
"""Utilities shared by the scripts that run INSIDE Blender.

This module is imported by Blender's interpreter, never by the studio's venv:
it must depend only on `bpy` and the standard library.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

import bpy

RESULT_BEGIN = "###GAMESTUDIO_RESULT_BEGIN###"
RESULT_END = "###GAMESTUDIO_RESULT_END###"


def parse_args() -> dict[str, Any]:
    """Read the JSON arguments passed after the command line's `--`."""
    argv = sys.argv
    if "--" not in argv:
        return {}
    tail = argv[argv.index("--") + 1:]
    if "--args" in tail:
        return json.loads(tail[tail.index("--args") + 1])
    return {}


def emit(result: dict[str, Any]) -> None:
    print(RESULT_BEGIN)
    print(json.dumps(result, default=str))
    print(RESULT_END)
    sys.stdout.flush()


def main(handler) -> None:
    """Standard wrapper: parse, run, emit the result or the error."""
    try:
        emit(handler(parse_args()) or {})
    except Exception as exc:  # noqa: BLE001 -- the error must reach the studio
        emit({"error": f"{type(exc).__name__}: {exc}",
              "traceback": traceback.format_exc()})
        sys.exit(1)


def reset_scene() -> None:
    """Empty the scene and the orphan data completely.

    `read_factory_settings(use_empty=True)` is not enough: depending on the
    Blender version, objects of the startup scene remain (a cube, an
    icosphere...). They then end up in the exported .glb and silently skew
    every bounding-box measurement. So they are removed explicitly.
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials,
                       bpy.data.actions, bpy.data.images, bpy.data.cameras,
                       bpy.data.lights):
        for item in list(collection):
            if item.users == 0:
                collection.remove(item)


def import_model(path: str) -> list[bpy.types.Object]:
    """Import a .glb/.gltf/.fbx/.obj and return the objects created."""
    before = set(bpy.data.objects)
    lower = path.lower()
    if lower.endswith((".glb", ".gltf")):
        bpy.ops.import_scene.gltf(filepath=path)
    elif lower.endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path)
    elif lower.endswith(".obj"):
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise ValueError(f"unsupported format: {path}")
    return [obj for obj in bpy.data.objects if obj not in before]


def find_armature(objects: list[bpy.types.Object] | None = None) -> bpy.types.Object | None:
    pool = objects if objects is not None else list(bpy.data.objects)
    return next((obj for obj in pool if obj.type == "ARMATURE"), None)


def find_meshes(objects: list[bpy.types.Object] | None = None) -> list[bpy.types.Object]:
    pool = objects if objects is not None else list(bpy.data.objects)
    return [obj for obj in pool if obj.type == "MESH"]


def character_meshes(
    objects: list[bpy.types.Object], armature: bpy.types.Object | None
) -> tuple[list[bpy.types.Object], list[str]]:
    """Separate the character's meshes from stray objects.

    Blender 5.2's glTF importer adds a ghost object (an `Icosphere`) that is not
    in the file. Unfiltered, it skews every bounding-box measurement and ends
    up in the export. The criterion -- being linked to the armature, through
    parenting, a modifier or vertex groups -- also covers the legitimate case
    of a character made of several meshes (body, weapon, hair).

    Returns (kept meshes, dropped names): rejects are reported to the studio
    rather than silently deleted.
    """
    meshes = find_meshes(objects)
    if armature is None:
        return meshes, []

    def linked(mesh: bpy.types.Object) -> bool:
        parent = mesh.parent
        while parent is not None:
            if parent == armature:
                return True
            parent = parent.parent
        for modifier in mesh.modifiers:
            if modifier.type == "ARMATURE" and modifier.object == armature:
                return True
        bone_names = {bone.name for bone in armature.data.bones}
        return any(group.name in bone_names for group in mesh.vertex_groups)

    kept = [mesh for mesh in meshes if linked(mesh)]
    dropped = [mesh.name for mesh in meshes if mesh not in kept]
    return (kept or meshes), dropped


def purge_objects(objects: list[bpy.types.Object]) -> None:
    for obj in objects:
        try:
            if obj.name in bpy.data.objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        except ReferenceError:
            continue


def live_objects(objects: list[bpy.types.Object]) -> list[bpy.types.Object]:
    """Filter out the references that became invalid after a deletion.

    Touching an attribute of a deleted object raises `ReferenceError`: keeping a
    list built before a purge is a classic trap of the Blender API.
    """
    alive = []
    for obj in objects:
        try:
            _ = obj.name
        except ReferenceError:
            continue
        if obj.name in bpy.data.objects:
            alive.append(obj)
    return alive


def scene_bounds(objects: list[bpy.types.Object]) -> tuple[tuple[float, float, float],
                                                           tuple[float, float, float]]:
    """World bounding box of the given objects."""
    from mathutils import Vector

    lo = Vector((float("inf"),) * 3)
    hi = Vector((float("-inf"),) * 3)
    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            continue
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                lo[axis] = min(lo[axis], world[axis])
                hi[axis] = max(hi[axis], world[axis])
    if lo.x == float("inf"):
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return tuple(lo), tuple(hi)


def deselect_all() -> None:
    for obj in bpy.data.objects:
        obj.select_set(False)


def activate(obj: bpy.types.Object) -> None:
    deselect_all()
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
