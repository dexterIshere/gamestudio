"""The screen editor: an element picked on the render, its property rewritten on a branch.

What these tests protect: a scene read and rewritten only changes the touched
property; editing lives on its branch, in its own checkout, and the user's
working copy does not move; each edit is a commit that can be undone; and what
cannot be written (a node created by code, a property outside the list, a game
without git) is refused with its reason. The render is simulated: Godot is not
required.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gamestudio.config import Settings
from gamestudio.godot.tscn_edit import Scene, SceneEditError, decode, encode
from gamestudio.service import build, documents, folders, renders, screens, using
from gamestudio.service.errors import ServiceError

SCENE = """[gd_scene load_steps=2 format=3 uid="uid://screen"]

[ext_resource type="Script" path="res://hud.gd" id="1_s"]

[node name="Hud" type="MarginContainer"]
script = ExtResource("1_s")
theme_override_constants/margin_left = 8

[node name="Gold" type="Label" parent="."]
text = "Gold:
plenty"
theme_override_font_sizes/font_size = 17

[node name="Button" type="Button" parent="."]
text = "Play"

[connection signal="pressed" from="Button" to="." method="_on_pressed"]
"""

PROJECT_GODOT = """config_version=5

[application]

config/name="Trial"

[display]

window/size/viewport_width=720
window/size/viewport_height=1280
"""


# ------------------------------------------------------------------ the scene


def test_a_scene_read_back_rewrites_identically() -> None:
    scene = Scene(SCENE)
    assert scene.text() == SCENE
    assert set(scene.nodes()) == {".", "Gold", "Button"}
    assert scene.read("Gold", "text") == "Gold:\nplenty", "a text on two lines"
    assert scene.read("Gold", "theme_override_font_sizes/font_size") == 17
    assert scene.read(".", "script") == "res://hud.gd"


def test_an_edit_only_touches_its_property() -> None:
    scene = Scene(SCENE)
    scene.write("Gold", "text", 'Gold "pure"', "text")
    scene.write("Gold", "theme_override_colors/font_color", "#ff8800", "color")
    scene.write("Button", "custom_minimum_size", [120, 48], "vector2")

    text = scene.text()
    assert 'text = "Gold \\"pure\\""' in text and "plenty" not in text, \
        "the two-line value is replaced as a whole"
    assert "theme_override_colors/font_color = Color(1, 0.5333, 0, 1)" in text
    assert "custom_minimum_size = Vector2(120, 48)" in text.split('[node name="Button"')[1]
    reread = Scene(text)
    assert reread.read("Gold", "text") == 'Gold "pure"'
    assert reread.read("Gold", "theme_override_colors/font_color") == "#ff8800ff"
    assert text.endswith('[connection signal="pressed" from="Button" to="." '
                         'method="_on_pressed"]\n'), "the rest of the file is intact"


def test_an_image_adds_its_resource_only_once() -> None:
    scene = Scene(SCENE)
    scene.write("Button", "icon", "res://icons/coin.svg", "texture")
    scene.write("Button", "icon", "res://icons/coin.svg", "texture")
    text = scene.text()
    assert text.count('path="res://icons/coin.svg"') == 1
    assert "load_steps=3" in text
    assert 'icon = ExtResource("2_studio")' in text
    assert Scene(text).read("Button", "icon") == "res://icons/coin.svg"


def test_a_removed_value_goes_back_to_the_default() -> None:
    scene = Scene(SCENE)
    scene.write("Gold", "theme_override_font_sizes/font_size", None, "int")
    assert scene.read("Gold", "theme_override_font_sizes/font_size") is None
    assert scene.read("Gold", "text") == "Gold:\nplenty"


@pytest.mark.parametrize(("value", "kind"), [
    ("green", "color"), ([1], "vector2"), ("yes", "bool"), ("x", "int"),
    # Godot writes neither, and `int(inf)` would raise a studio error.
    (float("inf"), "float"), (float("nan"), "float"), ("inf", "int"),
    ([0, float("-inf")], "vector2")])
def test_a_malformed_value_is_refused(value: Any, kind: str) -> None:
    with pytest.raises(SceneEditError):
        encode(value, kind)


@pytest.mark.parametrize("path", [
    'res://a.svg" type="Script" path="res://pirate.gd',
    "res://a.svg\n[node name=\"Pirate\" type=\"Node\"]",
    "res://a\\b.svg", "res://a\rb.svg"])
def test_a_resource_path_adds_nothing_to_the_scene(path: str) -> None:
    """The path is written between quotes in a header: it must not escape them."""
    scene = Scene(SCENE)
    with pytest.raises(SceneEditError, match="invalid resource path"):
        scene.write("Button", "icon", path, "texture")
    assert scene.text() == SCENE


def test_an_unknown_value_stays_raw() -> None:
    assert decode("PackedStringArray(\"a\")") == "PackedStringArray(\"a\")"
    with pytest.raises(SceneEditError, match="not found"):
        Scene(SCENE).write("Missing", "text", "x", "text")


# ------------------------------------------------------------------ the editor


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


# What the simulated engine drew: the checkout and the scene of each render.
RENDERS: list[dict[str, Any]] = []


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path,
         monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A game under git, opened as a project, and a simulated engine that surveys the screen."""
    root = tmp_path / "game"
    (root / "client").mkdir(parents=True)
    (root / "client/project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
    (root / "client/hud.tscn").write_text(SCENE, encoding="utf-8")
    (root / "client/hud.gd").write_text("extends MarginContainer\n", encoding="utf-8")
    _git(root, "init", "--quiet", "--initial-branch=main")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "game")

    surveyed = [
        {"path": ".", "name": "Hud", "type": "MarginContainer", "rect": [0, 0, 1440, 200],
         "file": "res://hud.tscn", "local": ".", "instance": False, "text": ""},
        {"path": "Gold", "name": "Gold", "type": "Label", "rect": [16, 0, 200, 60],
         "file": "res://hud.tscn", "local": "Gold", "instance": False, "text": "Gold"},
        {"path": "Counter", "name": "Counter", "type": "Label", "rect": [0, 0, 1, 1],
         "file": "", "local": "", "instance": False, "text": "3"},
    ]
    RENDERS.clear()

    def draw(target: dict[str, Any], output: Path, *, nodes: Path | None = None,
             **settings: Any) -> dict[str, Any]:
        RENDERS.append({"base": target["base"], "scene": target["res_path"],
                        "settings": settings})
        output.write_bytes(b"\x89PNG\r\n\x1a\n")
        if nodes is not None:
            nodes.write_text(json.dumps(surveyed), encoding="utf-8")
        return {"engine": "simulated", "width": 1440, "height": 2560, "notes": []}

    monkeypatch.setattr(renders, "render_png", draw)
    settings = Settings(data_dir=isolated_data, project_root=tmp_path / "studio",
                        context_dir=tmp_path / "context")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        documents.create_document("game", "Hud", "interface", "design/interface")
        yield root


