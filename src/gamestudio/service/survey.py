"""A game's survey: what its folder already contains, read without running anything.

A project often starts before the studio: its folder already holds scenes,
scripts, docs. Updating a studio section -- interface, icons, props, mechanics,
art direction, a world section -- means catching it up with what the game
contains, and the agent doing it needs a map before reading. This module draws
it: it walks the folder, reads the header of Godot scenes, scripts and
resources, counts the rest, and launches nothing.

The map says where to look, not what the game does: what a screen does, the
agent reads in its scene and its script (`handoff.shelf_brief`).
"""

from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

# What is never walked: dependencies and build outputs. Hidden folders (`.git`,
# `.godot`, `.gamestudio`, the agents') are all skipped, and Godot addons
# (`addons/`) are named, not surveyed: they are third-party code.
SKIPPED = frozenset({"node_modules", "target", "build", "dist", "out", "venv",
                     "__pycache__", "addons"})

# A developer's work folder: what it holds is not the game the player sees, and
# no section describes it.
DEV_DIRS = frozenset({"dev", "debug", "test", "tests", "sandbox", "demo", "demos",
                      "prototype", "prototypes", "playground", "examples", "samples",
                      "scratch", "tmp"})

# A guard: a game repository fits in a few thousand files.
MAX_FILES = 20_000
# Beyond this, a scene embeds its resources: it is counted, not read.
MAX_SCENE_BYTES = 4 * 1024 * 1024

CODE = {".gd": "GDScript", ".cs": "C#", ".rs": "Rust", ".py": "Python",
        ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".go": "Go",
        ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".c": "C", ".h": "C",
        ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".lua": "Lua"}
IMAGES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".bmp",
                    ".tga", ".exr", ".hdr"})
FONTS = frozenset({".ttf", ".otf", ".woff", ".woff2", ".fnt"})
SHADERS = frozenset({".gdshader", ".gdshaderinc", ".shader"})
DATA = frozenset({".json", ".yaml", ".yml", ".toml", ".csv", ".ron", ".xml"})
DOCS = frozenset({".md", ".markdown"})
TRANSLATIONS = frozenset({".po", ".pot", ".translation"})
MESHES = frozenset({".glb", ".gltf", ".fbx", ".obj", ".blend"})
AUDIO = frozenset({".ogg", ".wav", ".mp3", ".opus", ".flac"})

# What makes a scene a screen: a root of the Control family, or an interface
# layer. Containers and buttons are recognized by their class name.
UI_TYPES = frozenset({
    "Control", "Panel", "Label", "RichTextLabel", "TextureRect", "ColorRect",
    "NinePatchRect", "ReferenceRect", "LineEdit", "TextEdit", "CodeEdit", "ItemList",
    "Tree", "TabBar", "ProgressBar", "TextureProgressBar", "SpinBox", "ColorPicker",
    "GraphEdit", "GraphNode", "VideoStreamPlayer", "HSlider", "VSlider",
    "HScrollBar", "VScrollBar", "HSeparator", "VSeparator", "CanvasLayer", "Popup",
    "PopupMenu", "PopupPanel", "Window", "AcceptDialog", "ConfirmationDialog",
    "FileDialog",
})
# An icon is recognized by its folder or its name: `icons/`, `ui/icones/`,
# `icon_coin.png`. A whole word, not a substring (`silicon` is not one). French
# words are listed too: games name their folders in their own language.
ICON_WORDS = frozenset({"icon", "icons", "icone", "icones", "picto", "pictos",
                        "pictogram", "pictograms", "glyph", "glyphs", "emblem",
                        "emblems", "badge", "badges", "symbol", "symbols"})
# An interface prop: what the player touches or follows with the eyes --
# button, arrow, slider, checkbox, tab, frame, gauge. Recognized by its folder or
# name, like an icon; an image that is both stays an icon.
PROP_WORDS = frozenset({"button", "buttons", "btn", "bouton", "boutons", "arrow", "arrows",
                        "fleche", "fleches", "chevron", "chevrons", "toggle", "toggles",
                        "switch", "checkbox", "check", "radio", "slider", "sliders",
                        "knob", "handle", "grabber", "scrollbar", "scroll", "tab", "tabs",
                        "frame", "frames", "cadre", "cadres", "panel", "panels", "border",
                        "progress", "gauge", "jauge", "ninepatch", "nineslice", "cursor",
                        "widget", "widgets", "props"})
# A prop's Godot classes: the root of a component scene, or what a button
# script extends.
PROP_TYPES = frozenset({"BaseButton", "Button", "TextureButton", "CheckBox", "CheckButton",
                        "OptionButton", "MenuButton", "LinkButton", "ColorPickerButton",
                        "HSlider", "VSlider", "Slider", "ProgressBar", "TextureProgressBar",
                        "SpinBox", "TabBar", "HScrollBar", "VScrollBar", "NinePatchRect"})
STYLEBOXES = frozenset({"StyleBoxFlat", "StyleBoxTexture", "StyleBoxLine", "StyleBoxEmpty"})
_WORDS = re.compile(r"[a-z]+")
_PNG_SIZE = re.compile(rb"^\x89PNG\r\n\x1a\n....IHDR(.{4})(.{4})", re.DOTALL)
_SVG_DIM = re.compile(r'\b(width|height)="([\d.]+)(?:px)?"')
_SVG_BOX = re.compile(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)')
PARTICLES = frozenset({"GPUParticles2D", "GPUParticles3D", "CPUParticles2D",
                       "CPUParticles3D"})

