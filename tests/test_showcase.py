"""The icons and props showcase: the game's elements, shown alone, to be judged.

What these tests protect: an icon is its file, filed with its family; a prop is
a component or a scene others instance -- never a whole screen --, drawn in each
of its states if it has a button, and a state the theme does not distinguish is
reported as identical; a drawing is kept as long as the scene does not change;
no verdict is held -- an element is discussed with an agent, whose brief carries
the element, its images and its family. The engine is simulated: Godot is not
required.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from gamestudio.config import Settings
from gamestudio.service import build, documents, folders, handoff, renders, showcase, using
from gamestudio.service.errors import NotFound

PROJECT_GODOT = """config_version=5

[application]

config/name="Showcase"

[display]

window/size/viewport_width=720
window/size/viewport_height=1280
"""


def _write(root: Path, relative: str, text: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _game(root: Path) -> Path:
    """A game: two icons, a button, a row, a screen, a StyleBox, an arrow."""
    _write(root, "project.godot", PROJECT_GODOT)
    _write(root, "icons/coin.svg",
           '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"></svg>')
    _write(root, "icons/gem.svg",
           '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"></svg>')
    _write(root, "ui/big_button.tscn", "\n".join([
        "[gd_scene format=3]", "", '[node name="Big" type="Button"]', 'text = "Play"', ""]))
    _write(root, "ui/row.tscn", "\n".join([
        "[gd_scene format=3]", "", '[node name="Row" type="HBoxContainer"]', "",
        '[node name="Name" type="Label" parent="."]', 'text = "Iron"', ""]))
    # A whole screen, instanced by the HUD: not a prop.
    _write(root, "ui/inventory.tscn", "\n".join([
        "[gd_scene format=3]", "", '[node name="Inventory" type="Control"]',
        "anchors_preset = 15", "anchor_right = 1.0", "anchor_bottom = 1.0", ""]))
    _write(root, "ui/hud.tscn", "\n".join([
        "[gd_scene format=3]", "",
        '[ext_resource type="PackedScene" path="res://ui/row.tscn" id="1_r"]',
        '[ext_resource type="PackedScene" path="res://ui/inventory.tscn" id="2_i"]',
        '[ext_resource type="Texture2D" path="res://icons/coin.svg" id="3_c"]', "",
        '[node name="Hud" type="Control"]', "anchors_preset = 15", "",
        '[node name="Row" parent="." instance=ExtResource("1_r")]', "",
        '[node name="Inventory" parent="." instance=ExtResource("2_i")]', ""]))
    _write(root, "ui/frame.tres", '[gd_resource type="StyleBoxFlat" format=3]\n')
    _write(root, "ui/arrows/next.png")
    return root


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    settings = Settings(data_dir=isolated_data, project_root=tmp_path / "studio",
                        context_dir=tmp_path / "context")
    root = _game(tmp_path / "game")
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root


# What the simulated engine received: one batch per run.
BATCHES: list[list[dict[str, Any]]] = []


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, Any]]]:
    """An engine that draws each element: hover the same as normal, the rest not."""
    BATCHES.clear()

    def run(base: Path, script: Path, args: list[str], width: int, height: int,
            **_: Any) -> tuple[str, str, Any]:
        listing = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--gs-list="))
        batch = json.loads(Path(listing).read_text(encoding="utf-8"))
        BATCHES.append(batch)
        lines = []
        for job in batch:
            color = "red" if job.get("state") == "disabled" else "orange"
            Image.new("RGBA", (40, 20), color).save(job["out"])
            lines.append(f"GAMESTUDIO_SHOWCASE: {job['key']} OK 40x20")
        lines.append("GAMESTUDIO_RENDER: OK 0x0")
        log = "\n".join(lines)
        return "simulated", log, list(renders._RESULT.finditer(log))[-1]

    monkeypatch.setattr(renders, "run_engine", run)
    return BATCHES


def _items(found: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["file"]: {**item, "family": family["label"]}
            for family in found["families"] for item in family["items"]}


def test_icons_by_family(game: Path) -> None:
    found = showcase.showcase("game", "icons")
    icons = _items(found)
    assert set(icons) == {"icons/coin.svg", "icons/gem.svg"}
    assert icons["icons/gem.svg"]["width"] == 48
    assert icons["icons/coin.svg"]["users"] == ["ui/hud.tscn"]
    assert found["total"] == 2 and "verdict" not in icons["icons/coin.svg"]
    assert showcase.image_file("game", "icons", icons["icons/coin.svg"]["id"]) == \
        game / "icons/coin.svg"


def test_props_without_screens(game: Path) -> None:
    props = _items(showcase.showcase("game", "props"))
    assert set(props) == {"ui/big_button.tscn", "ui/row.tscn", "ui/frame.tres",
                          "ui/arrows/next.png"}, "a whole screen is not a prop"
    assert props["ui/big_button.tscn"]["family"] == "Components"
    assert props["ui/row.tscn"]["family"] == "Reused scenes"
    assert props["ui/frame.tres"]["family"] == "StyleBox"
    assert props["ui/big_button.tscn"]["stale"], "not drawn yet"


def test_a_button_is_drawn_in_its_states(game: Path, engine: list) -> None:
    found = showcase.render("game")
    assert found["drawn"] == 3 and len(engine) == 1, "one engine run for the whole batch"
    props = _items(found)
    button = props["ui/big_button.tscn"]
    assert [(s["state"], s["same"]) for s in button["states"]] == [
        ("normal", False), ("hover", True), ("pressed", True), ("disabled", False)]
    assert [s["state"] for s in props["ui/row.tscn"]["states"]] == ["normal"], \
        "without a button, a single state"
    batch = {job["key"].split("/")[1] if "stylebox" in job else job["state"] for job in engine[0]}
    assert batch == {"normal", "hover", "pressed", "disabled"}
    image = showcase.image_file("game", "props", button["id"], "hover")
    assert image.is_file() and image.is_relative_to(game / ".gamestudio" / "workspace")

    assert showcase.render("game")["drawn"] == 0, "an up-to-date drawing is not redone"
    _write(game, "ui/big_button.tscn", "[gd_scene format=3]\n\n"
           '[node name="Big" type="Button"]\ntext = "Play again"\n')
    assert _items(showcase.showcase("game", "props"))["ui/big_button.tscn"]["stale"]
    assert showcase.render("game")["drawn"] == 1


def test_an_element_is_discussed_with_an_agent(game: Path, engine: list) -> None:
    """The brief carries the element, each of its states, its family and the cards."""
    documents.create_document("game", "Buttons", "prop", "design/props")
    showcase.render("game")
    button = _items(showcase.showcase("game", "props"))["ui/big_button.tscn"]["id"]

    brief = handoff.showcase_brief("game", "props", button)
    text = Path(brief["path"]).read_text(encoding="utf-8")
    assert Path(brief["path"]).name == f"showcase-props-{button}.md"
    assert f"`{game / 'ui/big_button.tscn'}`" in text
    # A state identical to "normal" is flagged on its line; a distinct one is not.
    assert "hover.png` — " in text
    assert "disabled.png`\n" in text, "a state the theme distinguishes is not flagged"
    assert "buttons.md" in text, "the section's cards are cited"
    assert f"studio/props-{button}" in text, "the game is only modified on a branch"
    assert "\n" not in brief["prompt"] and brief["path"] in brief["prompt"]

    coin = _items(showcase.showcase("game", "icons"))["icons/coin.svg"]["id"]
    icon = Path(handoff.showcase_brief("game", "icons", coin)["path"]).read_text(encoding="utf-8")
    assert f"`{game / 'icons/gem.svg'}`" in icon, "its family, to judge consistency"
    with pytest.raises(NotFound):
        handoff.showcase_brief("game", "icons", "missing")
    with pytest.raises(NotFound, match="unknown showcase"):
        showcase.showcase("game", "scenery")
