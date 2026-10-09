"""A game's typography: its font files, its font resources, the sizes and the texts its scenes use.

Read without running anything and without a font library. A font file's own
tables (`name`, `OS/2`, `maxp`, `head`) give its family, its style, its weight
and its glyph count; a Godot font resource (`FontVariation`, `SystemFont`,
`FontFile` saved as `.tres`) gives its base file, its fallbacks and its
OpenType features; `project.godot` names the font every Control uses by
default; scenes and scripts give the sizes they set and the texts they show --
the game's own words, which the art direction sets in its type.
"""

from __future__ import annotations

import re
import struct
from collections import Counter
from pathlib import Path
from typing import Any

from . import survey

# Font files are read whole to find their tables; past this, it is not a font.
MAX_FONT_BYTES = 40 * 1024 * 1024
# The game's own texts offered as specimens: enough to choose from.
MAX_SAMPLES = 24
MAX_SAMPLE_LENGTH = 48
FONT_TYPES = frozenset({"FontVariation", "SystemFont", "FontFile"})

# The usual style words of a font name, from the most specific: `SemiBold`
# before `Bold`, `ExtraLight` before `Light`.
_WEIGHT_WORDS = (
    ("extralight", 200), ("ultralight", 200), ("semibold", 600), ("demibold", 600),
    ("extrabold", 800), ("ultrabold", 800), ("hairline", 100), ("thin", 100),
    ("light", 300), ("regular", 400), ("book", 400), ("normal", 400), ("medium", 500),
    ("bold", 700), ("black", 900), ("heavy", 900),
)
# The OpenType versions a font file may start with: TrueType, CFF, Apple.
_SFNT = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")

_RESOURCE = re.compile(r"^\[resource\]\s*$", re.M)
_BASE = re.compile(r'^base_font\s*=\s*ExtResource\(\s*"([^"]+)"\s*\)', re.M)
_FALLBACKS = re.compile(r"^fallbacks\s*=\s*(.+)$", re.M)
_REF = re.compile(r'(Sub|Ext)Resource\(\s*"([^"]+)"\s*\)')
_NAMES = re.compile(r"^font_names\s*=\s*PackedStringArray\(([^)]*)\)", re.M)
_FEATURES = re.compile(r"^opentype_features\s*=\s*\{([^}]*)\}", re.M)
_PAIR = re.compile(r"(\d+)\s*:\s*(-?\d+)")
_SETTING = re.compile(
    r"^(variation_embolden|variation_face_index|spacing_glyph|spacing_space|spacing_top|"
    r"spacing_bottom|baseline_offset|font_weight|font_stretch|font_italic|antialiasing|"
    r"hinting|subpixel_positioning|multichannel_signed_distance_field|oversampling)"
    r"\s*=\s*(.+)$", re.M)
_QUOTED = re.compile(r'"([^"]*)"')
_CUSTOM_FONT = re.compile(r'^theme/custom_font\s*=\s*"([^"]+)"', re.M)
_CUSTOM_THEME = re.compile(r'^theme/custom\s*=\s*"([^"]+)"', re.M)
_THEME_FONT = re.compile(r'^default_font\s*=\s*ExtResource\(\s*"([^"]+)"\s*\)', re.M)
_SIZE_SCENE = re.compile(
    r"^(?:theme_override_font_sizes/\w+|font_size|\w+/font_sizes/\w+|default_font_size)"
    r"\s*=\s*(\d+)\s*$", re.M)
_SIZE_SCRIPT = re.compile(
    r'add_theme_font_size_override\(\s*"[^"]*"\s*,\s*(\d+)\s*\)|\bfont_size\s*:?=\s*(\d+)\b')
_TEXT = re.compile(r'^text\s*=\s*"((?:[^"\\]|\\.)*)"\s*$', re.M)
_LETTER = re.compile(r"[^\W\d_]")
_KEY = re.compile(r"^[A-Z0-9_.]+$")


# --------------------------------------------------------------- a font file


def _tables(data: bytes) -> dict[str, bytes]:
    """The tables of an OpenType font, or of the first font of a collection."""
    offset = 0
    if data[:4] == b"ttcf" and len(data) >= 16:
        offset = struct.unpack(">I", data[12:16])[0]
    if data[offset:offset + 4] not in _SFNT or len(data) < offset + 12:
        return {}
    count = struct.unpack(">H", data[offset + 4:offset + 6])[0]
    tables: dict[str, bytes] = {}
    for index in range(count):
        record = offset + 12 + 16 * index
        if record + 16 > len(data):
            break
        tag = data[record:record + 4].decode("latin-1")
        start, length = struct.unpack(">II", data[record + 8:record + 16])
        if start + length <= len(data):
            tables[tag] = data[start:start + length]
    return tables