# What sets a scene's mood: its light, its environment, its tint.
AMBIANCE = frozenset({"WorldEnvironment", "DirectionalLight3D", "OmniLight3D",
                      "SpotLight3D", "DirectionalLight2D", "PointLight2D",
                      "CanvasModulate", "LightmapGI", "VoxelGI", "ReflectionProbe"})
# What makes a scene a level: a tile grid, or the folder filing it.
LEVEL_TYPES = frozenset({"TileMap", "TileMapLayer", "GridMap"})
LEVEL_DIRS = frozenset({"level", "levels", "map", "maps", "niveau", "niveaux", "stage",
                        "stages", "zone", "zones", "world", "worlds", "arena", "arenas",
                        "room", "rooms"})
# The moments the art direction stages, recognized by the name of a scene or a
# sound: that is where the game says what it wants the player to feel. The
# French words are file names a game may use.
MOMENTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("launch", frozenset({"splash", "boot", "intro", "title", "titre", "menu",
                          "mainmenu", "start", "accueil", "home"})),
    ("loading", frozenset({"loading", "chargement", "loader"})),
    ("victory", frozenset({"victory", "victoire", "win", "won", "success", "reward",
                           "levelup", "complete"})),
    ("defeat", frozenset({"defeat", "defaite", "gameover", "lose", "lost", "death",
                          "mort", "fail", "failure"})),
    ("ending", frozenset({"credits", "ending", "outro", "fin"})),
)
# What is kept of an environment is what shows: background, sky, ambient light,
# tonemap, glow, fog, color adjustment.
ENV_KEYS = ("background_mode", "background_color", "background_energy", "sky",
            "ambient_light", "tonemap", "glow_", "fog_", "volumetric_fog_enabled",
            "adjustment_", "ssao_enabled", "ssr_enabled", "sdfgi_enabled")

_ATTR = re.compile(r'(\w+)=(?:"([^"]*)"|ExtResource\(\s*"([^"]*)"\s*\)|(\S+))')
_SCRIPT = re.compile(r'^script\s*=\s*ExtResource\(\s*"([^"]*)"\s*\)')
_EXTENDS = re.compile(r'^\s*(?:class_name\s+(\w+)\s+)?extends\s+("[^"]+"|[\w.]+)')
_CLASS_NAME = re.compile(r'^\s*class_name\s+(\w+)')
_QUOTED = re.compile(r'"([^"]*)"')
_NUM = r"\s*(-?[\d.]+(?:e[+-]?\d+)?)\s*"
_COLOR_F = re.compile(rf"\bColor\({_NUM},{_NUM},{_NUM}(?:,{_NUM})?\)", re.IGNORECASE)
_COLOR_S = re.compile(r'\bColor(?:\.html)?\(\s*"#?([0-9a-fA-F]{3,8})"\s*\)')
_COLOR_8 = re.compile(r"\bColor8\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)")
_SHADER_TYPE = re.compile(r"^\s*shader_type\s+(\w+)\s*;", re.MULTILINE)
_UNIFORM = re.compile(r"^\s*(?:global\s+|instance\s+)?uniform\s+(\w+)\s+(\w+)"
                      r"\s*(?::\s*([^=;]+?))?\s*(?:=\s*([^;]+?))?\s*;", re.MULTILINE)
_RES = re.compile(r'"(res://[^"]+)"')
_BUILT = re.compile(r"\b(" + "|".join(sorted(AMBIANCE | {"Environment"})) + r")\.new\(")
_STYLEBOX_NEW = re.compile(r"\bStyleBox(?:Flat|Texture|Line|Empty)\.new\(")
_SUB = re.compile(r'SubResource\(\s*"([^"]+)"\s*\)')


def is_ui(kind: str) -> bool:
    """A Godot node type that makes interface."""
    return kind in UI_TYPES or kind.endswith(("Container", "Button"))


def _attrs(line: str) -> dict[str, str]:
    """The attributes of a Godot header line: `[node name="X" type="Y"]`."""
    found: dict[str, str] = {}
    for match in _ATTR.finditer(line):
        key, quoted, ext, bare = match.groups()
        if quoted is not None:
            found[key] = quoted
        elif ext is not None:
            found[key] = ext
        else:
            found[key] = bare.rstrip("]")
    return found