FOLDER = "design/interface"


def test_the_screen_opens_on_its_branch_without_touching_the_copy(game: Path) -> None:
    state = screens.open_screen("game", FOLDER, "hud", "client/hud.tscn")

    assert state["branch"] == "studio/screen-hud" and state["opened"]
    assert _git(game, "branch", "--show-current") == "main", "the copy stays on its branch"
    checkout = Path(state["checkout"])
    assert checkout.is_relative_to(game / ".gamestudio" / "workspace")
    assert RENDERS[-1]["base"] == checkout / "client", "the branch is what gets drawn"
    assert [n["editable"] for n in state["nodes"]] == [True, True, False]
    assert {"file": "client/hud.tscn", "root": "MarginContainer"} in state["scenes"]

    properties = screens.node_properties("game", FOLDER, "hud", "Gold")
    values = {p["key"]: p.get("value") for p in properties["properties"]}
    assert values["text"] == "Gold:\nplenty"
    assert values["theme_override_colors/font_color"] is None


def test_an_edit_is_a_branch_commit_that_can_be_undone(game: Path) -> None:
    screens.open_screen("game", FOLDER, "hud", "client/hud.tscn")
    state = screens.edit("game", FOLDER, "hud", "Gold", {"text": "Coins",
                                                         "theme_override_font_sizes/font_size": 24})

    assert state["changed"]
    [commit] = state["commits"]
    assert commit["studio"] and commit["subject"].startswith("Screen hud : Gold")
    checkout = Path(state["checkout"])
    assert 'text = "Coins"' in (checkout / "client/hud.tscn").read_text(encoding="utf-8")
    assert (game / "client/hud.tscn").read_text(encoding="utf-8") == SCENE, \
        "the user's scene does not move"

    state = screens.undo("game", FOLDER, "hud")
    assert state["commits"] == []
    assert (checkout / "client/hud.tscn").read_text(encoding="utf-8") == SCENE
    with pytest.raises(ServiceError, match="nothing to undo"):
        screens.undo("game", FOLDER, "hud")