def _names(table: bytes) -> dict[int, str]:
    """Family (1, 16) and style (2, 17) names, in English when the font has them."""
    if len(table) < 6:
        return {}
    count, strings = struct.unpack(">HH", table[2:6])
    best: dict[int, tuple[int, str]] = {}
    for index in range(count):
        record = 6 + 12 * index
        if record + 12 > len(table):
            break
        platform, _, language, name_id, length, start = struct.unpack(
            ">HHHHHH", table[record:record + 12])
        if name_id not in (1, 2, 16, 17):
            continue
        raw = table[strings + start:strings + start + length]
        if platform in (0, 3):
            text, rank = raw.decode("utf-16-be", errors="replace"), \
                0 if platform == 3 and language == 0x409 else 1
        elif platform == 1:
            text, rank = raw.decode("mac_roman", errors="replace"), 2
        else:
            continue
        if text.strip() and (name_id not in best or rank < best[name_id][0]):
            best[name_id] = (rank, text.strip())
    return {name_id: text for name_id, (_, text) in best.items()}


def _weight_of(words: str) -> int:
    folded = words.lower().replace(" ", "").replace("-", "").replace("_", "")
    return next((weight for word, weight in _WEIGHT_WORDS if word in folded), 0)


def face(path: Path) -> dict[str, Any]:
    """What a font file says about itself: family, style, weight, italic, glyph count.

    A file the tables do not describe (WOFF, a bitmap font) is described by
    its name: `Prompt-SemiBold.ttf` is Prompt, SemiBold, 600.
    """
    stem = path.stem
    family, _, style = stem.partition("-")
    found: dict[str, Any] = {"family": family or stem, "style": style or "Regular",
                             "weight": 0, "italic": False, "glyphs": None,
                             "format": path.suffix.lower().lstrip(".")}
    try:
        data = path.read_bytes() if path.stat().st_size <= MAX_FONT_BYTES else b""
    except OSError:
        data = b""
    tables = _tables(data)
    names = _names(tables.get("name", b""))
    if names:
        found["family"] = names.get(16) or names.get(1) or found["family"]
        found["style"] = names.get(17) or names.get(2) or found["style"]
    os2 = tables.get("OS/2", b"")
    if len(os2) >= 64:
        found["weight"] = struct.unpack(">H", os2[4:6])[0]
        found["italic"] = bool(struct.unpack(">H", os2[62:64])[0] & 1)
    head = tables.get("head", b"")
    if len(head) >= 46 and struct.unpack(">H", head[44:46])[0] & 2:
        found["italic"] = True
    maxp = tables.get("maxp", b"")
    if len(maxp) >= 6:
        found["glyphs"] = struct.unpack(">H", maxp[4:6])[0]
    if not 1 <= found["weight"] <= 1000:
        found["weight"] = _weight_of(found["style"]) or 400
    if not found["italic"]:
        found["italic"] = any(word in found["style"].lower() for word in ("italic", "oblique"))
    return found


# --------------------------------------------------------- the font resources


def _local(res_path: str, godot: str) -> str:
    inner = res_path.removeprefix("res://")
    return f"{godot}/{inner}" if godot else inner


def _tag(number: int) -> str:
    """An OpenType tag stored as a number (`1667329140` is `calt`)."""
    tag = struct.pack(">I", number & 0xFFFFFFFF).decode("latin-1")
    return tag if tag.isprintable() and tag.strip() else str(number)


def _resource(text: str, kind: str, relative: str, godot: str) -> dict[str, Any]:
    """What a font resource sets: its base, its fallbacks, its features and settings."""
    ext: dict[str, str] = {}
    subs: dict[str, str] = {}
    block = ""
    for line in text.splitlines():
        if line.startswith("[ext_resource"):
            attrs = survey._attrs(line)
            if "id" in attrs and "path" in attrs:
                ext[attrs["id"]] = _local(attrs["path"], godot)
        elif line.startswith("[sub_resource"):
            attrs = survey._attrs(line)
            block = attrs.get("id", "")
            subs[block] = ""
        elif line.startswith("["):
            block = ""
        elif block:
            subs[block] += line + "\n"
    head = _RESOURCE.search(text)
    main = text[head.end():] if head else ""
    found: dict[str, Any] = {"file": relative, "type": kind, "base": "", "fallbacks": [],
                             "system": [], "features": [], "settings": {}}
    base = _BASE.search(main)
    if base:
        found["base"] = ext.get(base.group(1), "")
    fallbacks = _FALLBACKS.search(main)
    for source, ref in _REF.findall(fallbacks.group(1) if fallbacks else ""):
        if source == "Ext":
            found["fallbacks"].append(ext.get(ref, ref))
        else:
            names = _NAMES.search(subs.get(ref, ""))
            found["fallbacks"] += _QUOTED.findall(names.group(1)) if names else []
    names = _NAMES.search(main)
    if names:
        found["system"] = _QUOTED.findall(names.group(1))
    features = _FEATURES.search(main)
    if features:
        found["features"] = [{"tag": _tag(int(number)), "value": int(value)}
                             for number, value in _PAIR.findall(features.group(1))]
    found["settings"] = {key: value.strip() for key, value in _SETTING.findall(main)}
    return found


