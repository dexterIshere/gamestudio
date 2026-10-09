"""A game's colors: each one written in its files, by aspect of the art direction, and where.

Read without running anything. A color is a literal -- `Color(...)` in a
scene, a script or a resource (`survey.colors`), or the default of a shader
setting marked `source_color`. Each use says where it is written: the file,
and the name carrying it (a property, a theme key, a constant, a shader
setting). It also says the aspect of the art direction it belongs to -- the
interface (Controls, themes, StyleBoxes, canvas_item shaders), the materials
(3D objects, their materials, spatial shaders), the sky (environments, sky
shaders), or elsewhere (the game's logic, 2D objects, other shaders).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import fonts, survey

ASPECTS = ("interface", "materials", "sky", "other")
# The colors a page can hold, the most used first; and the names kept per use.
MAX_COLORS = 256
MAX_NAMES = 6

SHADER_ASPECTS = {"canvas_item": "interface", "spatial": "materials", "sky": "sky"}
SKY_TYPES = frozenset({"Environment", "Sky", "WorldEnvironment", "ProceduralSkyMaterial",
                       "PhysicalSkyMaterial", "PanoramaSkyMaterial"})
INTERFACE_TYPES = survey.STYLEBOXES | {"Theme", "LabelSettings"}

_HEADER = re.compile(r"^\[(gd_scene|gd_resource|ext_resource|sub_resource|node|resource)\b")
_KEY = re.compile(r"^([\w/.:]+)\s*=")
_SHADER_REF = re.compile(r'^shader\s*=\s*ExtResource\(\s*"([^"]+)"\s*\)')
# A script's color, named by what carries it: a quoted key (`"font_color", Color(`,
# `"plains": Color(`), a declaration, or what is assigned.
_QUOTED = re.compile(r'"([\w ]+)"\s*[:,]\s*Color(?:8|\.html)?\(')
_DECLARED = re.compile(r"\b(?:const|var)\s+(\w+)")
_ASSIGNED = re.compile(r"([A-Za-z_][\w.]*)\s*(?::\s*\w+\s*)?[+\-*/]?=(?!=)")
_SKY_NAME = re.compile(r"(?:^|_)(?:sky|horizon|fog|ambient|background)(?:_|$)", re.I)
_UNIFORM = re.compile(
    r"^\s*(?:global\s+|instance\s+)?uniform\s+vec([34])\s+(\w+)\s*:\s*[^=;]*"
    r"\b(?:source_color|hint_color)\b[^=;]*=\s*vec[34]\s*\(([^)]*)\)\s*;", re.M)


def shader_aspect(kind: str) -> str:
    """The aspect of the art direction a shader type belongs to."""
    return SHADER_ASPECTS.get(kind, "other")


def _type_aspect(kind: str) -> str:
    """The aspect a node or resource type paints; "" when it does not say."""
    if kind in SKY_TYPES:
        return "sky"
    if kind in INTERFACE_TYPES or survey.is_ui(kind):
        return "interface"
    if kind.endswith("3D"):
        return "materials"
    if kind.endswith("2D"):
        return "other"
    return ""


def _glsl_hex(size: str, values: str) -> str:
    """A `vecN(...)` default as hexadecimal; "" when it is not literal."""
    try:
        numbers = [float(value) for value in values.split(",")]
    except ValueError:
        return ""
    if len(numbers) == 1:
        numbers *= int(size)
    if len(numbers) != int(size):
        return ""
    return survey._hex(*numbers)


def _godot_text(text: str, default: str, shaders: dict[str, str],
                godot: str) -> list[tuple[str, str, str]]:
    """A scene's or a resource's colors: (hex, aspect, name), block by block.

    A block takes the aspect of its type -- a StyleBox paints interface, an
    Environment the sky; a ShaderMaterial, that of its shader -- and otherwise
    the file's.
    """
    found: list[tuple[str, str, str]] = []
    ext: dict[str, str] = {}
    aspect = default
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header:
            attrs = survey._attrs(line)
            if header.group(1) == "ext_resource":
                if "id" in attrs and "path" in attrs:
                    ext[attrs["id"]] = survey._local(attrs["path"], godot)
                continue
            if header.group(1) == "resource":
                aspect = default
                continue
            aspect = _type_aspect(attrs.get("type", "")) or default
            if header.group(1) == "gd_resource":
                default = aspect
            continue
        shader = _SHADER_REF.match(line)
        if shader and ext.get(shader.group(1)) in shaders:
            aspect = shader_aspect(shaders[ext[shader.group(1)]])
            continue
        tints = survey.colors(line)
        if not tints:
            continue
        key = _KEY.match(line)
        name = key.group(1) if key else ""
        if name.startswith(("shader_parameter/", "theme_override_colors/")):
            name = name.split("/", 1)[1]
        found += [(tint, aspect, name) for tint in tints]
    return found


def _script_text(text: str, default: str) -> list[tuple[str, str, str]]:
    """A script's colors: (hex, aspect, name), line by line.

    A world script's color named after the sky, the fog, the ambient light or
    the background colors the sky.
    """
    found: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        code = line.split("#", 1)[0] if '"#' not in line else line
        tints = survey.colors(code)
        if not tints:
            continue
        named = _QUOTED.search(code) or _DECLARED.search(code) or _ASSIGNED.search(code)
        name = named.group(1).rsplit(".", 1)[-1] if named else ""
        aspect = "sky" if default != "interface" and _SKY_NAME.search(name) else default
        found += [(tint, aspect, name) for tint in tints]
    return found


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > survey.MAX_SCENE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def palette(root: Path, game: dict[str, Any]) -> dict[str, Any]:
    """The game's colors, the most used first, each with its uses and its aspects.

    `colors`: `hex`, `count` (the times it is written), `aspects` (count per
    aspect) and `uses` (`file`, `aspect`, `count`, `names`). A developer's
    folder is not the game the player sees: it is left out.
    """
    shaders = {shader["path"]: shader["type"] for shader in game["shaders"]}
    # An include paints where the shaders that include it do.
    for shader in game["shaders"]:
        if shader["type"] == "include":
            hosts = [shaders[user] for user in shader["users"]
                     if shaders.get(user) not in (None, "include")]
            shaders[shader["path"]] = hosts[0] if hosts else ""

    found: list[tuple[str, str, str, str]] = []

    def godot_of(relative: str) -> str:
        """The Godot project holding a file: its nearest root."""
        return max((entry["path"] for entry in game["godot"]
                    if not entry["path"] or relative.startswith(entry["path"] + "/")),
                   key=len, default="")

    for scene in game["scenes"]:
        if scene["dev"]:
            continue
        root_type = scene["root"]
        default = _type_aspect(root_type) or ("interface" if scene["role"] == "interface"
                                              else "other")
        text = _read(root / scene["path"])
        found += [(tint, aspect, name, scene["path"]) for tint, aspect, name in
                  _godot_text(text, default, shaders, godot_of(scene["path"]))]
    for script in game["scripts"]:
        if script["dev"]:
            continue
        default = "interface" if script["ui"] else (_type_aspect(script["base"]) or "other")
        found += [(tint, aspect, name, script["path"]) for tint, aspect, name in
                  _script_text(_read(root / script["path"]), default)]
    for path, godot in fonts._godot_files(root, game, ".tres"):
        relative = path.relative_to(root).as_posix()
        if survey._is_dev(relative):
            continue
        found += [(tint, aspect, name, relative) for tint, aspect, name in
                  _godot_text(_read(path), "other", shaders, godot)]
    for shader in game["shaders"]:
        if survey._is_dev(shader["path"]):
            continue
        aspect = shader_aspect(shaders[shader["path"]])
        for match in _UNIFORM.finditer(_read(root / shader["path"])):
            tint = _glsl_hex(match.group(1), match.group(3))
            if tint:
                found.append((tint, aspect, match.group(2), shader["path"]))

    colors: dict[str, dict[str, Any]] = {}
    for tint, aspect, name, file in found:
        color = colors.setdefault(tint, {"hex": tint, "count": 0, "aspects": {}, "uses": {}})
        color["count"] += 1
        color["aspects"][aspect] = color["aspects"].get(aspect, 0) + 1
        use = color["uses"].setdefault((file, aspect), {"file": file, "aspect": aspect,
                                                         "count": 0, "names": []})
        use["count"] += 1
        if name and name not in use["names"] and len(use["names"]) < MAX_NAMES:
            use["names"].append(name)
    ranked = sorted(colors.values(), key=lambda color: (-color["count"], color["hex"]))
    for color in ranked:
        color["uses"] = sorted(color["uses"].values(),
                               key=lambda use: (ASPECTS.index(use["aspect"]), -use["count"],
                                                use["file"]))
    return {"colors": ranked[:MAX_COLORS], "distinct": len(ranked),
            "total": sum(color["count"] for color in ranked)}