def test_what_cannot_be_written_is_refused(game: Path) -> None:
    screens.open_screen("game", FOLDER, "hud", "client/hud.tscn")
    with pytest.raises(ServiceError, match="created by code"):
        screens.edit("game", FOLDER, "hud", "Counter", {"text": "4"})
    with pytest.raises(ServiceError, match="not editable"):
        screens.edit("game", FOLDER, "hud", "Gold", {"script": "res://other.gd"})
    with pytest.raises(ServiceError, match="color expected"):
        screens.edit("game", FOLDER, "hud", "Gold", {"theme_override_colors/font_color": "red"})
    assert screens.state("game", FOLDER, "hud")["commits"] == []


def test_a_scene_that_no_longer_draws_is_put_back(game: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    screens.open_screen("game", FOLDER, "hud", "client/hud.tscn")

    def broken(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise ServiceError("render failed")

    monkeypatch.setattr(renders, "render_png", broken)
    with pytest.raises(ServiceError, match="change undone"):
        screens.edit("game", FOLDER, "hud", "Gold", {"text": "Coins"})
    assert screens.state("game", FOLDER, "hud")["commits"] == []


def test_a_prop_is_edited_alone_on_its_branch(game: Path) -> None:
    """A prop named like a screen has its own branch, its own checkout, and renders cropped."""
    documents.create_document("game", "Hud", "prop", "design/props")
    state = screens.open_screen("game", "design/props", "hud", "client/hud.tscn")
    assert state["branch"] == "studio/prop-hud"
    assert RENDERS[-1]["settings"] == {"crop": True, "transparent": True}
    state = screens.edit("game", "design/props", "hud", "Gold", {"text": "Fine gold"})
    assert state["commits"][0]["subject"].startswith("Prop hud : Gold")
    assert screens.state("game", FOLDER, "hud")["opened"] is False, \
        "the screen with the same name is not touched"
    with pytest.raises(ServiceError, match="cannot be edited"):
        screens.state("game", "design/mechanics", "hud")


def test_a_game_without_git_has_no_editor(tmp_path: Path, isolated_data: Path) -> None:
    root = tmp_path / "no-git"
    root.mkdir()
    (root / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
    (root / "hud.tscn").write_text(SCENE, encoding="utf-8")
    settings = Settings(data_dir=isolated_data, project_root=tmp_path / "studio",
                        context_dir=tmp_path / "context")
    with using(build(settings)):
        folders.open_folder(str(root), "bare")
        documents.create_document("bare", "Hud", "interface", FOLDER)
        with pytest.raises(ServiceError, match="not a git repository"):
            screens.open_screen("bare", FOLDER, "hud", "hud.tscn")