def _godot_files(root: Path, game: dict[str, Any], suffix: str) -> list[tuple[Path, str]]:
    """The files of a kind in each Godot project of the game, with the project's path."""
    found: list[tuple[Path, str]] = []
    for entry in game["godot"]:
        base = root / entry["path"] if entry["path"] else root
        for path in sorted(base.rglob(f"*{suffix}")):
            parts = path.relative_to(base).parts
            if any(part.startswith(".") or part == "addons" or part in survey.SKIPPED
                   for part in parts):
                continue
            found.append((path, entry["path"]))
    return found


# ------------------------------------------------------------- the typography


def typography(root: Path, game: dict[str, Any]) -> dict[str, Any]:
    """The game's typography: families and faces, resources, default font, sizes, texts."""
    resources: list[dict[str, Any]] = []
    themes: dict[str, str] = {}
    for path, godot in _godot_files(root, game, ".tres"):
        if path.stat().st_size > survey.MAX_SCENE_BYTES:
            continue
        kind = survey._resource_type(path)
        relative = path.relative_to(root).as_posix()
        if kind in FONT_TYPES:
            text = path.read_text(encoding="utf-8", errors="replace")
            resources.append(_resource(text, kind, relative, godot))
        elif kind == "Theme":
            themes[relative] = path.read_text(encoding="utf-8", errors="replace")
    by_file = {resource["file"]: resource for resource in resources}

    # The font every Control uses unless told otherwise: the project's custom
    # font, else the default font of its custom theme.
    default: dict[str, Any] = {"resource": "", "base": "", "family": "", "size": None}
    for entry in game["godot"]:
        base = root / entry["path"] if entry["path"] else root
        try:
            settings = (base / "project.godot").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        custom = _CUSTOM_FONT.search(settings)
        theme = _CUSTOM_THEME.search(settings)
        if custom:
            default["resource"] = _local(custom.group(1), entry["path"])
        if theme and not default["resource"]:
            text = themes.get(_local(theme.group(1), entry["path"]), "")
            font = _THEME_FONT.search(text)
            if font:
                ids = {attrs["id"]: _local(attrs["path"], entry["path"])
                       for attrs in (survey._attrs(line) for line in text.splitlines()
                                     if line.startswith("[ext_resource"))
                       if "id" in attrs and "path" in attrs}
                default["resource"] = ids.get(font.group(1), "")
            size = re.search(r"^default_font_size\s*=\s*(\d+)", text, re.M)
            default["size"] = int(size.group(1)) if size else None
        if default["resource"]:
            break
    held = by_file.get(default["resource"])
    default["base"] = held["base"] if held else (
        default["resource"] if default["resource"] in game["fonts"] else "")

    # Who cites each font file: a scene or a script directly, or through a
    # resource built on it.
    citing = [set(entry["refs"]) for entry in game["scenes"] + game["scripts"]
              if not entry["dev"]]
    faces: list[dict[str, Any]] = []
    for file in game["fonts"]:
        info = face(root / file)
        holders = {file} | {resource["file"] for resource in resources
                            if resource["base"] == file or file in resource["fallbacks"]}
        info.update({"file": file, "uses": sum(1 for refs in citing if refs & holders),
                     "default": file == default["base"]})
        faces.append(info)
        if file == default["base"]:
            default["family"] = info["family"]
    families: dict[str, list[dict[str, Any]]] = {}
    for info in sorted(faces, key=lambda entry: (entry["weight"], entry["italic"])):
        families.setdefault(info["family"], []).append(info)

    # The sizes the game sets, and the texts it shows.
    sizes: Counter[int] = Counter()
    sized: dict[int, list[str]] = {}
    samples: Counter[str] = Counter()
    order: dict[str, int] = {}
    for entry, pattern in ([(scene, _SIZE_SCENE) for scene in game["scenes"]]
                           + [(script, _SIZE_SCRIPT) for script in game["scripts"]]):
        if entry["dev"]:
            continue
        path = root / entry["path"]
        try:
            if path.stat().st_size > survey.MAX_SCENE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in pattern.finditer(text):
            size = int(next(group for group in match.groups() if group))
            if 4 <= size <= 512:
                sizes[size] += 1
                where = sized.setdefault(size, [])
                if entry["path"] not in where:
                    where.append(entry["path"])
        if pattern is _SIZE_SCENE:
            for raw in _TEXT.findall(text):
                sample = " ".join(raw.replace("\\n", " ").replace('\\"', '"')
                                  .replace("\\\\", "\\").split())
                if (2 <= len(sample) <= MAX_SAMPLE_LENGTH and _LETTER.search(sample)
                        and not _KEY.match(sample) and "res://" not in sample):
                    samples[sample] += 1
                    order.setdefault(sample, len(order))
    for resource in themes.values():
        for match in _SIZE_SCENE.finditer(resource):
            sizes[int(match.group(1))] += 1

    return {
        "families": [{"family": family, "faces": members}
                     for family, members in sorted(
                         families.items(), key=lambda item: (
                             not any(member["default"] for member in item[1]), item[0]))],
        "resources": resources,
        "default": default,
        "sizes": [{"size": size, "count": count, "files": sized.get(size, [])}
                  for size, count in sorted(sizes.items())],
        "samples": sorted(samples, key=lambda text: (-samples[text], order[text]))[:MAX_SAMPLES],
    }
