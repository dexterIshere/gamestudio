"""A game's typography, read from its files: faces, resources, default font, sizes, texts.

What these tests protect: a font file is described by its own tables -- family,
style, weight, italic, glyph count -- and, when they cannot be read, by its
name; a Godot font resource gives its base, its fallbacks (system names
included) and its OpenType features as tags; the project's default font is
found and its file marked; the sizes come from scenes, scripts and themes; the
game's texts become specimens, without translation keys, paths or texts
without letters; a face nothing cites says so.
"""

from __future__ import annotations

import struct
from pathlib import Path

from gamestudio.service import fonts, survey


def _font(family: str, style: str, weight: int, *, italic: bool = False,
          glyphs: int = 321) -> bytes:
    """A minimal OpenType file: just the tables the reader looks at."""
    names = [(1, family), (2, style)]
    records, strings = b"", b""
    for name_id, text in names:
        raw = text.encode("utf-16-be")
        records += struct.pack(">HHHHHH", 3, 1, 0x409, name_id, len(raw), len(strings))
        strings += raw
    name = struct.pack(">HHH", 0, len(names), 6 + len(records)) + records + strings
    os2 = bytearray(78)
    os2[4:6] = struct.pack(">H", weight)
    os2[62:64] = struct.pack(">H", 1 if italic else 0)
    maxp = struct.pack(">IH", 0x00005000, glyphs)
    head = bytearray(54)
    tables = {"OS/2": bytes(os2), "head": bytes(head), "maxp": maxp, "name": name}
    offset = 12 + 16 * len(tables)
    directory, body = b"", b""
    for tag, data in tables.items():
        directory += tag.encode("latin-1") + struct.pack(">III", 0, offset + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    return struct.pack(">IHHHH", 0x00010000, len(tables), 0, 0, 0) + directory + body


def _game(root: Path) -> Path:
    def write(relative: str, content: str | bytes = "") -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    write("client/project.godot", "\n".join([
        "config_version=5", "", "[application]", 'config/name="Trial"', "",
        "[gui]", "", 'theme/custom_font="res://fonts/ui_font.tres"', ""]))
    write("client/fonts/Sail-Medium.ttf", _font("Sail", "Medium", 500))
    write("client/fonts/Sail-BoldItalic.ttf", _font("Sail", "Bold Italic", 700, italic=True))
    write("client/fonts/Beacon.woff2", b"wOF2 not readable here")
    write("client/fonts/ui_font.tres", "\n".join([
        '[gd_resource type="FontVariation" load_steps=3 format=3]', "",
        '[ext_resource type="FontFile" path="res://fonts/Sail-Medium.ttf" id="base"]', "",
        '[sub_resource type="SystemFont" id="system"]',
        'font_names = PackedStringArray("Noto Sans CJK", "sans-serif")', "",
        "[resource]",
        'fallbacks = Array[Font]([SubResource("system")])',
        'base_font = ExtResource("base")',
        "opentype_features = {",
        "1667329140: 0",
        "}",
        "spacing_glyph = 1", ""]))
    write("client/ui/bar.tscn", "\n".join([
        "[gd_scene format=3]", "",
        '[node name="Bar" type="Control"]', "",
        '[node name="Title" type="Label" parent="."]',
        'text = "Found my colony"',
        "theme_override_font_sizes/font_size = 18", "",
        '[node name="Count" type="Label" parent="."]',
        'text = "Found my colony"', "",
        '[node name="Key" type="Label" parent="."]',
        'text = "UI_MENU_TITLE"', "",
        '[node name="Digits" type="Label" parent="."]',
        'text = "1 350"', ""]))
    write("client/ui/hud.gd", "\n".join([
        "extends Control", "func _ready() -> void:",
        '\t$Label.add_theme_font_size_override("font_size", 14)']))
    write("client/dev/sandbox.tscn", "\n".join([
        "[gd_scene format=3]", "", '[node name="Sandbox" type="Label"]',
        'text = "Developer only"', "theme_override_font_sizes/font_size = 99"]))
    return root


def test_a_font_file_is_read_from_its_tables(tmp_path: Path) -> None:
    path = tmp_path / "anything.ttf"
    path.write_bytes(_font("Sail", "Bold Italic", 700, italic=True, glyphs=812))
    assert fonts.face(path) == {"family": "Sail", "style": "Bold Italic", "weight": 700,
                                "italic": True, "glyphs": 812, "format": "ttf"}


def test_a_font_without_readable_tables_is_read_from_its_name(tmp_path: Path) -> None:
    path = tmp_path / "Beacon-SemiBold.woff2"
    path.write_bytes(b"wOF2 compressed")
    found = fonts.face(path)
    assert (found["family"], found["style"], found["weight"]) == ("Beacon", "SemiBold", 600)
    assert found["glyphs"] is None and found["format"] == "woff2"


def test_the_typography_of_a_game(tmp_path: Path) -> None:
    root = _game(tmp_path / "game")
    typography = fonts.typography(root, survey.game_map(root))

    assert typography["default"] == {"resource": "client/fonts/ui_font.tres",
                                     "base": "client/fonts/Sail-Medium.ttf",
                                     "family": "Sail", "size": None}
    [resource] = typography["resources"]
    assert resource["type"] == "FontVariation"
    assert resource["base"] == "client/fonts/Sail-Medium.ttf"
    assert resource["fallbacks"] == ["Noto Sans CJK", "sans-serif"]
    assert resource["features"] == [{"tag": "calt", "value": 0}], "a feature reads as its tag"
    assert resource["settings"] == {"spacing_glyph": "1"}

    families = {family["family"]: family["faces"] for family in typography["families"]}
    assert list(families) == ["Sail", "Beacon"], "the default font's family first"
    medium, bold = families["Sail"]
    assert (medium["weight"], medium["default"]) == (500, True)
    assert (bold["weight"], bold["italic"], bold["default"], bold["uses"]) == (700, True, False, 0)

    assert [(size["size"], size["count"]) for size in typography["sizes"]] == [(14, 1), (18, 1)], \
        "scenes and scripts; never a developer scene"
    assert typography["samples"] == ["Found my colony"], \
        "the game's texts; neither a translation key nor a text without letters"
