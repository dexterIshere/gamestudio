"""A game's renders: a scene drawn by its engine, off screen, cited by a card.

What these tests protect: a scene is named the way an agent cites it (from the
game root or as `res://`), the virtual display comes before the window, the
latest render of a name is the one the library shows, and a card can only show
an image from its own project. The last test makes a real render, when Godot
and a virtual display are available.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from gamestudio.config import Settings
from gamestudio.service import build, documents, folders, renders, using
from gamestudio.service.context import space
from gamestudio.service.errors import NotFound, ServiceError

PROJECT_GODOT = """config_version=5

[application]

config/name="Trial"
run/main_scene="res://ui.tscn"

[display]

window/size/viewport_width=720
window/size/viewport_height=1280
window/stretch/mode="canvas_items"
"""

# A screen: a background of a plain color, found again in the render.
UI_SCENE = """[gd_scene format=3]

[node name="Screen" type="Control"]
layout_mode = 3
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0

[node name="Background" type="ColorRect" parent="."]
layout_mode = 1
anchors_preset = 15
anchor_right = 1.0
anchor_bottom = 1.0
color = Color(1, 0, 0, 1)
"""


def _game(root: Path, godot: str = "client") -> Path:
    base = root / godot if godot else root
    (base / "scenes").mkdir(parents=True)
    (base / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
    (base / "ui.tscn").write_text(UI_SCENE, encoding="utf-8")
    (base / "scenes" / "panel.tscn").write_text(UI_SCENE, encoding="utf-8")
    (base / "notes.txt").write_text("not a scene\n", encoding="utf-8")
    return root


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    """A project opened on a game folder, with its Godot client in `client/`."""
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    settings = Settings(data_dir=isolated_data, project_root=studio_dir,
                        context_dir=tmp_path / "context")
    root = _game(tmp_path / "my-game")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root


def test_a_scene_is_named_the_way_an_agent_cites_it(game: Path) -> None:
    from_the_game = renders.resolve_scene("game", "client/scenes/panel.tscn")
    assert from_the_game["res_path"] == "res://scenes/panel.tscn"
    assert from_the_game["local"] == "client/scenes/panel.tscn"
    assert renders.resolve_scene("game", "res://scenes/panel.tscn")["local"] == \
        "client/scenes/panel.tscn"
    # Without a scene: the game as it starts.
    assert renders.resolve_scene("game")["res_path"] == "res://ui.tscn"


def test_a_scene_outside_the_project_or_not_a_scene_is_refused(game: Path) -> None:
    with pytest.raises(NotFound):
        renders.resolve_scene("game", "res://../../etc/passwd")
    with pytest.raises(NotFound):
        renders.resolve_scene("game", "server/main.tscn")
    with pytest.raises(ServiceError, match="not a Godot scene"):
        renders.resolve_scene("game", "client/notes.txt")


def test_the_virtual_display_comes_before_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renders.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name == "gamescope" else None)
    found = renders.displays(720, 1280)
    assert [name for name, _ in found] == ["gamescope", "window"]
    assert found[0][1][:3] == ["gamescope", "--backend", "headless"]
    monkeypatch.setattr(renders.shutil, "which", lambda name: None)
    assert [name for name, _ in renders.displays(720, 1280)] == ["window"]


def test_the_setup_becomes_a_function_body() -> None:
    script = renders.setup_script('scene.open_page("empire")\nawait scene.get_tree().process_frame')
    assert script.startswith("extends RefCounted")
    assert "func setup(scene: Node) -> void:\n\tscene.open_page" in script
    assert "\tawait scene.get_tree().process_frame" in script
    assert renders.setup_script("  \n").endswith("\tpass\n")


def test_the_latest_render_of_a_name_is_the_one_the_library_shows(game: Path) -> None:
    st = space("game")
    ids = []
    for day, color in (("2026-10-01", "red"), ("2026-10-05", "blue")):
        image = game / f"{color}.png"
        Image.new("RGB", (8, 8), color).save(image)
        asset = st.store.put_file(image, kind="image", meta={
            "role": renders.ROLE, "project": "game", "name": "top-bar",
            "scene": "res://ui.tscn", "rendered_at": f"{day}T10:00:00+00:00"})
        st.db.save_asset(asset)
        ids.append(asset.id)

    index = st.librarian.index_project("game")
    folder = index.folder("renders")
    assert folder is not None
    assert [(f.name, f.asset_id) for f in folder.files] == [("top-bar.png", ids[1])]
    assert folder.documents["renders.json"]["renders"][0]["scene"] == "res://ui.tscn"


def test_a_brief_image_is_filed_under_its_section(game: Path) -> None:
    """The image illustrating cards lives with them, not with free renders.

    Three cases: the render carrying its section's tag, one made before that
    filing -- recognized because a card carries its name --, and the render
    nothing claims, which stays a free image.
    """
    st = space("game")
    section = documents.directory("game", "design/interface")
    section.mkdir(parents=True, exist_ok=True)
    (section / "top-bar.md").write_text("# Top bar\n", encoding="utf-8")
    (section / "world-list.md").write_text("# World list\n", encoding="utf-8")

    for name, tag, color in (("top-bar", "briefing/design/interface", "red"),
                             ("world-list", "", "green"),
                             ("free-screen", "", "blue")):
        image = game / f"{name}.png"
        # One color per render: the store is content-addressed, and two
        # identical images would make a single asset -- hence a single name.
        Image.new("RGB", (8, 8), color).save(image)
        st.db.save_asset(st.store.put_file(image, kind="image", meta={
            "role": renders.ROLE, "project": "game", "name": name, "briefing": tag,
            "scene": "res://ui.tscn", "rendered_at": "2026-10-05T10:00:00+00:00"}))

    index = st.librarian.index_project("game")
    brief = index.folder("briefing/design/interface")
    assert brief is not None, "a brief image has its section folder"
    assert [f.name for f in brief.files] == ["top-bar.png", "world-list.png"]
    assert brief.documents["renders.json"]["renders"][0]["scene"] == "res://ui.tscn"
    assert [f.name for f in index.folder("renders").files] == ["free-screen.png"]


def test_a_card_finds_its_image_filed_elsewhere(game: Path) -> None:
    """The filing changes, the card's citation does not: it still resolves."""
    st = space("game")
    section = documents.directory("game", "design/interface")
    section.mkdir(parents=True, exist_ok=True)
    (section / "planet-visit.md").write_text("# Planet visit\n", encoding="utf-8")
    image = game / "planet-visit.png"
    Image.new("RGB", (8, 8), "red").save(image)
    st.db.save_asset(st.store.put_file(image, kind="image", meta={
        "role": renders.ROLE, "project": "game", "name": "planet-visit",
        "rendered_at": "2026-10-05T10:00:00+00:00"}))

    st.librarian.sync_project("game")
    filed = st.librarian.project_dir("game") / "briefing/design/interface/planet-visit.png"
    assert filed.is_file(), "the render followed its section"

    # What the card cites: the path from before the filing.
    cited = documents.image_file("game", "design/interface",
                                 "../../../library/renders/planet-visit.png")
    assert cited == filed.resolve()


