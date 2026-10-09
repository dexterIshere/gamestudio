"""A game's colors, read from its files: by aspect of the art direction, and where.

What these tests protect: every literal color is found -- in scenes, scripts,
resources and the `source_color` settings of shaders --; each use names its
file and what carries the color (a property, a theme key, a constant, a shader
setting); the aspect follows what paints it: a Control, a theme or a
canvas_item shader paints the interface, a 3D object or a spatial shader the
materials, an environment or a sky shader the sky, the rest is elsewhere; a
ShaderMaterial takes its shader's aspect and an include that of its hosts; a
developer's folder is left out.
"""

from __future__ import annotations

from pathlib import Path

from gamestudio.service import colors, survey


def _game(root: Path) -> Path:
    def write(relative: str, text: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write("client/project.godot", 'config_version=5\n\n[application]\nconfig/name="Trial"\n')
    write("client/shaders/water.gdshader", "\n".join([
        "shader_type spatial;",
        "uniform vec3 deep : source_color = vec3(0.0, 0.2, 0.4);",
        "uniform vec3 direction = vec3(1.0, 0.0, 0.0);", ""]))
    write("client/shaders/night.gdshader", "\n".join([
        "shader_type sky;",
        "uniform vec4 zenith : source_color = vec4(0.0, 0.0, 0.2, 1.0);", ""]))
    write("client/shaders/common.gdshaderinc",
          "uniform vec4 rim : source_color = vec4(1.0);\n")
    write("client/shaders/button.gdshader", "\n".join([
        "shader_type canvas_item;", '#include "res://shaders/common.gdshaderinc"', ""]))
    write("client/style/theme.tres", "\n".join([
        '[gd_resource type="Theme" load_steps=2 format=3]', "",
        '[sub_resource type="StyleBoxFlat" id="panel"]', "bg_color = Color(0, 0, 0, 1)", "",
        "[resource]", "Button/colors/font_color = Color(1, 1, 1, 1)", ""]))
    write("client/style/water.tres", "\n".join([
        '[gd_resource type="ShaderMaterial" load_steps=2 format=3]', "",
        '[ext_resource type="Shader" path="res://shaders/water.gdshader" id="1"]', "",
        "[resource]", 'shader = ExtResource("1")',
        "shader_parameter/deep = Color(0, 0.4, 0.4, 1)", ""]))
    write("client/world/night.tres", "\n".join([
        '[gd_resource type="Environment" format=3]', "", "[resource]",
        "ambient_light_color = Color(0.2, 0.2, 0.4, 1)", ""]))
    write("client/ui/hud.tscn", "\n".join([
        "[gd_scene format=3]", "",
        '[node name="Hud" type="Control"]', "",
        '[node name="Title" type="Label" parent="."]',
        "theme_override_colors/font_color = Color(1, 0.8, 0.4, 1)", "",
        '[node name="Marker" type="Label3D" parent="."]',
        "modulate = Color(0, 1, 0, 1)", ""]))
    write("client/ui/hud.gd", "\n".join([
        "extends Control", "func _ready() -> void:",
        '\t$Title.add_theme_color_override("font_color", Color(1, 0.8, 0.4))', ""]))
    write("client/world/planet.gd", "\n".join([
        "extends Node3D", "func _ready() -> void:",
        "\t$Sun.light_color = Color(1, 0.8, 0.4)",
        "\tenvironment.ambient_light_color = Color(0.2, 0.2, 0.4)", ""]))
    write("client/data/biomes.gd", "\n".join([
        "extends RefCounted", "const COLORS := {", '\t"plains": Color("#88aa44"),', "}", ""]))
    write("client/dev/sandbox.gd", 'extends Control\nconst DEBUG := Color("#ff00ff")\n')
    return root


def test_the_colors_of_a_game_by_aspect(tmp_path: Path) -> None:
    root = _game(tmp_path / "game")
    palette = colors.palette(root, survey.game_map(root))
    by_hex = {color["hex"]: color for color in palette["colors"]}

    def uses(hex_: str) -> set[tuple[str, str, tuple[str, ...]]]:
        return {(use["file"], use["aspect"], tuple(use["names"])) for use in by_hex[hex_]["uses"]}

    assert uses("#ffcc66") == {
        ("client/ui/hud.tscn", "interface", ("font_color",)),
        ("client/ui/hud.gd", "interface", ("font_color",)),
        ("client/world/planet.gd", "materials", ("light_color",)),
    }, "the same color, for the interface and for the 3D world"
    assert by_hex["#ffcc66"]["aspects"] == {"interface": 2, "materials": 1}
    assert uses("#00ff00") == {("client/ui/hud.tscn", "materials", ("modulate",))}, \
        "a 3D node in an interface scene paints the world"
    assert uses("#333366") == {("client/world/night.tres", "sky", ("ambient_light_color",)),
                               ("client/world/planet.gd", "sky", ("ambient_light_color",))}, \
        "an environment, and the ambient light a world script sets"
    assert uses("#000000") == {("client/style/theme.tres", "interface", ("bg_color",))}
    assert ("client/style/theme.tres", "interface", ("Button/colors/font_color",)) \
        in uses("#ffffff"), "a theme color is named by its key"
    assert ("client/shaders/common.gdshaderinc", "interface", ("rim",)) in uses("#ffffff"), \
        "an include paints where the shaders including it do"
    assert uses("#006666") == {("client/style/water.tres", "materials", ("deep",))}, \
        "a material takes the aspect of its shader"
    assert uses("#003366") == {("client/shaders/water.gdshader", "materials", ("deep",))}, \
        "a shader's color setting, never a vector that is not a color"
    assert uses("#000033") == {("client/shaders/night.gdshader", "sky", ("zenith",))}
    assert uses("#88aa44") == {("client/data/biomes.gd", "other", ("plains",))}
    assert "#ff00ff" not in by_hex, "a developer's folder is not the game"
    assert palette["distinct"] == len(palette["colors"])
    assert palette["total"] == sum(color["count"] for color in palette["colors"])
    assert palette["colors"][0]["hex"] == "#ffcc66", "the most used first"
