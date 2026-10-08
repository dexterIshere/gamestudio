"""Writing Godot 4 scene files (.tscn) and resources (.tres).

The format is textual and stable: generating it directly avoids depending on an
import plugin on the Godot side, and keeps scenes readable in a diff. So native
resources and scenes are produced -- SpriteFrames, AnimatedSprite2D,
GPUParticles2D -- rather than going through a third-party runtime.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _uid(*parts: str) -> str:
    """Short deterministic identifier, used to name internal resources.

    Deliberately NOT a `uid://`: Godot keeps its own UID registry and rejects
    those it does not know ("invalid UID"). External resources are therefore
    referenced by their `res://` path, and Godot writes the real UIDs itself on
    the first save from the editor.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    value = int.from_bytes(digest[:10], "big")
    out = []
    while value and len(out) < 13:
        value, rem = divmod(value, len(alphabet))
        out.append(alphabet[rem])
    return "".join(out) or "a"


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def format_value(value: Any) -> str:
    """Serialize a Python value into Godot file syntax."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    if isinstance(value, (ExtResource, SubResource, RawValue)):
        return str(value)
    if isinstance(value, str):
        return f'"{escape(value)}"'
    if isinstance(value, (list, tuple)):
        return f"[{', '.join(format_value(v) for v in value)}]"
    if isinstance(value, dict):
        inner = ", ".join(f"{format_value(k)}: {format_value(v)}" for k, v in value.items())
        return f"{{{inner}}}"
    if value is None:
        return "null"
    return str(value)


@dataclass(frozen=True)
class RawValue:
    """Value inserted as is (NodePath, Vector2, PackedVector2Array...)."""

    text: str

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class ExtResource:
    id: str

    def __str__(self) -> str:
        return f'ExtResource("{self.id}")'


@dataclass(frozen=True)
class SubResource:
    id: str

    def __str__(self) -> str:
        return f'SubResource("{self.id}")'


@dataclass
class _Node:
    name: str
    type: str
    parent: str | None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class _SubResource:
    id: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)


class SceneWriter:
    """Build a .tscn node by node."""

    def __init__(self, root_name: str, root_type: str = "Node2D", *, scene_key: str = "") -> None:
        self._scene_key = scene_key or root_name
        self._ext: list[tuple[str, str, str]] = []  # (id, type, path)
        self._sub: list[_SubResource] = []
        self._nodes: list[_Node] = [_Node(name=root_name, type=root_type, parent=None)]

    # -------------------------------------------------------------- resources

    def ext_resource(self, res_type: str, res_path: str, *, key: str = "") -> ExtResource:
        for existing_id, _, path in self._ext:
            if path == res_path:
                return ExtResource(existing_id)
        res_id = f"{len(self._ext) + 1}_{_uid(key or res_path)[:5]}"
        self._ext.append((res_id, res_type, res_path))
        return ExtResource(res_id)

    def sub_resource(self, res_type: str, properties: dict[str, Any], *,
                     key: str = "") -> SubResource:
        res_id = f"{res_type}_{_uid(self._scene_key, key or str(len(self._sub)))[:5]}"
        self._sub.append(_SubResource(id=res_id, type=res_type, properties=properties))
        return SubResource(res_id)

    # ------------------------------------------------------------------ nodes

    def node(self, name: str, node_type: str, parent: str, **properties: Any) -> str:
        """Add a node. `parent` is a path relative to the root ('.' for the root).

        Returns the node's path, usable as another node's parent.
        """
        self._nodes.append(_Node(name=name, type=node_type, parent=parent,
                                 properties=properties))
        return name if parent == "." else f"{parent}/{name}"

    # ----------------------------------------------------------------- output

    def render(self) -> str:
        load_steps = len(self._ext) + len(self._sub) + 1
        lines = [f"[gd_scene load_steps={load_steps} format=3]", ""]
        for res_id, res_type, res_path in self._ext:
            lines.append(
                f'[ext_resource type="{res_type}" path="{escape(res_path)}" id="{res_id}"]'
            )
        if self._ext:
            lines.append("")

        for sub in self._sub:
            lines.append(f'[sub_resource type="{sub.type}" id="{sub.id}"]')
            for key, value in sub.properties.items():
                lines.append(f"{key} = {format_value(value)}")
            lines.append("")

        for node in self._nodes:
            header = f'[node name="{escape(node.name)}" type="{node.type}"'
            if node.parent is not None:
                header += f' parent="{escape(node.parent)}"'
            lines.append(header + "]")
            for key, value in node.properties.items():
                lines.append(f"{key} = {format_value(value)}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")
        return path


class ResourceWriter:
    """Write a standalone resource (.tres), for example a SpriteFrames."""

    def __init__(self, res_type: str, *, resource_key: str = "") -> None:
        self.res_type = res_type
        self._key = resource_key or res_type
        self._ext: list[tuple[str, str, str]] = []  # (id, type, path)
        self._sub: list[_SubResource] = []
        self.properties: dict[str, Any] = {}

    def ext_resource(self, res_type: str, res_path: str, *, key: str = "") -> ExtResource:
        for existing_id, _, path in self._ext:
            if path == res_path:
                return ExtResource(existing_id)
        res_id = f"{len(self._ext) + 1}_{_uid(key or res_path)[:5]}"
        self._ext.append((res_id, res_type, res_path))
        return ExtResource(res_id)

    def sub_resource(self, res_type: str, properties: dict[str, Any], *,
                     key: str = "") -> SubResource:
        res_id = f"{res_type}_{_uid(self._key, key or str(len(self._sub)))[:5]}"
        self._sub.append(_SubResource(id=res_id, type=res_type, properties=properties))
        return SubResource(res_id)

    def render(self) -> str:
        load_steps = len(self._ext) + len(self._sub) + 1
        lines = [
            f'[gd_resource type="{self.res_type}" load_steps={load_steps} format=3]',
            "",
        ]
        for res_id, res_type, res_path in self._ext:
            lines.append(
                f'[ext_resource type="{res_type}" path="{escape(res_path)}" id="{res_id}"]'
            )
        if self._ext:
            lines.append("")
        for sub in self._sub:
            lines.append(f'[sub_resource type="{sub.type}" id="{sub.id}"]')
            for key, value in sub.properties.items():
                lines.append(f"{key} = {format_value(value)}")
            lines.append("")
        lines.append("[resource]")
        for key, value in self.properties.items():
            lines.append(f"{key} = {format_value(value)}")
        return "\n".join(lines).rstrip() + "\n"

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render(), encoding="utf-8")
        return path