def test_a_card_only_shows_an_image_from_its_project(game: Path) -> None:
    render = game / ".gamestudio" / "library" / "renders" / "screen.png"
    render.parent.mkdir(parents=True)
    Image.new("RGB", (4, 4), "white").save(render)
    (game / "secret.txt").write_text("no\n", encoding="utf-8")

    found = documents.image_file("game", "design/interface",
                                 "../../../library/renders/screen.png")
    assert found == render.resolve()
    for refused in ("../../../../secret.txt",                 # not an image
                    "../../../../../../../etc/hostname.png",  # outside the project
                    "https://example.org/x.png",              # the network
                    "../../../library/renders/missing.png"):  # nothing there
        with pytest.raises(NotFound):
            documents.image_file("game", "design/interface", refused)


@pytest.mark.skipif(shutil.which("godot") is None or not (shutil.which("gamescope")
                                                          or shutil.which("xvfb-run")),
                    reason="needs Godot and a virtual display (gamescope, xvfb-run)")
def test_a_real_render_by_the_engine(game: Path) -> None:
    render = renders.render_scene("game", "client/scenes/panel.tscn", name="Panel",
                                  folder="design/interface", scale=0.5, delay=0)

    assert render["engine"] != "window", "no window must open"
    assert (render["width"], render["height"]) == (360, 640)
    assert render["library"] == "renders/panel.png"
    assert render["markdown"] == "![panel](../../../library/renders/panel.png)"
    path = Path(render["path"])
    with Image.open(path) as image:
        assert image.size == (360, 640)
        assert image.convert("RGB").getpixel((180, 320)) == (255, 0, 0), \
            "the scene's red background is in the image: the engine drew it"
    # The card cites the image by the path the render gave.
    assert documents.image_file("game", "design/interface",
                                "../../../library/renders/panel.png") == path.resolve()
