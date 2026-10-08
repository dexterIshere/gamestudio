# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate a rigged and animated test entity, exported as glTF.

Stands in for what the rigging and animating agent delivers (see the skill
`animation`): a GLB that carries its bones and all its animations, one NLA
track per animation. Used to check sprite rendering and the Godot import and
export without depending on Runware or an agent.

Two kinds, because a studio animates more than characters:

* `humanoid` -- canonical skeleton, weighted mesh, animations `idle_loop` (a
  cycle: Godot will loop it, under the name `idle`) and `wave`;
* `machine`  -- a base and a wheel, without any bone: the wheel spins in a
  loop (`spin_loop`), like a mill or a machine whose parts are animated.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy
from mathutils import Vector

from bl_common import activate, deselect_all, emit, main, reset_scene  # noqa: F401

# (canonical name, parent, head, tail) in meters, for a 1.8 m character.
SKELETON = [
    ("hips",        None,          (0.00, 0, 0.98), (0.00, 0, 1.12)),
    ("spine",       "hips",        (0.00, 0, 1.12), (0.00, 0, 1.28)),
    ("chest",       "spine",       (0.00, 0, 1.28), (0.00, 0, 1.45)),
    ("neck",        "chest",       (0.00, 0, 1.45), (0.00, 0, 1.55)),
    ("head",        "neck",        (0.00, 0, 1.55), (0.00, 0, 1.78)),
    ("shoulder.L",  "chest",       (0.03, 0, 1.44), (0.17, 0, 1.44)),
    ("upper_arm.L", "shoulder.L",  (0.17, 0, 1.44), (0.45, 0, 1.30)),
    ("lower_arm.L", "upper_arm.L", (0.45, 0, 1.30), (0.68, 0, 1.14)),
    ("hand.L",      "lower_arm.L", (0.68, 0, 1.14), (0.78, 0, 1.07)),
    ("shoulder.R",  "chest",       (-0.03, 0, 1.44), (-0.17, 0, 1.44)),
    ("upper_arm.R", "shoulder.R",  (-0.17, 0, 1.44), (-0.45, 0, 1.30)),
    ("lower_arm.R", "upper_arm.R", (-0.45, 0, 1.30), (-0.68, 0, 1.14)),
    ("hand.R",      "lower_arm.R", (-0.68, 0, 1.14), (-0.78, 0, 1.07)),
    ("thigh.L",     "hips",        (0.10, 0, 0.96), (0.11, 0, 0.54)),
    ("shin.L",      "thigh.L",     (0.11, 0, 0.54), (0.12, 0, 0.10)),
    ("foot.L",      "shin.L",      (0.12, 0, 0.10), (0.12, -0.16, 0.02)),
    ("thigh.R",     "hips",        (-0.10, 0, 0.96), (-0.11, 0, 0.54)),
    ("shin.R",      "thigh.R",     (-0.11, 0, 0.54), (-0.12, 0, 0.10)),
    ("foot.R",      "shin.R",      (-0.12, 0, 0.10), (-0.12, -0.16, 0.02)),
]

# Radius of the volume attached to each bone, to build a plausible mesh.
RADII = {
    "hips": 0.15, "spine": 0.15, "chest": 0.16, "neck": 0.06, "head": 0.11,
    "shoulder.L": 0.07, "shoulder.R": 0.07,
    "upper_arm.L": 0.06, "upper_arm.R": 0.06,
    "lower_arm.L": 0.05, "lower_arm.R": 0.05,
    "hand.L": 0.045, "hand.R": 0.045,
    "thigh.L": 0.09, "thigh.R": 0.09,
    "shin.L": 0.07, "shin.R": 0.07,
    "foot.L": 0.06, "foot.R": 0.06,
}

# The character's animations: (bone, axis, angle in degrees) per keyframe.
# `idle` breathes, `wave` raises the right arm and swings it: two actions with
# different silhouettes, so that a render mixing them up shows.
ANIMATIONS = {
    "idle_loop": {"frames": (1, 13, 25), "keys": [("chest", 0, (0, 4, 0))]},
    "wave": {"frames": (1, 7, 13, 19, 25),
             "keys": [("upper_arm.R", 0, (0, 75, 85, 75, 0)),
                      ("lower_arm.R", 0, (0, 40, -10, 40, 0))]},
}


def build_armature() -> bpy.types.Object:
    armature_data = bpy.data.armatures.new("Armature")
    armature = bpy.data.objects.new("Armature", armature_data)
    bpy.context.collection.objects.link(armature)
    activate(armature)
    bpy.ops.object.mode_set(mode="EDIT")

    created: dict[str, bpy.types.EditBone] = {}
    for name, parent, head, tail in SKELETON:
        bone = armature_data.edit_bones.new(name)
        bone.head = Vector(head)
        bone.tail = Vector(tail)
        bone.use_connect = False
        if parent is not None:
            bone.parent = created[parent]
        created[name] = bone

    bpy.ops.object.mode_set(mode="OBJECT")
    return armature


