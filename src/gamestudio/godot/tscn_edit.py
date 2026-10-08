"""Editing an existing Godot 4 scene: reading and rewriting a node's properties.

`tscn.py` writes new scenes; here the scene is one the game already holds, and
nothing else may be disturbed. The file is read the way Godot writes it --
`[...]` sections, then `key = value` lines whose value may span several lines
(a text, an array). An edit replaces the block of a single property, or appends
it at the end of the section; the rest of the file is returned byte for byte.

Values come and go in a simple form: text, number, boolean, `#rrggbbaa` color,
`[x, y]` vector, resource by its `res://` path. A value this module cannot read
is returned raw, and left untouched.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

_SECTION = re.compile(r"^\[(\w+)(.*)\]\s*$")
_PROPERTY = re.compile(r"^([A-Za-z_][\w/:.]*)\s*=\s?(.*)$")
_ATTR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\w+\([^)]*\)|[^\s\]]+)')
_COLOR = re.compile(r"^Color\(([^)]*)\)$")
_VECTOR2 = re.compile(r"^Vector2i?\(([^)]*)\)$")
_EXT = re.compile(r'^ExtResource\(\s*"([^"]+)"\s*\)$')
_NUMBER = re.compile(r"^-?\d+(\.\d+)?(e[+-]?\d+)?$")
_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class SceneEditError(ValueError):
    """An edit the scene refuses: missing node, malformed value."""


@dataclass
class Section:
    """A section of the file: its header, and the lines that follow it."""

    kind: str
    attrs: dict[str, str]
    start: int  # the header line
    end: int  # the line after the section's last one
    properties: dict[str, tuple[int, int]] = field(default_factory=dict)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return _unescape(value[1:-1])
    return value


def _unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def quote(text: str) -> str:
    """A text as Godot writes it: quoted, escaped."""
    escaped = (text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
               .replace("\t", "\\t"))
    return f'"{escaped}"'


def _is_continuation(line: str) -> bool:
    return not line.startswith("[") and _PROPERTY.match(line) is None


class Scene:
    """A `.tscn` scene in memory, editable property by property."""

    def __init__(self, text: str) -> None:
        self.lines = text.split("\n")
        self.sections = self._parse()

    # ------------------------------------------------------------------ reading

    def _parse(self) -> list[Section]:
        sections: list[Section] = []
        current: Section | None = None
        open_value = False
        for index, line in enumerate(self.lines):
            if not open_value:
                match = _SECTION.match(line)
                if match:
                    if current is not None:
                        current.end = index
                    attrs = {key: _unquote(value)
                             for key, value in _ATTR.findall(match.group(2))}
                    current = Section(match.group(1), attrs, index, len(self.lines))
                    sections.append(current)
                    continue
            if current is None:
                continue
            if open_value:
                key = next(reversed(current.properties))
                start, _ = current.properties[key]
                current.properties[key] = (start, index + 1)
                open_value = _still_open(self.lines[start:index + 1])
                continue
            prop = _PROPERTY.match(line)
            if prop:
                current.properties[prop.group(1)] = (index, index + 1)
                open_value = _still_open([line])
        if current is not None:
            current.end = len(self.lines)
        return sections

    def nodes(self) -> dict[str, Section]:
        """The nodes by path in the scene: `.` for the root, then `A/B`."""
        found: dict[str, Section] = {}
        for section in self.sections:
            if section.kind != "node":
                continue
            name = section.attrs.get("name", "")
            parent = section.attrs.get("parent")
            if parent is None:
                found["."] = section
            elif parent == ".":
                found[name] = section
            else:
                found[f"{parent}/{name}"] = section
        return found

    def node(self, path: str) -> Section:
        found = self.nodes().get(path or ".")
        if found is None:
            raise SceneEditError(f"node not found in the scene: {path}")
        return found

    def raw(self, path: str, key: str) -> str | None:
        """A property's written value, as is, or None."""
        section = self.node(path)
        if key not in section.properties:
            return None
        start, end = section.properties[key]
        text = "\n".join(self.lines[start:end])
        return text.split("=", 1)[1].strip()

    def ext_resources(self) -> dict[str, dict[str, str]]:
        """The external resources, by identifier."""
        return {section.attrs["id"]: section.attrs for section in self.sections
                if section.kind == "ext_resource" and "id" in section.attrs}

    def read(self, path: str, key: str) -> Any:
        """A property's value in its simple form (see `decode`)."""
        raw = self.raw(path, key)
        return None if raw is None else decode(raw, self.ext_resources())

    # ------------------------------------------------------------------ writing

    def write(self, path: str, key: str, value: Any, kind: str) -> None:
        """Write a node's property; `None` removes it (default value)."""
        if not _PROPERTY.match(f"{key} = 0"):
            raise SceneEditError(f"invalid property: {key}")
        section = self.node(path)
        if value is None:
            if key in section.properties:
                start, end = section.properties[key]
                del self.lines[start:end]
                self.sections = self._parse()
            return
        if kind == "texture":
            encoded = f'ExtResource("{self._ext_resource(str(value), "Texture2D")}")'
            section = self.node(path)  # adding a resource shifted the lines
        else:
            encoded = encode(value, kind)
        line = f"{key} = {encoded}"
        if key in section.properties:
            start, end = section.properties[key]
            self.lines[start:end] = [line]
        else:
            # At the end of the section, before the blank lines separating it from the next.
            at = section.end
            while at > section.start + 1 and not self.lines[at - 1].strip():
                at -= 1
            self.lines.insert(at, line)
        self.sections = self._parse()

    def _ext_resource(self, res_path: str, kind: str) -> str:
        """The identifier of an external resource, added if the scene lacks it."""
        if not res_path.startswith("res://"):
            raise SceneEditError(f"a resource is referenced with res://: {res_path}")
        # The path is written as is between quotes in a header: a quote or a
        # newline would add lines to the scene.
        if any(char in '"\\' or ord(char) < 0x20 or ord(char) == 0x7F for char in res_path):
            raise SceneEditError(f"invalid resource path: {res_path!r}")
        for ident, attrs in self.ext_resources().items():
            if attrs.get("path") == res_path:
                return ident
        taken = set(self.ext_resources())
        number = len(taken) + 1
        while f"{number}_studio" in taken:
            number += 1
        ident = f"{number}_studio"
        header = f'[ext_resource type="{kind}" path="{res_path}" id="{ident}"]'
        existing = [s for s in self.sections if s.kind == "ext_resource"]
        if existing:
            self.lines.insert(existing[-1].start + 1, header)
        else:
            first = self.sections[0] if self.sections else None
            at = first.start + 1 if first is not None and first.kind.startswith("gd_") else 0
            self.lines[at:at] = ["", header]
        self._bump_load_steps()
        self.sections = self._parse()
        return ident

    def _bump_load_steps(self) -> None:
        """`load_steps` counts the resources: one more if it is declared."""
        for index, line in enumerate(self.lines):
            if line.startswith("[gd_scene"):
                self.lines[index] = re.sub(r"load_steps=(\d+)",
                                           lambda m: f"load_steps={int(m.group(1)) + 1}", line)
                return

    def text(self) -> str:
        return "\n".join(self.lines)


