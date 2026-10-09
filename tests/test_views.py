"""The game's views: its main pages, found in its files, each drawn once.

What these tests protect: the main scene comes first; a scene a script loads
as a whole is a view, a screen or a world; a scene placed inside another one,
a widget, a developer's scene or one nothing opens is not; a view takes the
title of the interface card citing it; its image is drawn once and drawn again
when its scene changes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from gamestudio.config import Settings
from gamestudio.service import build, documents, folders, renders, using, views
from gamestudio.service.errors import NotFound


def _game(root: Path) -> Path:
    def write(relative: str, text: str = "") -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    write("client/project.godot", "\n".join([
        "config_version=5", "", "[application]", 'config/name="Trial"',
        'run/main_scene="res://scenes/main.tscn"', "",
        "[display]", "window/size/viewport_width=720", "window/size/viewport_height=1280"]))
    write("client/scenes/main.tscn", '[gd_scene format=3]\n\n[node name="Main" type="Node3D"]\n')
    write("client/scripts/main.gd", "\n".join([
        "extends Node3D",
        'const BOOT := preload("res://scenes/boot.tscn")',
        'const ROW := preload("res://scenes/hud/build_row.tscn")',
        'func open_menu() -> void:',
        '\tget_tree().change_scene_to_file("res://scenes/menu.tscn")',
        'func visit() -> void:',
        '\tget_tree().change_scene_to_file("res://scenes/galaxy.tscn")']))
    write("client/scenes/boot.tscn",
          '[gd_scene format=3]\n\n[node name="Boot" type="CanvasLayer"]\n')
    write("client/scenes/galaxy.tscn",
          '[gd_scene format=3]\n\n[node name="Galaxy" type="Node3D"]\n')
    write("client/scenes/hud/build_row.tscn",
          '[gd_scene format=3]\n\n[node name="Row" type="Control"]\n')
    write("client/scenes/hud/top_panel.tscn",
          '[gd_scene format=3]\n\n[node name="Top" type="PanelContainer"]\n')
    write("client/scenes/menu.tscn", "\n".join([
        "[gd_scene load_steps=2 format=3]", "",
        '[ext_resource type="PackedScene" path="res://scenes/hud/top_panel.tscn" id="1"]', "",
        '[node name="Menu" type="Control"]', "",
        '[node name="Top" parent="." instance=ExtResource("1")]', ""]))
    write("client/scenes/unused.tscn", '[gd_scene format=3]\n\n[node name="U" type="Control"]\n')
    write("client/dev/sandbox.tscn", '[gd_scene format=3]\n\n[node name="S" type="Control"]\n')
    write("client/dev/try.gd", 'extends Node\nconst S := preload("res://dev/sandbox.tscn")\n')
    return root


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    settings = Settings(data_dir=isolated_data, project_root=studio_dir,
                        context_dir=tmp_path / "context")
    root = _game(tmp_path / "my-game")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root


def test_the_views_are_the_pages_a_game_opens(game: Path) -> None:
    documents.write_document("game", "main-menu", "# Main menu\n\nSee `res://scenes/menu.tscn`.\n",
                             folder="design/interface")
    found = {view["id"]: view for view in views.views("game")["views"]}
    assert list(found) == ["main", "boot", "menu", "galaxy"], \
        "the start page first, then the screens, then the worlds"
    main, menu = found["main"], found["menu"]
    assert (main["entry"], main["kind"], main["res_path"]) == (True, "world-3d",
                                                              "res://scenes/main.tscn")
    assert (menu["kind"], menu["opened_from"]) == ("screen", ["client/scripts/main.gd"])
    assert menu["title"] == "Main menu" and menu["card"] == {"name": "main-menu",
                                                             "title": "Main menu"}
    assert found["boot"]["title"] == "Boot", "without a card, the file's name"
    documents.write_document("game", "start", "# Start\n\n`boot.tscn` then `main.tscn`.\n",
                             folder="design/interface")
    titles = {view["id"]: view["title"] for view in views.views("game")["views"]}
    assert (titles["boot"], titles["main"]) == ("Boot", "Start"), \
        "a card describing several views names only the start page"
    assert "top_panel" not in found, "a piece placed in a scene is not a page"
    assert "build_row" not in found, "nor a widget a script loads"
    assert "unused" not in found and "sandbox" not in found


def test_a_view_is_drawn_once_until_its_scene_changes(game: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    drawn: list[dict] = []

    def render_png(target, output: Path, **settings) -> dict:
        drawn.append({"scene": target["res_path"], **settings})
        Image.new("RGB", (54, 96), "navy").save(output)
        return {"engine": "fake", "width": 54, "height": 96, "notes": []}

    monkeypatch.setattr(renders, "render_png", render_png)
    first = views.image("game", "menu")
    assert first.is_file() and len(drawn) == 1
    assert drawn[0]["scene"] == "res://scenes/menu.tscn"
    assert drawn[0]["scale"] == pytest.approx(0.75), "a 960-pixel side for a 1280-pixel game"
    assert views.image("game", "menu") == first and len(drawn) == 1, "kept"

    scene = game / "client" / "scenes" / "menu.tscn"
    later = scene.stat().st_mtime_ns + 10_000_000_000
    os.utime(scene, ns=(later, later))
    views.image("game", "menu")
    assert len(drawn) == 2, "drawn again once its scene changed"
    with pytest.raises(NotFound):
        views.image("game", "nothing")