def build_body() -> bpy.types.Object:
    """One volume per bone, joined into a single mesh."""
    pieces = []
    for name, _, head, tail in SKELETON:
        head_v, tail_v = Vector(head), Vector(tail)
        length = max((tail_v - head_v).length, 0.02)
        bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=RADII.get(name, 0.05),
                                            depth=length, location=(head_v + tail_v) / 2)
        piece = bpy.context.active_object
        piece.rotation_mode = "QUATERNION"
        piece.rotation_quaternion = (tail_v - head_v).normalized().to_track_quat("Z", "Y")
        pieces.append(piece)

    deselect_all()
    for piece in pieces:
        piece.select_set(True)
    bpy.context.view_layer.objects.active = pieces[0]
    bpy.ops.object.join()
    body = bpy.context.active_object
    body.name = "Body"
    return body


def stack(obj: bpy.types.Object, name: str, action: bpy.types.Action) -> None:
    """Put the action in an NLA track of its name, as a careful export does."""
    track = obj.animation_data.nla_tracks.new()
    track.name = name
    track.strips.new(name, int(action.frame_range[0]), action)
    obj.animation_data.action = None


def animate_humanoid(armature: bpy.types.Object) -> list[str]:
    armature.animation_data_create()
    for name, spec in ANIMATIONS.items():
        action = bpy.data.actions.new(name)
        action.use_fake_user = True
        armature.animation_data.action = action
        for bone, axis, degrees in spec["keys"]:
            pose = armature.pose.bones[bone]
            pose.rotation_mode = "XYZ"
            for frame, value in zip(spec["frames"], degrees, strict=True):
                rotation = [0.0, 0.0, 0.0]
                rotation[axis] = math.radians(value)
                pose.rotation_euler = rotation
                pose.keyframe_insert("rotation_euler", frame=frame)
        stack(armature, name, action)
    return list(ANIMATIONS)


def build_humanoid() -> dict:
    armature = build_armature()
    body = build_body()
    # Automatic skinning: enough for a fixture.
    deselect_all()
    body.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
    animations = animate_humanoid(armature)
    return {"bones": [bone.name for bone in armature.data.bones],
            "vertices": len(body.data.vertices), "animations": animations}


def build_machine() -> dict:
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.5))
    base = bpy.context.active_object
    base.name = "Base"
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.6, depth=0.12,
                                        location=(0.0, -0.6, 1.0),
                                        rotation=(math.pi / 2, 0.0, 0.0))
    wheel = bpy.context.active_object
    wheel.name = "Wheel"
    # The blades stick out of the wheel: under flat light, a part that spins
    # without changing its silhouette would not be seen spinning.
    blades = []
    for angle in (0.0, math.pi / 2):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, -0.6, 1.0),
                                        rotation=(0.0, angle, 0.0))
        blade = bpy.context.active_object
        blade.scale = (1.9, 0.08, 0.16)
        blades.append(blade)
    deselect_all()
    for blade in blades:
        blade.select_set(True)
    wheel.select_set(True)
    bpy.context.view_layer.objects.active = wheel
    bpy.ops.object.join()

    wheel.animation_data_create()
    action = bpy.data.actions.new("spin_loop")
    action.use_fake_user = True
    wheel.animation_data.action = action
    wheel.rotation_mode = "XYZ"
    for frame, degrees in ((1, 0.0), (25, 360.0)):
        wheel.rotation_euler = (math.pi / 2, math.radians(degrees), 0.0)
        wheel.keyframe_insert("rotation_euler", frame=frame)
    stack(wheel, "spin_loop", action)
    return {"bones": [], "vertices": len(base.data.vertices) + len(wheel.data.vertices),
            "animations": ["spin_loop"]}


def handler(args: dict) -> dict:
    output = args["output"]
    kind = args.get("kind", "humanoid")
    if kind not in ("humanoid", "machine"):
        raise ValueError(f"unknown fixture kind: {kind}")

    reset_scene()
    built = build_humanoid() if kind == "humanoid" else build_machine()
    bpy.ops.export_scene.gltf(
        filepath=output,
        export_format="GLB" if output.lower().endswith(".glb") else "GLTF_SEPARATE",
        export_yup=True,
        use_selection=False,
        export_animation_mode="NLA_TRACKS",
        # An animation starts at 0 s: otherwise Godot holds the first pose
        # one frame too long where a cycle wraps.
        export_anim_slide_to_zero=True,
    )
    return {"output": output, "kind": kind, **built}


if __name__ == "__main__":
    main(handler)
