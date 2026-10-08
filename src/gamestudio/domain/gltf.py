"""Lightweight reading of a glTF/GLB file, with no dependency and no Blender.

A .glb is a binary header followed by a JSON chunk: bone and animation names
read in a few milliseconds. No need to start Blender (~4 s) to survey a
delivery.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

GLB_MAGIC = 0x46546C67       # 'glTF'
CHUNK_JSON = 0x4E4F534A      # 'JSON'

# The suffix that makes an animation loop on import in Godot, which strips it
# from the name (checked on Godot 4.7: `idle_loop` becomes a looping `idle`).
LOOP_SUFFIXES = ("_loop", "-loop")


def godot_animation(name: str) -> tuple[str, bool]:
    """The name a glTF animation takes in Godot, and whether it loops there.

    The studio reads names the way Godot does: a sprite sheet and the
    `AnimationPlayer` must refer to the same `walk`.
    """
    for suffix in LOOP_SUFFIXES:
        if name.lower().endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], True
    return name, False


def read_gltf_json(path: Path) -> dict:
    """The JSON document of a .glb or a .gltf."""
    data = path.read_bytes()
    if len(data) >= 4 and struct.unpack_from("<I", data, 0)[0] == GLB_MAGIC:
        _, _, total = struct.unpack_from("<III", data, 0)
        offset = 12
        while offset < min(total, len(data)):
            chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
            offset += 8
            if chunk_type == CHUNK_JSON:
                return json.loads(data[offset:offset + chunk_length].decode("utf-8"))
            offset += chunk_length
        raise ValueError(f"no JSON chunk in {path}")
    return json.loads(data.decode("utf-8"))


@dataclass
class GltfRigInfo:
    """What a glTF file carries: its bones (if it has a skin) and its animations."""

    bones: list[str]
    skin_count: int
    animation_names: list[str]

    @property
    def is_rigged(self) -> bool:
        return self.skin_count > 0 and bool(self.bones)


def inspect_rig(path: Path) -> GltfRigInfo:
    """List the bones (in skin order) and the animation names."""
    doc = read_gltf_json(path)
    nodes = doc.get("nodes", [])
    skins = doc.get("skins", [])

    joint_indices: list[int] = []
    for skin in skins:
        joint_indices.extend(skin.get("joints", []))
    # Without a declared skin, a bone cannot be told apart from any other node.
    ordered = list(dict.fromkeys(joint_indices))

    def node_name(index: int) -> str:
        node = nodes[index] if 0 <= index < len(nodes) else {}
        return node.get("name") or f"node_{index}"

    return GltfRigInfo(
        bones=[node_name(i) for i in ordered],
        skin_count=len(skins),
        animation_names=[a.get("name", f"anim_{i}")
                         for i, a in enumerate(doc.get("animations", []))],
    )