def _rel(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return "" if relative == "." else relative


def _local(res_path: str, godot: str) -> str:
    """A `res://` path brought back to the game root: the one that gets cited."""
    if not res_path.startswith("res://"):
        return res_path
    inner = res_path.removeprefix("res://")
    return f"{godot}/{inner}" if godot else inner


def _is_dev(relative: str) -> bool:
    return any(part.lower() in DEV_DIRS for part in relative.split("/")[:-1])


def _title(path: Path) -> str:
    """A document's first heading, otherwise its name."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(40), handle, strict=False):
                if line.startswith("#"):
                    return line.lstrip("#").strip()
    except OSError:
        pass
    return path.stem


def _hex(r: float, g: float, b: float, a: float = 1.0) -> str:
    """A Godot color (floats from 0 to 1) as `#rrggbb`, `#rrggbbaa` if transparent."""
    def byte(value: float) -> int:
        return max(0, min(255, round(value * 255)))
    alpha = f"{byte(a):02x}" if a < 1 else ""
    return f"#{byte(r):02x}{byte(g):02x}{byte(b):02x}{alpha}"


def colors(text: str) -> list[str]:
    """The colors written literally in a Godot text, as hexadecimal.

    `Color(r, g, b[, a])`, `Color("#hex")`, `Color.html("hex")`, `Color8(…)`:
    a named (`Color.RED`) or computed color cannot be read without running.
    """
    found: list[tuple[int, str]] = []
    for match in _COLOR_F.finditer(text):
        try:
            values = [float(v) for v in match.groups() if v is not None]
        except ValueError:
            continue
        found.append((match.start(), _hex(*values)))
    for match in _COLOR_S.finditer(text):
        raw = match.group(1).lower()
        if len(raw) in (3, 4):
            raw = "".join(c * 2 for c in raw)
        if len(raw) not in (6, 8):
            continue
        if len(raw) == 8 and raw.endswith("ff"):
            raw = raw[:6]
        found.append((match.start(), f"#{raw}"))
    for match in _COLOR_8.finditer(text):
        values = [int(v) / 255 for v in match.groups() if v is not None]
        found.append((match.start(), _hex(*values)))
    return [color for _, color in sorted(found)]


def _read(path: Path) -> str:
    """A Godot file's text, unless it is oversized."""
    if path.stat().st_size > MAX_SCENE_BYTES:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _env_value(value: str) -> str:
    found = colors(value)
    return found[0] if found else value.strip('"')


def _keeps(key: str) -> bool:
    return key.startswith(ENV_KEYS)


def _project(path: Path) -> dict[str, Any]:
    """What `project.godot` says about the game: name, main scene, autoloads, display."""
    found: dict[str, Any] = {"name": "", "version": "", "main_scene": "",
                             "autoloads": {}, "inputs": [], "display": {}, "locales": [],
                             "splash": {}, "clear_color": ""}
    section = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith((";", '"', "}")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if section == "application":
            if key == "config/name":
                found["name"] = value.strip('"')
            elif key == "run/main_scene":
                found["main_scene"] = value.strip('"')
            elif key == "config/features":
                found["version"] = next((f for f in _QUOTED.findall(value)
                                         if f[:1].isdigit()), "")
            elif key.startswith("boot_splash/"):
                found["splash"][key.removeprefix("boot_splash/")] = _env_value(value)
        elif section == "rendering" and key == "environment/defaults/default_clear_color":
            found["clear_color"] = _env_value(value)
        elif section == "autoload":
            found["autoloads"][key] = value.strip('"').lstrip("*")
        elif section == "input" and value.startswith("{"):
            found["inputs"].append(key)
        elif section == "display" and key.startswith(("window/size/viewport_",
                                                       "window/handheld/orientation",
                                                       "window/stretch/")):
            found["display"][key.rsplit("/", 1)[-1]] = value.strip('"')
        elif section == "internationalization" and key == "locale/translations":
            found["locales"] = _QUOTED.findall(value)
    return found


def _scene(path: Path) -> dict[str, Any]:
    """A scene's header: its root, what it inherits, its script, its nodes."""
    resources: dict[str, str] = {}
    used: list[str] = []
    root: dict[str, str] | None = None
    in_root = False
    script = ""
    nodes = 0
    types: set[str] = set()
    environment: dict[str, str] = {}
    in_env = False
    tints: list[str] = []
    boxes = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("[ext_resource"):
                attrs = _attrs(line)
                if "id" in attrs and "path" in attrs:
                    resources[attrs["id"]] = attrs["path"]
                    if attrs.get("type") == "Script":
                        used.append(attrs["path"])
            elif line.startswith("[node "):
                attrs = _attrs(line)
                nodes += 1
                in_env = False
                if attrs.get("type"):
                    types.add(attrs["type"])
                in_root = root is None and "parent" not in attrs
                if in_root:
                    root = attrs
            elif line.startswith("["):
                in_root = False
                if line.startswith("[sub_resource") and \
                        _attrs(line).get("type") in STYLEBOXES:
                    boxes += 1
                # The scene's first environment: the one that colors it.
                in_env = (line.startswith("[sub_resource")
                          and _attrs(line).get("type") == "Environment" and not environment)
            else:
                tints += colors(line)
                key, sep, value = line.partition("=")
                if in_env and sep and _keeps(key.strip()):
                    environment[key.strip()] = _env_value(value.strip())
                if in_root and not script:
                    match = _SCRIPT.match(line)
                    if match:
                        script = resources.get(match.group(1), "")
    root = root or {}
    return {"root": root.get("type", ""),
            "inherits": resources.get(root.get("instance", ""), ""),
            "script": script, "uses": used, "nodes": nodes,
            "particles": bool(types & PARTICLES), "types": sorted(types),
            "refs": sorted(set(resources.values())), "environment": environment,
            "colors": tints, "styleboxes": boxes}


def _script(path: Path) -> dict[str, str]:
    """What a GDScript script extends, and the class name it declares."""
    extends = class_name = ""
    with path.open(encoding="utf-8", errors="replace") as handle:
        for _, line in zip(range(60), handle, strict=False):
            if not class_name:
                named = _CLASS_NAME.match(line)
                if named:
                    class_name = named.group(1)
            match = _EXTENDS.match(line)
            if match:
                class_name = class_name or (match.group(1) or "")
                extends = match.group(2).strip('"')
                break
    return {"extends": extends, "class_name": class_name}


def is_icon(relative: str) -> bool:
    """An image filed or named like an icon."""
    return any(word in ICON_WORDS for word in _WORDS.findall(relative.lower()))


def is_prop(relative: str) -> bool:
    """An image filed or named like an interface prop (and not like an icon)."""
    return (not is_icon(relative)
            and any(word in PROP_WORDS for word in _WORDS.findall(relative.lower())))


def is_prop_type(kind: str) -> bool:
    """A Godot prop class: a button, a slider, a gauge, a frame."""
    return kind in PROP_TYPES or kind.endswith("Button")


def image_size(path: Path) -> str:
    """A PNG or SVG's size, read from the header: `64x64`, or empty."""
    try:
        with path.open("rb") as handle:
            head = handle.read(2048)
    except OSError:
        return ""
    if path.suffix.lower() == ".png":
        match = _PNG_SIZE.match(head)
        if match:
            return (f"{int.from_bytes(match.group(1), 'big')}x"
                    f"{int.from_bytes(match.group(2), 'big')}")
        return ""
    if path.suffix.lower() == ".svg":
        text = head.decode("utf-8", errors="replace")
        tag = text[text.find("<svg"):] if "<svg" in text else ""
        tag = tag[:tag.find(">") + 1]
        dims = dict(_SVG_DIM.findall(tag))
        if "width" in dims and "height" in dims:
            return f"{float(dims['width']):g}x{float(dims['height']):g}"
        box = _SVG_BOX.search(tag)
        if box:
            return f"{float(box.group(1)):g}x{float(box.group(2)):g}"
    return ""


def _resource_type(path: Path) -> str:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return _attrs(handle.readline()).get("type", "")


def _tres(path: Path) -> dict[str, Any]:
    """A text resource: its type, what it uses, and what it says about the art direction.

    For a theme, its colors, named by the key carrying them
    (`Button/colors/font_color`, `Panel/styles/panel.bg_color`); for an
    environment, what shows.
    """
    text = _read(path)
    lines = text.splitlines()
    kind = _attrs(lines[0]).get("type", "") if lines else ""
    refs: list[str] = []
    subs: dict[str, list[tuple[str, str]]] = {}
    props: list[tuple[str, str]] = []
    block = ""
    for line in lines[1:]:
        if line.startswith("[ext_resource"):
            attrs = _attrs(line)
            if "path" in attrs:
                refs.append(attrs["path"])
        elif line.startswith("[sub_resource"):
            block = _attrs(line).get("id", "?")
            subs.setdefault(block, [])
        elif line.startswith("[resource]"):
            block = ""
        elif "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if block:
                subs[block] += [(key, color) for color in colors(value)[:1]]
            else:
                props.append((key, value))
    found: dict[str, Any] = {"type": kind, "refs": sorted(set(refs))}
    if kind == "Theme":
        palette: list[tuple[str, str]] = []
        for key, value in props:
            sub = _SUB.search(value)
            if sub:
                palette += [(f"{key}.{inner}", color)
                            for inner, color in subs.get(sub.group(1), [])]
            else:
                palette += [(key, color) for color in colors(value)[:1]]
        found["colors"] = palette
    elif kind == "Environment":
        found["environment"] = {key: _env_value(value) for key, value in props if _keeps(key)}
    return found


def _shader(path: Path) -> dict[str, Any]:
    """A shader: its type, and the settings it offers (its uniforms)."""
    text = _read(path)
    kind = _SHADER_TYPE.search(text)
    uniforms = []
    for name_type, name, hint, default in (
            (m.group(1), m.group(2), m.group(3), m.group(4)) for m in _UNIFORM.finditer(text)):
        entry = f"{name} ({name_type}"
        entry += f", {hint.strip()})" if hint else ")"
        if default:
            entry += f" = {' '.join(default.split())}"
        uniforms.append(entry)
    return {"type": kind.group(1) if kind else ("include" if path.suffix == ".gdshaderinc"
                                                else ""),
            "uniforms": uniforms, "refs": sorted(set(_RES.findall(text)))}


def moment(path: str) -> str:
    """The moment a file stages, from its name: launch, victory…"""
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    tokens = set(re.split(r"[_\-\s.]+", stem)) | {re.sub(r"[_\-\s.]+", "", stem)}
    return next((label for label, words in MOMENTS if tokens & words), "")


def _is_level(relative: str, types: list[str]) -> bool:
    folders = {part.lower() for part in relative.split("/")[:-1]}
    return bool(LEVEL_TYPES & set(types)) or bool(folders & LEVEL_DIRS)


def _role(kind: str) -> str:
    """What a scene is from its root: a screen, a world object, or logic."""
    if is_ui(kind):
        return "interface"
    if kind.endswith(("2D", "3D")) or kind in {"TileMap", "TileMapLayer"}:
        return "object"
    return "logic"


def _docs_order(entry: dict[str, Any]) -> tuple[int, str]:
    path = entry["path"]
    if "/" not in path and path.lower().startswith("readme"):
        return (0, path)
    if path.startswith("docs/"):
        return (1, path)
    return (2, path)


def game_map(root: Path, *, limit: int = MAX_FILES) -> dict[str, Any]:
    """The map of a game folder: Godot projects, scenes, scripts, resources, docs.

    Nothing is run or imported: the header of Godot text files is read, the
    rest is counted per folder. Paths are relative to the game root -- the ones
    an agent cites in a card.
    """
    root = root.resolve()
    godot: dict[str, dict[str, Any]] = {}
    docs: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    scripts: list[dict[str, Any]] = []
    themes: list[str] = []
    fonts: list[str] = []
    shaders: list[dict[str, Any]] = []
    resources: dict[str, dict[str, Any]] = {}
    theme_colors: dict[str, list[tuple[str, str]]] = {}
    environments: list[dict[str, Any]] = []
    tints: Counter[str] = Counter()
    tinted: dict[str, list[str]] = {}
    audio: list[str] = []
    translations: list[str] = []
    images: Counter[str] = Counter()
    icons: list[dict[str, Any]] = []
    props: list[dict[str, Any]] = []
    meshes: Counter[str] = Counter()
    data: Counter[str] = Counter()
    code: dict[str, Counter[str]] = {}
    seen = 0
    truncated = False

    def owner(relative: str) -> str:
        """The Godot project containing a file: its nearest root."""
        best = None
        for base in godot:
            if base == "" or relative == base or relative.startswith(base + "/"):
                if best is None or len(base) > len(best):
                    best = base
        return best if best is not None else ""

    def tint(relative: str, dev: bool, found: list[str]) -> None:
        """The colors written in the game, counted, and where they are found."""
        if dev:
            return
        for color in found:
            tints[color] += 1
            where = tinted.setdefault(color, [])
            if relative not in where:
                where.append(relative)

    for current, dirs, files in os.walk(root):
        here = Path(current)
        if "project.godot" in files:
            info = _project(here / "project.godot")
            addons = here / "addons"
            info["path"] = _rel(here, root)
            info["addons"] = sorted(p.name for p in addons.iterdir()
                                    if p.is_dir()) if addons.is_dir() else []
            godot[info["path"]] = info
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIPPED)
        for name in sorted(files):
            seen += 1
            if seen > limit:
                truncated = True
                break
            path = here / name
            relative = _rel(path, root)
            folder = _rel(here, root) or "."
            suffix = path.suffix.lower()
            dev = _is_dev(relative)
            if suffix in CODE:
                top = relative.split("/", 1)[0] if "/" in relative else "."
                code.setdefault(top, Counter())[CODE[suffix]] += 1
            if suffix == ".tscn":
                base = owner(relative)
                entry: dict[str, Any] = {"path": relative, "dev": dev, "root": "",
                                         "inherits": "", "script": "", "uses": [],
                                         "nodes": 0, "particles": False, "types": [],
                                         "refs": [], "environment": {}, "colors": [],
                                         "styleboxes": 0}
                if path.stat().st_size <= MAX_SCENE_BYTES:
                    entry.update(_scene(path))
                entry["inherits"] = _local(entry["inherits"], base)
                entry["script"] = _local(entry["script"], base)
                entry["uses"] = [_local(used, base) for used in entry["uses"]]
                entry["refs"] = [_local(ref, base) for ref in entry["refs"]]
                entry["level"] = _is_level(relative, entry["types"])
                tint(relative, dev, entry.pop("colors"))
                if entry["environment"] and not dev:
                    environments.append({"path": relative, "props": entry["environment"]})
                scenes.append(entry)
            elif suffix == ".gd":
                found = _script(path)
                found["extends"] = _local(found["extends"], owner(relative))
                text = _read(path)
                base = owner(relative)
                scripts.append({"path": relative, "dev": dev, **found,
                                "refs": sorted({_local(ref, base) for ref in _RES.findall(text)}),
                                "builds": sorted(set(_BUILT.findall(text))),
                                "styleboxes": len(_STYLEBOX_NEW.findall(text))})
                tint(relative, dev, colors(text))
            elif suffix == ".tres":
                found = _tres(path)
                found["refs"] = [_local(ref, owner(relative)) for ref in found["refs"]]
                resources[relative] = found
                if found["type"] == "Theme":
                    themes.append(relative)
                    theme_colors[relative] = found["colors"]
                elif found["type"] == "Environment" and not dev:
                    environments.append({"path": relative, "props": found["environment"]})
            elif suffix in DOCS:
                docs.append({"path": relative, "title": _title(path),
                             "bytes": path.stat().st_size})
            elif suffix in FONTS:
                fonts.append(relative)
            elif suffix in SHADERS:
                found = _shader(path)
                found["refs"] = [_local(ref, owner(relative)) for ref in found["refs"]]
                shaders.append({"path": relative, **found})
            elif suffix in AUDIO:
                audio.append(relative)
            elif suffix in TRANSLATIONS:
                translations.append(relative)
            elif suffix in IMAGES:
                images[folder] += 1
                if not dev and (is_icon(relative) or is_prop(relative)):
                    (icons if is_icon(relative) else props).append(
                        {"path": relative, "folder": folder,
                         "format": suffix.lstrip("."), "size": image_size(path)})
            elif suffix in MESHES:
                meshes[folder] += 1
            elif suffix in DATA:
                data[folder] += 1
        if truncated:
            break

    # An inherited scene takes the root of the scene it inherits: a screen
    # variant stays a screen.
    roots = {scene["path"]: scene["root"] for scene in scenes}
    for scene in scenes:
        parent, hops = scene["inherits"], 0
        while not scene["root"] and parent and hops < 5:
            scene["root"] = roots.get(parent, "")
            parent = next((s["inherits"] for s in scenes if s["path"] == parent), "")
            hops += 1
        scene["role"] = _role(scene["root"])
    # A script often extends another (`extends BasePanel`, or its path): climb
    # up to a Godot type to know whether it makes interface.
    by_path = {script["path"]: script for script in scripts}
    by_class = {script["class_name"]: script for script in scripts if script["class_name"]}
    for script in scripts:
        base, hops = script["extends"], 0
        while hops < 8 and (base in by_path or base in by_class):
            base = (by_path.get(base) or by_class[base])["extends"]
            hops += 1
        script["ui"] = is_ui(base)
        script["base"] = base
    # Who uses a shader: a scene or a script citing it, directly or through a
    # `.tres` material citing it; another shader including it.
    for shader in shaders:
        holders = {path for path, res in resources.items() if shader["path"] in res["refs"]}
        holders.add(shader["path"])
        shader["users"] = sorted(
            [entry["path"] for entry in scenes + scripts
             if not entry["dev"] and holders & set(entry["refs"])]
            + [other["path"] for other in shaders if shader["path"] in other["refs"]])

    # Who uses an image, a scene or a StyleBox: a scene, a script or a resource
    # (a theme, an AtlasTexture) citing it. What nothing cites may be loaded by
    # a path built in code: the map says so, without deciding.
    citing = ([(entry["path"], set(entry["refs"])) for entry in scenes + scripts
               if not entry["dev"]]
              + [(path, set(res["refs"])) for path, res in resources.items()])

    def users(wanted: str) -> list[str]:
        return sorted(path for path, refs in citing if wanted in refs and path != wanted)

    for image in icons + props:
        image["users"] = users(image["path"])
    for scene in scenes:
        scene["users"] = users(scene["path"])
    styleboxes = [{"path": path, "type": res["type"], "users": users(path)}
                  for path, res in sorted(resources.items()) if res["type"] in STYLEBOXES]

    return {
        "root": str(root),
        "godot": [godot[key] for key in sorted(godot)],
        "docs": sorted(docs, key=_docs_order),
        "scenes": scenes,
        "scripts": scripts,
        "themes": themes,
        "fonts": fonts,
        "shaders": shaders,
        "theme_colors": theme_colors,
        "colors": {color: {"count": count, "files": tinted[color]}
                   for color, count in tints.most_common()},
        "environments": environments,
        "audio": audio,
        "translations": translations,
        "images": dict(sorted(images.items())),
        "icons": icons,
        "props": props,
        "styleboxes": styleboxes,
        "meshes": dict(sorted(meshes.items())),
        "data": dict(sorted(data.items())),
        "code": {top: dict(counts.most_common()) for top, counts in sorted(code.items())},
        "files": min(seen, limit),
        "truncated": truncated,
    }


# ------------------------------------------------------------- the written map

# A longer list no longer reads: the agent has the whole map in `game_map`,
# and the folder in front of it.
CAP = 40


def _bullets(lines: list[str], cap: int = CAP) -> str:
    if not lines:
        return "- (none)"
    shown = [f"- {line}" for line in lines[:cap]]
    if len(lines) > cap:
        shown.append(f"- … and {len(lines) - cap} more")
    return "\n".join(shown)


def _counted(counts: dict[str, int]) -> list[str]:
    return [f"`{folder}/` — {count}" for folder, count in counts.items()]


def _godot(game: dict[str, Any]) -> str:
    if not game["godot"]:
        return "- no `project.godot` in the folder"
    lines = []
    for project in game["godot"]:
        where = f"`{project['path'] or '.'}/`"
        name = f" “{project['name']}”" if project["name"] else ""
        version = f", Godot {project['version']}" if project["version"] else ""
        lines.append(f"{where}{name}{version}")
        if project["main_scene"]:
            lines.append(f"  main scene: `{project['main_scene']}`")
        if project["autoloads"]:
            lines.append("  autoloads: " + ", ".join(
                f"{name} (`{path}`)" for name, path in project["autoloads"].items()))
        display = project["display"]
        if display.get("viewport_width") and display.get("viewport_height"):
            lines.append(f"  display: {display['viewport_width']} x "
                         f"{display['viewport_height']} px")
        if project["addons"]:
            lines.append("  addons (third-party code, not surveyed): "
                         + ", ".join(project["addons"]))
    return "\n".join(f"- {line}" if not line.startswith("  ") else line
                     for line in lines)


def _scene_line(scene: dict[str, Any]) -> str:
    kind = scene["root"] or "?"
    if scene["inherits"]:
        kind += f", inherits `{scene['inherits']}`"
    script = f", script `{scene['script']}`" if scene["script"] else ""
    return f"`{scene['path']}` — {kind}{script}"


# Beyond this, a color is no longer a choice: it is a one-off setting.
TOP_COLORS = 24


def _direction(game: dict[str, Any], scenes: list[dict[str, Any]]) -> list[str]:
    """The part of the map serving the art direction.

    What the game already uses, read in its files: the welcome screen, the
    moments it stages, its colors, shaders, light, levels, sounds. What it wants
    the player to feel is written nowhere: that is the question to ask.
    """
    welcome = []
    for project in game["godot"]:
        where = f"`{project['path'] or '.'}/`"
        if project["splash"]:
            welcome.append(f"{where} boot splash: " + ", ".join(
                f"{key} `{value}`" for key, value in project["splash"].items()))
        if project["clear_color"]:
            welcome.append(f"{where} default clear color `{project['clear_color']}`")
    moments = [f"**{label}** — `{s['path']}`" for s in scenes
               for label in [moment(s["path"])] if label]
    moments += [f"**{label}** — sound `{path}`" for path in game["audio"]
                for label in [moment(path)] if label]

    themed = []
    for theme, palette in game["theme_colors"].items():
        shown = ", ".join(f"{key} `{color}`" for key, color in palette[:12])
        more = f", … and {len(palette) - 12} more" if len(palette) > 12 else ""
        themed.append(f"`{theme}` — " + (shown + more if palette else "no color written"))
    written = [f"`{color}` — {entry['count']} times, " + ", ".join(
                   f"`{path}`" for path in entry["files"][:3])
               + (" …" if len(entry["files"]) > 3 else "")
               for color, entry in list(game["colors"].items())[:TOP_COLORS]]

    shaders = []
    for shader in game["shaders"]:
        line = f"`{shader['path']}` — {shader['type'] or '?'}"
        if shader["uniforms"]:
            line += "; settings: " + ", ".join(shader["uniforms"][:8])
            if len(shader["uniforms"]) > 8:
                line += " …"
        users = shader.get("users", [])
        line += ("; used by " + ", ".join(f"`{u}`" for u in users[:4])
                 + (" …" if len(users) > 4 else "")
                 if users else "; nothing cites it literally")
        shaders.append(line)

    environments = [f"`{env['path']}` — " + (", ".join(
                        f"{key} `{value}`" for key, value in env["props"].items())
                        or "default values")
                    for env in game["environments"]]
    lit = [f"`{s['path']}` — " + ", ".join(t for t in s["types"] if t in AMBIANCE)
           for s in scenes if AMBIANCE & set(s["types"])]
    lit += [f"`{s['path']}` — built in code: " + ", ".join(s["builds"])
            for s in game["scripts"] if s["builds"] and not s["dev"]]
    levels = [_scene_line(s) + (", tiles" if LEVEL_TYPES & set(s["types"]) else "")
              for s in scenes if s.get("level")]
    sounds: Counter[str] = Counter(path.rsplit("/", 1)[0] if "/" in path else "."
                                   for path in game["audio"])
    return [
        "### Welcome screen and moments (the game at launch, in play, on victory, "
        "on defeat)",
        _bullets(welcome + moments),
        "### Themes and their colors",
        _bullets(themed),
        "### Fonts",
        _bullets([f"`{f}`" for f in game["fonts"]]),
        f"### Colors written in scenes and scripts (the {TOP_COLORS} most frequent)",
        _bullets(written),
        "### Shaders",
        _bullets(shaders),
        "### Environment and light",
        _bullets(environments + lit),
        "### Levels and places",
        _bullets(levels),
        "### Music and sounds, by folder",
        _bullets(_counted(dict(sorted(sounds.items())))),
        "### Images, by folder",
        _bullets(_counted(game["images"])),
        "### 3D models, by folder",
        _bullets(_counted(game["meshes"])),
    ]


def _cited(users: list[str], nobody: str) -> str:
    """Who cites a file: the first three, then the count."""
    if not users:
        return nobody
    return (", ".join(f"`{user}`" for user in users[:3])
            + (f" +{len(users) - 3}" if len(users) > 3 else ""))


def _image_line(image: dict[str, Any]) -> str:
    size = f" ({image['size']})" if image["size"] else ""
    return (f"`{image['path']}`{size} — "
            + _cited(image["users"], "cited nowhere (path built in code?)"))


def _props(game: dict[str, Any], scenes: list[dict[str, Any]]) -> list[str]:
    """The game's props: components, button scripts, StyleBoxes, textures."""
    ui = [s for s in scenes if s["role"] == "interface"]
    components = [s for s in ui if is_prop_type(s["root"])]
    reused = [s for s in ui if s["users"] and s not in components]
    buttons = [s for s in game["scripts"] if not s["dev"] and s["ui"]
               and is_prop_type(s.get("base", ""))]

    def scene_line(scene: dict[str, Any]) -> str:
        return f"{_scene_line(scene)} — " + _cited(scene["users"], "instanced nowhere")

    return [
        "### Components (scenes rooted in a button, slider, gauge, frame…)",
        _bullets([scene_line(s) for s in components]),
        "### Interface scenes reused by others (prop or panel: to sort out)",
        _bullets([scene_line(s) for s in reused]),
        "### Prop scripts (they extend a button, a slider, a gauge)",
        _bullets([f"`{s['path']}` — extends {s['extends']}"
                  + (f" (class_name {s['class_name']})" if s["class_name"] else "")
                  for s in buttons]),
        "### StyleBox (backgrounds, frames and control states)",
        _bullets([f"`{box['path']}` — {box['type']} — "
                  + _cited(box["users"], "cited nowhere") for box in game["styleboxes"]]
                 + [f"`{s['path']}` — {s['styleboxes']} written in the scene"
                    for s in ui if s["styleboxes"]]
                 + [f"`{s['path']}` — {s['styleboxes']} built by the code"
                    for s in game["scripts"] if not s["dev"] and s["styleboxes"]]),
        "### Themes (they carry each state's StyleBox)",
        _bullets([f"theme `{t}`" for t in game["themes"]]),
        "### Prop textures (buttons, arrows, frames…), and who uses them",
        _bullets([_image_line(image) for image in game["props"]], cap=CAP * 2),
    ]


def _icons(game: dict[str, Any]) -> list[str]:
    """The game's icons: by folder (a folder is often a set), then one by one."""
    icons = game["icons"]
    folders: dict[str, list[dict[str, Any]]] = {}
    for icon in icons:
        folders.setdefault(icon["folder"], []).append(icon)

    def family(folder: str, members: list[dict[str, Any]]) -> str:
        formats = Counter(icon["format"] for icon in members)
        sizes = Counter(icon["size"] for icon in members if icon["size"])
        used = sum(1 for icon in members if icon["users"])
        line = (f"`{folder}/` — {len(members)} icon(s), "
                + ", ".join(f"{fmt} {n}" for fmt, n in formats.most_common()))
        if sizes:
            line += "; sizes " + ", ".join(f"{size} ({n})"
                                           for size, n in sizes.most_common(4))
        return line + f"; {used} cited by the game"

    return [
        "### Icon sets, by folder",
        _bullets([family(folder, members) for folder, members in sorted(folders.items())]),
        "### The icons, and who uses them",
        _bullets([_image_line(icon) for icon in icons], cap=CAP * 2),
        "### Themes (they carry the controls' icons)",
        _bullets([f"theme `{t}`" for t in game["themes"]]),
    ]


def outline(game: dict[str, Any], kind: str) -> str:
    """The part of the map serving a section, in Markdown.

    `kind`: `interface`, `icons`, `props`, `mechanics`, `direction`, `vfx` or
    `world`. The Godot projects and the game's docs always come first: they are
    the two ways in.
    """
    scenes = [s for s in game["scenes"] if not s["dev"]]
    dev = [s for s in game["scenes"] if s["dev"]]
    parts = ["### The Godot projects", _godot(game),
             "### The game's docs (read in place, never copied)",
             _bullets([f"`{d['path']}` — {d['title']}" for d in game["docs"]])]

    if kind == "interface":
        screens = [s for s in scenes if s["role"] == "interface"]
        attached = {used for s in game["scenes"] for used in s["uses"]}
        coded = [s for s in game["scripts"] if s["ui"] and not s["dev"]
                 and s["path"] not in attached]
        inputs = sorted({name for p in game["godot"] for name in p["inputs"]})
        languages: Counter[str] = Counter(
            t.rsplit("/", 1)[0] if "/" in t else "." for t in game["translations"])
        parts += [
            "### Screens and panels (scenes with an interface root)",
            _bullets([_scene_line(s) for s in screens]),
            "### Interface built in code (interface scripts no scene uses)",
            _bullets([f"`{s['path']}` — extends {s['extends']}" for s in coded]),
            "### Themes, fonts, translations",
            _bullets([f"theme `{t}`" for t in game["themes"]]
                     + [f"font `{f}`" for f in game["fonts"]]
                     + [f"translations `{folder}/` — {count} file(s)"
                        for folder, count in sorted(languages.items())]),
            "### Declared inputs",
            _bullets([", ".join(inputs)] if inputs else []),
        ]
        dev_screens = [s for s in dev if s["role"] == "interface"]
        if dev_screens:
            parts += ["### Set aside: development interface scenes",
                      _bullets([_scene_line(s) for s in dev_screens], cap=10)]
    elif kind == "icons":
        parts += _icons(game)
    elif kind == "props":
        parts += _props(game, scenes)
    elif kind == "mechanics":
        by_folder: Counter[str] = Counter(s["path"].rsplit("/", 1)[0] if "/" in s["path"]
                                          else "." for s in game["scripts"] if not s["dev"])
        parts += [
            "### GDScript scripts, by folder",
            _bullets(_counted(dict(sorted(by_folder.items())))),
            "### The code, by folder and language",
            _bullets([f"`{top}/` — " + ", ".join(f"{lang} {n}" for lang, n in langs.items())
                      for top, langs in game["code"].items()]),
            "### Data (settings, tables, balancing), by folder",
            _bullets(_counted(game["data"])),
            "### Logic scenes (Node root)",
            _bullets([_scene_line(s) for s in scenes if s["role"] == "logic"]),
        ]
    elif kind == "direction":
        parts += _direction(game, scenes)
    elif kind == "vfx":
        effect_dirs = {folder: count for folder, count in game["images"].items()
                       if any(word in folder.lower()
                              for word in ("vfx", "fx", "effect", "particle"))}
        parts += [
            "### Particle scenes",
            _bullets([_scene_line(s) for s in scenes if s["particles"]]),
            "### Shaders",
            _bullets([f"`{s['path']}`" for s in game["shaders"]]),
            "### Effect images, by folder",
            _bullets(_counted(effect_dirs)),
        ]
    else:
        parts += [
            "### Object and entity scenes (2D or 3D root)",
            _bullets([_scene_line(s) for s in scenes if s["role"] == "object"]),
            "### Data, by folder",
            _bullets(_counted(game["data"])),
            "### 3D models, by folder",
            _bullets(_counted(game["meshes"])),
            "### Images, by folder",
            _bullets(_counted(game["images"])),
        ]
    if game["truncated"]:
        parts.append(f"_Map truncated at {game['files']} files: walk the rest of the "
                     "folder by hand._")
    return "\n\n".join(parts)