def _still_open(lines: list[str]) -> bool:
    """A value still open: an unclosed text, or pending brackets."""
    value = "\n".join(lines).split("=", 1)[1] if "=" in lines[0] else ""
    depth, in_string, escaped = 0, False, False
    for char in value:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
    return in_string or depth > 0


# ------------------------------------------------------------------- values


def _floats(body: str) -> list[float]:
    return [float(part) for part in body.split(",") if part.strip()]


def _hex(r: float, g: float, b: float, a: float = 1.0) -> str:
    def byte(value: float) -> str:
        return f"{max(0, min(255, round(value * 255))):02x}"
    return f"#{byte(r)}{byte(g)}{byte(b)}{byte(a)}"


def decode(raw: str, resources: dict[str, dict[str, str]] | None = None) -> Any:
    """A written value, brought back to its simple form; otherwise the raw text."""
    raw = raw.strip()
    if raw in ("true", "false"):
        return raw == "true"
    if raw.startswith('"') and raw.endswith('"'):
        return _unescape(raw[1:-1])
    if _NUMBER.match(raw):
        return float(raw) if any(c in raw for c in ".e") else int(raw)
    match = _COLOR.match(raw)
    if match:
        try:
            return _hex(*_floats(match.group(1)))
        except (TypeError, ValueError):
            return raw
    match = _VECTOR2.match(raw)
    if match:
        try:
            x, y = _floats(match.group(1))
        except ValueError:
            return raw
        return [x, y]
    match = _EXT.match(raw)
    if match and resources is not None:
        found = resources.get(match.group(1))
        if found and found.get("path"):
            return found["path"]
    return raw


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SceneEditError(f"number expected: {value!r}") from exc
    # Godot writes neither `nan` nor `inf`, and no integer comes from an infinity.
    if not math.isfinite(number):
        raise SceneEditError(f"finite number expected: {value!r}")
    return number


def _g(value: float) -> str:
    """A number as Godot writes it: `24`, `0.5`."""
    return f"{value:.6g}"


def encode(value: Any, kind: str) -> str:
    """A simple value, written as Godot expects for this kind of property."""
    if kind == "text":
        return quote(str(value))
    if kind == "int":
        return str(round(_number(value)))
    if kind == "enum":
        return str(round(_number(value)))
    if kind == "float":
        number = _number(value)
        return _g(number) if number != int(number) else f"{int(number)}.0"
    if kind == "bool":
        if not isinstance(value, bool):
            raise SceneEditError(f"true or false expected: {value!r}")
        return "true" if value else "false"
    if kind == "color":
        match = _HEX.match(str(value).strip())
        if not match:
            raise SceneEditError(f"color expected (#rrggbb or #rrggbbaa): {value!r}")
        digits = match.group(1) + ("ff" if len(match.group(1)) == 6 else "")
        channels = [int(digits[i:i + 2], 16) / 255 for i in range(0, 8, 2)]
        return "Color(" + ", ".join(_g(round(c, 4)) for c in channels) + ")"
    if kind == "vector2":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise SceneEditError(f"vector expected ([x, y]): {value!r}")
        x, y = (_number(v) for v in value)
        return f"Vector2({_g(x)}, {_g(y)})"
    raise SceneEditError(f"unknown value kind: {kind}")
