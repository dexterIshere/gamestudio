"""A game's survey: the map of its folder, and the brief that catches a section up.

What these tests protect: the map reads a game without running anything, and
without going down into what is not the game; a section is named the way one
says it ("UI", "Buildings", or the French words a user may type); and the brief
carries what is needed to bring the section back in line with the game -- what
it already says, what the game contains, the procedure.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio import mcp_server
from gamestudio.config import Settings
from gamestudio.service import build, documents, folders, handoff, survey, using, world
from gamestudio.service.errors import ServiceError


def _game(root: Path) -> Path:
    """A small game: a Godot client in `client/`, a server, docs, noise."""

    def write(relative: str, text: str = "") -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write("README.md", "# My game\n\nA 4X.\n")
    write("docs/GDD.md", "# Game design document\n")
    write("client/project.godot", "\n".join([
        "config_version=5", "", "[application]", "", 'config/name="My Game"',
        'run/main_scene="res://scenes/main.tscn"',
        'config/features=PackedStringArray("4.7", "Mobile")', "", "[autoload]", "",
        'Api="*res://scripts/api.gd"', "", "[display]", "",
        "window/size/viewport_width=720", "window/size/viewport_height=1280", "",
        "[input]", "", "zoom_in={", '"deadzone": 0.2,', '"events": []', "}", ""]))
    write("client/scenes/hud/top_bar.tscn", "\n".join([
        '[gd_scene load_steps=2 format=3 uid="uid://a"]', "",
        '[ext_resource type="Script" path="res://scenes/hud/top_bar.gd" id="1_s"]',
        '[ext_resource type="Texture2D" path="res://assets/icons/coin.svg" id="2_i"]',
        '[ext_resource type="PackedScene" path="res://ui/big_button.tscn" id="3_b"]', "",
        '[node name="TopBar" type="MarginContainer"]', 'script = ExtResource("1_s")', "",
        '[node name="Gold" type="Label" parent="."]', 'text = "Gold"', ""]))
    write("client/scenes/hud/top_bar.gd", "extends MarginContainer\n")
    write("client/scenes/hud/top_bar_compact.tscn", "\n".join([
        "[gd_scene load_steps=2 format=3]", "",
        '[ext_resource type="PackedScene" path="res://scenes/hud/top_bar.tscn" id="1_b"]',
        "", '[node name="TopBarCompact" instance=ExtResource("1_b")]', ""]))
    write("client/scenes/main.tscn", "\n".join([
        "[gd_scene format=3]", "", '[node name="Main" type="Node3D"]', "",
        '[node name="Stars" type="GPUParticles3D" parent="."]', ""]))
    write("client/scripts/base_panel.gd", "class_name BasePanel extends PanelContainer\n")
    write("client/scripts/settings_panel.gd", "extends BasePanel\n")
    write("client/scripts/api.gd", "extends Node\n")
    write("client/style/theme.tres", '[gd_resource type="Theme" format=3]\n')
    write("client/assets/fonts/Prompt.ttf")
    # Icons: one cited by the bar, the other loaded by a path built in code.
    write("client/assets/icons/coin.svg",
          '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0"/></svg>')
    write("client/assets/icons/gem.svg",
          '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"></svg>')
    write("client/assets/silicon/wafer.svg", "<svg/>")
    # Props: a button the bar instances, its script, its StyleBox, an arrow.
    write("client/ui/big_button.tscn", "\n".join([
        "[gd_scene format=3]", "",
        '[ext_resource type="Script" path="res://ui/big_button.gd" id="1_b"]', "",
        '[sub_resource type="StyleBoxFlat" id="StyleBoxFlat_1"]', "bg_color = Color(1, 1, 1, 1)",
        "", '[node name="Big" type="Button"]', 'script = ExtResource("1_b")', ""]))
    write("client/ui/big_button.gd", "class_name BigButton extends Button\n"
          "func _ready():\n\tadd_theme_stylebox_override('normal', StyleBoxFlat.new())\n")
    write("client/ui/panel_frame.tres", '[gd_resource type="StyleBoxTexture" format=3]\n')
    write("client/assets/arrows/next.png", "")
    write("client/locale/game.fr.po", 'msgid ""\n')
    write("client/dev/sandbox.tscn", '[gd_scene format=3]\n\n[node name="Box" type="Control"]\n')
    write("client/addons/dialogues/plugin.cfg", "[plugin]\n")
    write("client/addons/dialogues/panel.tscn",
          '[gd_scene format=3]\n\n[node name="P" type="Control"]\n')
    write("server/src/main.rs", "fn main() {}\n")
    # What is not the game: the history, a build, an agent's working copy.
    write(".git/notes.md", "# not the game\n")
    write("target/debug/build.rs")
    write(".claude/worktrees/w/README.md", "# a copy\n")
    return root


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    """A project opened on the folder of a game already under way."""
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    settings = Settings(data_dir=isolated_data, project_root=studio_dir,
                        context_dir=tmp_path / "context")
    root = _game(tmp_path / "my-game")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root


def test_the_map_reads_the_game_without_running_anything(tmp_path: Path) -> None:
    found = survey.game_map(_game(tmp_path / "game"))

    [client] = found["godot"]
    assert (client["path"], client["name"], client["version"]) == ("client", "My Game", "4.7")
    assert client["main_scene"] == "res://scenes/main.tscn"
    assert client["autoloads"] == {"Api": "res://scripts/api.gd"}
    assert client["inputs"] == ["zoom_in"]
    assert client["addons"] == ["dialogues"], "an addon is named, not surveyed"

    scenes = {scene["path"]: scene for scene in found["scenes"]}
    bar = scenes["client/scenes/hud/top_bar.tscn"]
    assert (bar["root"], bar["role"]) == ("MarginContainer", "interface")
    assert bar["script"] == "client/scenes/hud/top_bar.gd", "cited from the game root"
    assert scenes["client/scenes/hud/top_bar_compact.tscn"]["role"] == "interface", \
        "an inherited scene stays a screen"
    assert scenes["client/scenes/main.tscn"]["particles"] is True
    assert scenes["client/dev/sandbox.tscn"]["dev"] is True
    assert "client/addons/dialogues/panel.tscn" not in scenes

    scripts = {script["path"]: script for script in found["scripts"]}
    assert scripts["client/scripts/settings_panel.gd"]["ui"] is True, \
        "the type is found through class_name"
    assert scripts["client/scripts/api.gd"]["ui"] is False
    assert found["themes"] == ["client/style/theme.tres"]
    assert found["fonts"] == ["client/assets/fonts/Prompt.ttf"]
    assert found["code"]["server"] == {"Rust": 1}
    assert [doc["path"] for doc in found["docs"]] == ["README.md", "docs/GDD.md"], \
        "neither the history, nor the build, nor an agent's copy"


def test_the_map_surveys_what_the_game_conveys(tmp_path: Path) -> None:
    """The art direction reads from the files: colors, shaders, light, levels,
    sounds, and the moments the game stages -- without running anything."""
    root = tmp_path / "game"

    def write(relative: str, text: str = "") -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write("project.godot", "\n".join([
        "[application]", 'config/name="Game"', 'boot_splash/bg_color=Color(0, 0, 0, 1)', "",
        "[rendering]", "environment/defaults/default_clear_color=Color(0.05, 0.09, 0.16, 1)"]))
    write("style/theme.tres", "\n".join([
        '[gd_resource type="Theme" load_steps=2 format=3]', "",
        '[sub_resource type="StyleBoxFlat" id="bg"]', "bg_color = Color(1, 0.8, 0.4, 1)", "",
        "[resource]", 'Panel/styles/panel = SubResource("bg")',
        "Label/colors/font_color = Color(1, 1, 1, 1)"]))
    write("shaders/water.gdshader", "\n".join([
        "shader_type spatial;", '#include "res://shaders/common.gdshaderinc"',
        "uniform vec3 deep : source_color = vec3(0.0, 0.1, 0.3);",
        "uniform float waves;"]))
    write("shaders/common.gdshaderinc", "float soften(float x) { return x; }\n")
    write("materials/water.tres", "\n".join([
        '[gd_resource type="ShaderMaterial" load_steps=2 format=3]', "",
        '[ext_resource type="Shader" path="res://shaders/water.gdshader" id="1"]']))
    write("levels/island.tscn", "\n".join([
        "[gd_scene format=3]", "",
        '[ext_resource type="Material" path="res://materials/water.tres" id="1"]', "",
        '[sub_resource type="Environment" id="env"]', "fog_enabled = true",
        "fog_light_color = Color(0.5, 0.6, 0.7, 1)", "glow_enabled = true",
        "ssil_enabled = true", "",
        '[node name="Island" type="Node3D"]', "",
        '[node name="Ground" type="GridMap" parent="."]', "",
        '[node name="Sun" type="DirectionalLight3D" parent="."]']))
    write("ui/game_over.tscn", "\n".join([
        "[gd_scene format=3]", "", '[node name="End" type="Control"]',
        "modulate = Color(1, 0.2, 0.2, 1)"]))
    write("scripts/sky.gd", "\n".join([
        "extends Node3D", 'const SKY = preload("res://shaders/water.gdshader")',
        "func _ready():", "\tvar env = Environment.new()",
        '\tmodulate = Color("#ff3333")', "\tvar tint = Color8(255, 51, 51)"]))
    write("audio/victory_fanfare.ogg")

    found = survey.game_map(root)
    [project] = found["godot"]
    assert project["clear_color"] == "#0d1729"
    assert project["splash"] == {"bg_color": "#000000"}
    assert found["theme_colors"]["style/theme.tres"] == [
        ("Panel/styles/panel.bg_color", "#ffcc66"), ("Label/colors/font_color", "#ffffff")]
    assert found["colors"]["#ff3333"] == {"count": 3,
                                          "files": ["scripts/sky.gd", "ui/game_over.tscn"]}
    water, common = (next(s for s in found["shaders"] if s["path"] == path)
                     for path in ("shaders/water.gdshader", "shaders/common.gdshaderinc"))
    assert water["type"] == "spatial"
    assert water["uniforms"] == ["deep (vec3, source_color) = vec3(0.0, 0.1, 0.3)",
                                 "waves (float)"]
    assert water["users"] == ["levels/island.tscn", "scripts/sky.gd"], \
        "through a material, and through the code that loads it"
    assert (common["type"], common["users"]) == ("include", ["shaders/water.gdshader"])
    [env] = found["environments"]
    assert env == {"path": "levels/island.tscn", "props": {
        "fog_enabled": "true", "fog_light_color": "#8099b2", "glow_enabled": "true"}}

    outline = survey.outline(found, "direction")
    assert "**defeat** — `ui/game_over.tscn`" in outline
    assert "**victory** — sound `audio/victory_fanfare.ogg`" in outline
    assert "`levels/island.tscn` — Node3D, tiles" in outline
    assert "`levels/island.tscn` — DirectionalLight3D" in outline
    assert "`scripts/sky.gd` — built in code: Environment" in outline
    assert "`audio/` — 1" in outline


@pytest.mark.parametrize(("path", "expected"), [
    ("ui/main_menu.tscn", "launch"), ("ui/GameOver.tscn", "defeat"),
    ("ui/game_over.tscn", "defeat"), ("ui/window.tscn", ""),
    ("sfx/win.ogg", "victory"),
    # A game may name its files in French.
    ("ui/ecran_titre.tscn", "launch")])
def test_a_moment_is_recognized_by_its_name(path: str, expected: str) -> None:
    assert survey.moment(path) == expected


def test_the_brief_catches_the_section_up_with_the_game(game: Path) -> None:
    documents.create_document("game", "Main screen", "interface", "design/interface")

    sent = handoff.shelf_brief("game", "Interface")

    assert sent["folder"] == "design/interface"
    assert sent["documents"] == ["main-screen"]
    assert Path(sent["path"]).name == "section-interface.md"
    text = Path(sent["path"]).read_text(encoding="utf-8")
    assert text == sent["text"]
    # What the section already says: the user's intent, to keep.
    assert "main-screen.md" in text
    # The map: the game's screens, the interface made in code, the game's docs.
    assert "`client/scenes/hud/top_bar.tscn` — MarginContainer" in text
    assert "`client/scripts/settings_panel.gd` — extends BasePanel" in text
    assert "`docs/GDD.md` — Game design document" in text
    assert "client/dev/sandbox.tscn" in text.split("Set aside")[1]
    # The procedure, and what guards it.
    assert "## Game survey — " in text
    assert "read-only" in text
    assert 'template="interface"' in text
    assert "game-survey" in text
    # A single line is enough to hand it over: it points to the brief.
    assert "\n" not in sent["prompt"] and sent["path"] in sent["prompt"]


def test_icons_have_their_section(game: Path) -> None:
    """An icon set is surveyed apart from the interface: size, format, who cites it."""
    found = survey.game_map(game)
    icons = {icon["path"]: icon for icon in found["icons"]}
    assert set(icons) == {"client/assets/icons/coin.svg", "client/assets/icons/gem.svg"}, \
        "a whole word names an icon: `silicon/` is not one"
    assert icons["client/assets/icons/coin.svg"]["size"] == "24x24"
    assert icons["client/assets/icons/gem.svg"]["size"] == "48x48"
    assert icons["client/assets/icons/coin.svg"]["users"] == ["client/scenes/hud/top_bar.tscn"]

    assert handoff.catchup_target("game", "icons")["folder"] == "design/icons"
    text = handoff.shelf_brief("game", "Icons")["text"]
    assert 'template="icon"' in text
    assert "`client/assets/icons/` — 2 icon(s), svg 2" in text
    assert "`client/assets/icons/gem.svg` (48x48) — cited nowhere" in text


def test_props_have_their_section(game: Path) -> None:
    """Buttons, arrows, frames: their scenes, scripts, StyleBoxes, textures."""
    found = survey.game_map(game)
    assert [image["path"] for image in found["props"]] == ["client/assets/arrows/next.png"], \
        "an arrow is a prop; an icon stays an icon"
    assert [box["path"] for box in found["styleboxes"]] == ["client/ui/panel_frame.tres"]

    assert handoff.catchup_target("game", "boutons")["folder"] == "design/props"
    text = handoff.shelf_brief("game", "Props")["text"]
    assert 'template="prop"' in text
    components = text.split("### Components")[1].split("###")[0]
    assert "`client/ui/big_button.tscn` — Button, script `client/ui/big_button.gd` — " \
        "`client/scenes/hud/top_bar.tscn`" in components
    assert "`client/ui/big_button.gd` — extends Button (class_name BigButton)" in text
    assert "`client/ui/big_button.tscn` — 1 written in the scene" in text
    assert "`client/ui/big_button.gd` — 1 built by the code" in text
    assert "`client/assets/arrows/next.png` — cited nowhere" in text


@pytest.mark.parametrize("said", ["interface", "Interface", "UI", "design/interface",
                                  "écrans"])
def test_a_section_is_named_the_way_one_says_it(game: Path, said: str) -> None:
    assert handoff.catchup_target("game", said)["folder"] == "design/interface"


def test_world_sections_and_the_ones_not_caught_up(game: Path) -> None:
    world.create_section("game", "Bâtiments")
    target = handoff.catchup_target("game", "bâtiments")
    assert (target["folder"], target["template"], target["kind"]) == \
        ("world/batiments", "card", "world")
    assert handoff.catchup_target("game", "Mécaniques")["template"] == "mechanic"
    with pytest.raises(ServiceError, match="the user's writing"):
        handoff.catchup_target("game", "notes")
    with pytest.raises(ServiceError, match="Bâtiments"):
        handoff.catchup_target("game", "inventory")


def test_a_project_without_a_folder_has_nothing_to_survey(tmp_path: Path,
                                                          isolated_data: Path) -> None:
    settings = Settings(data_dir=isolated_data, project_root=tmp_path / "studio",
                        context_dir=tmp_path / "context")
    with using(build(settings)), pytest.raises(ServiceError, match="has no game folder"):
        handoff.shelf_brief("trial", "interface")


def test_every_cli_connected_to_the_studio_gets_the_instruction() -> None:
    """The phrase "update a section" is understood everywhere: the server
    instructions, which every CLI receives, name the tool that does it.

    Claude Code cuts these instructions at 2048 characters without warning: an
    instruction placed beyond does not exist for it.
    """
    visible = mcp_server.INSTRUCTIONS[:2048]
    assert visible == mcp_server.INSTRUCTIONS, "beyond 2048 characters, everything is lost"
    assert "shelf_update_brief" in visible
    assert (mcp_server.shelf_update_brief.__doc__ or "").strip()
