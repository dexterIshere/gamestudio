"""The agent discussion on a game design card: its brief, its tab.

What these tests protect: the brief cites everything by absolute path -- the
card, which is alive and is not copied, the game render, the user's sketch,
the generated images --, it says what is missing instead of keeping quiet, it
requires no Godot project, and the discussion opens in a tab named after the
subject. The game design templates give a card clear sections.

A card's media come from `cards.media`: the first test lets it find them on
files placed where the studio places them; the others substitute chosen media,
to test what is missing and what overflows. No terminal tab is opened: the
manager is faked.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gamestudio.api import auth
from gamestudio.api.app import app
from gamestudio.config import Settings
from gamestudio.service import build, cards, documents, folders, handoff, renders, using
from gamestudio.service.context import space

FOLDER = "design/interface"
NAME = "top-bar"
SCENE = "res://scenes/hud/top_bar.tscn"
RENDERED_AT = "2026-10-05T12:00:00+00:00"

PROJECT_GODOT = """config_version=5

[application]

config/name="My Game"
run/main_scene="res://scenes/main.tscn"
"""

# A HUD bar: an interface root, and the script it carries.
TOP_BAR = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scenes/hud/top_bar.gd" id="1_s"]

[node name="TopBar" type="MarginContainer"]
script = ExtResource("1_s")
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _local(iso: str) -> str:
    """A date as the brief shows it: in the machine's time zone."""
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")


def _sections(template: str) -> str:
    """A template's section headings, as the brief lists them."""
    return ", ".join(line[3:].strip() for line in documents.TEMPLATES[template]["body"]
                     .splitlines() if line.startswith("## "))


def _media(project: str, folder: str, name: str, *, title: str = "Top bar",
           section: str = "Interface", **parts: Any) -> dict[str, Any]:
    """A card's media in the contract's shape (`CardMedia`): nothing by default."""
    return {"project": project, "folder": folder, "name": name, "title": title,
            "section_label": section, "render": None, "sketch": None, "generations": [],
            "pending": 0, "prompt_seed": title, **parts}


class Tabs:
    """The terminal manager, faked: it records what it is asked."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.typed: list[tuple[str, str]] = []

    def create(self, **request: Any) -> SimpleNamespace:
        self.created.append(request)
        described = {"id": f"tab-{len(self.created)}", "title": request["title"],
                     "cwd": request["cwd"]}
        return SimpleNamespace(describe=lambda: described)

    def type_in(self, session: str, text: str) -> dict[str, Any]:
        self.typed.append((session, text))
        return {"id": session}


@pytest.fixture
def studio(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    """An isolated studio, and the game folder a test will open (empty)."""
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    settings = Settings(data_dir=isolated_data, project_root=studio_dir,
                        context_dir=tmp_path / "context")
    root = tmp_path / "my-game"
    root.mkdir()
    with using(build(settings)):
        yield root


@pytest.fixture
def tabs(monkeypatch: pytest.MonkeyPatch) -> Tabs:
    fake = Tabs()
    monkeypatch.setattr(handoff, "manager", fake)
    return fake


@pytest.fixture
def workbench(studio: Path, tmp_path: Path) -> dict[str, Path]:
    """A Godot game (client in `client/`), an interface card and its media.

    The sketch is placed next to the card, the render enters the store the way
    `render_scene` puts it there: the library files it under the section, and
    `cards.media` finds both.
    """
    client = studio / "client"
    _write(client / "project.godot", PROJECT_GODOT)
    _write(client / "scenes" / "main.tscn", '[gd_scene format=3]\n\n[node name="Main" '
                                            'type="Control"]\n')
    _write(client / "scenes" / "hud" / "top_bar.tscn", TOP_BAR)
    _write(client / "scenes" / "hud" / "top_bar.gd", "extends MarginContainer\n")
    folders.open_folder(str(studio), "game")
    card = Path(documents.create_document("game", "Top bar", "interface",
                                           FOLDER)["path"])

    sketch = card.with_name(f"{NAME}{cards.SKETCH_SUFFIX}")
    sketch.write_text('{"type": "excalidraw", "elements": []}\n', encoding="utf-8")
    png = card.with_name(f"{NAME}{cards.SKETCH_PNG_SUFFIX}")
    Image.new("RGB", (8, 8), "white").save(png)

    st = space("game")
    image = tmp_path / "render.png"
    Image.new("RGB", (8, 8), "red").save(image)
    asset = st.store.put_file(image, kind="image", meta={
        "role": renders.ROLE, "project": "game", "name": NAME, "scene": SCENE,
        "godot": "client", "scale": 2.0, "crop": True, "setup": False, "locale": "",
        "rendered_at": RENDERED_AT, "briefing": renders.briefing_tag(FOLDER)})
    st.db.save_asset(asset)
    st.librarian.sync_project("game")
    render = st.librarian.project_dir("game") / "briefing" / FOLDER / f"{NAME}.png"
    assert render.is_file()
    return {"root": studio, "card": card, "render": render, "sketch": png}


def test_the_brief_cites_the_card_and_its_images_by_absolute_path(
        workbench: dict[str, Path]) -> None:
    sent = handoff.card_brief("game", FOLDER, NAME)

    assert set(sent) == {"project", "folder", "name", "title", "section_label", "path",
                         "text", "prompt", "godot", "godot_ready"}
    assert (sent["title"], sent["section_label"]) == ("Top bar", "Interface")
    path = Path(sent["path"])
    assert path.name == "card-design-interface-top-bar.md"
    text = path.read_text(encoding="utf-8")
    assert text == sent["text"]

    # The card is alive: cited by its path, never copied.
    assert f"`{workbench['card']}`" in text
    instruction = next(line for line in workbench["card"].read_text(encoding="utf-8")
                       .splitlines() if line.startswith("<!--"))
    assert instruction not in text
    assert _sections("interface") in text, "the template is named"
    # Its images, by absolute path: the game as it is, then the intent.
    assert f"`{workbench['render']}`" in text
    assert f"scene `{SCENE}`, rendered {_local(RENDERED_AT)}" in text
    assert f"`{workbench['sketch']}`" in text
    assert "it is what the user is talking about" in text
    assert "look=true" in text, "a CLI that does not read images has a way to see them"
    # The game: the Godot project, the card's scene, the script of its root.
    assert sent["godot"] == str(workbench["root"] / "client")
    assert sent["godot_ready"] is True
    assert "root `MarginContainer`" in text
    assert "`client/scenes/hud/top_bar.gd`" in text
    # What the agent does: redo the render under the card's name, and write
    # following the skill, cited by its absolute path.
    assert f'render_scene(project="game", scene="{SCENE}", name="{NAME}"' in text
    assert f'card_rerender(project="game", folder="{FOLDER}", name="{NAME}")' in text, \
        "a render without setup is redone as is"
    skill = workbench["root"].parent / "studio" / ".claude" / "skills" / "game-survey"
    assert f"`{skill / 'SKILL.md'}` (“Writing a card”)" in text
    assert "Nothing paid without the user's explicit consent" in text
    # A single line goes to the terminal: it points to the brief.
    assert "\n" not in sent["prompt"]
    assert sent["prompt"] == (
        f"We are working on “Top bar” (Interface) of project game: read the brief "
        f"{path}, look at the images it cites, tell me in two lines where this card "
        "stands, then wait for my request.")


def test_what_is_missing_is_said(studio: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folders.open_folder(str(studio), "game")
    documents.create_document("game", "Top bar", "interface", FOLDER)
    monkeypatch.setattr(cards, "media", _media)

    text = handoff.card_brief("game", FOLDER, NAME)["text"]

    assert "- **Game render** — none." in text
    assert "- **User's sketch** — none." in text
    assert "- **Generated images** — none." in text
    assert "look=true" not in text, "nothing to look at"
    assert "none known: the card has no render yet" in text
    assert "no `project.godot`" in text


def test_generated_images_are_cited_by_their_path(
        studio: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folders.open_folder(str(studio), "game")
    documents.create_document("game", "Top bar", "interface", FOLDER)
    image = tmp_path / "generated.png"
    Image.new("RGB", (8, 8), "blue").save(image)
    generated = {"asset_id": "a" * 64, "path": str(image), "prompt": "neon bar " * 20,
                 "model": "runware:101@1", "reference": "sketch", "created_at": RENDERED_AT,
                 "width": 8, "height": 8}
    waiting = {**generated, "asset_id": "b" * 64, "path": None, "prompt": "variant",
               "reference": ""}
    others = [{**generated, "asset_id": f"{index}" * 64} for index in range(5)]
    failure = {"job": "job-1", "error": "unknown model\n  detail", "at": RENDERED_AT}
    monkeypatch.setattr(cards, "media", lambda project, folder, name: _media(
        project, folder, name, generations=[generated, waiting, *others], pending=1,
        failures=[failure]))

    text = handoff.card_brief("game", FOLDER, NAME)["text"]

    assert "- **Generated images** — 7, most recent first; 1 in progress:" in text
    assert f"`{image}` — “neon bar" in text
    assert "…”" in text, "a long prompt is cut"
    assert f"from the sketch, {_local(RENDERED_AT)}" in text
    # Not filed yet: cited by its asset, to look at with `view_asset`.
    assert f"asset `{'b' * 64}` (not in the library yet: `view_asset`)" in text
    assert "“variant”, text only" in text
    assert "… and 1 more" in text
    # A failed generation leaves its trace: the agent knows why nothing came.
    assert (f"1 recent failure(s), the last one {_local(RENDERED_AT)}: “unknown model "
            "detail” (job `job-1`, `job_detail`)") in text
    assert "look=true" in text


def test_a_card_is_discussed_without_a_godot_project(
        studio: Path, tabs: Tabs, monkeypatch: pytest.MonkeyPatch) -> None:
    folders.open_folder(str(studio), "game")
    documents.create_document("game", "Combat", "mechanic", "design/mechanics")
    monkeypatch.setattr(cards, "media", lambda project, folder, name: _media(
        project, folder, name, title="Combat", section="Mechanics"))

    sent = handoff.send_card("game", "design/mechanics", "combat", harness="claude",
                             effort="high")

    assert sent["godot_ready"] is False, "no game is required"
    assert "text" not in sent and sent["session"]["id"] == "tab-1"
    [created] = tabs.created
    assert created["title"] == "Mechanics · Combat"
    assert created["first_message"] == sent["prompt"]
    assert created["cwd"] == str(studio), "the discussion opens in the game folder"
    assert (created["harness"], created["effort"]) == ("claude", "high")
    text = Path(sent["path"]).read_text(encoding="utf-8")
    assert f"“Mechanic”: {_sections('mechanic')}." in text
    # An already open discussion receives the same line, typed.
    handoff.send_card("game", "design/mechanics", "combat", session="tab-7")
    assert tabs.typed == [("tab-7", sent["prompt"])]
    assert len(tabs.created) == 1


def test_the_routes_open_the_discussion(workbench: dict[str, Path], tabs: Tabs,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(auth.TOKEN_ENV, "test-token")
    auth.reset()
    try:
        client = TestClient(app, base_url="http://127.0.0.1:7788",
                            headers={"Authorization": "Bearer test-token"})
        route = f"/api/projects/game/documents/{NAME}"

        read = client.get(f"{route}/brief", params={"folder": FOLDER})
        assert read.status_code == 200
        assert read.json()["title"] == "Top bar" and read.json()["text"]
        assert client.get(f"{route}/brief").status_code == 422, "the section is required"
        assert client.get("/api/projects/game/documents/missing/brief",
                          params={"folder": FOLDER}).status_code == 404

        opened = client.post(f"{route}/handoff", params={"folder": FOLDER},
                             json={"harness": "claude", "effort": "", "session": ""})
        assert opened.status_code == 200
        assert opened.json()["session"]["title"] == "Interface · Top bar"
        assert tabs.created[0]["loop"] is not None, "the tab follows the server's loop"
    finally:
        auth.reset()


@pytest.mark.parametrize(("template", "shelf", "sections"), [
    ("interface", "design/interface", ["Access", "Content", "Actions", "States"]),
    ("icon", "design/icons", ["Usage", "Shape", "Set"]),
    ("prop", "design/props", ["Usage", "Shape", "States", "Variants"]),
    ("mechanic", "design/mechanics", ["Loop", "Rules", "Parameters", "Requirements"]),
    ("direction", "design/direction", ["Rules", "In the game", "References", "To avoid"]),
    ("mood", "design/direction", ["Style", "Moments", "To avoid"]),
])
def test_a_card_created_from_a_template_is_clear_at_once(
        studio: Path, template: str, shelf: str, sections: list[str]) -> None:
    text = documents.create_document("game", "Trial", template, shelf)["text"]
    lines = text.splitlines()

    assert lines[0] == "# Trial"
    # The first line says it all: its instruction comes before any section.
    assert lines[2].startswith("<!-- One sentence")
    assert [line[3:] for line in lines if line.startswith("## ")] == sections
    # Bullets to fill in, which the renderer recognizes: a dash, then a space.
    bullets = [line for line in lines if line.startswith("-")]
    assert bullets and all(line.startswith("- ") for line in bullets)


def test_a_world_card_is_discussed_with_its_group_and_its_concepts(
        studio: Path, tabs: Tabs, tmp_path: Path) -> None:
    """A world card has neither render nor sketch: its brief tells its filing, its
    neighbours in the same group, its concepts, and its stage."""
    from gamestudio.service import entities, world

    folders.open_folder(str(studio), "game")
    world.create_section("game", "Civs", "star")
    world.set_axes("game", "civs", [{"label": "Race", "values": [
        {"label": "Humans"}, {"label": "Orcs"}]}])
    for title, race in (("Barracks", "humans"), ("Forge", "humans"), ("Totem", "orcs")):
        documents.create_document("game", title, "card", "world/civs")
        entities.set_axes("game", "civs", documents.slug(title), {"race": race})

    st = space("game")
    image = tmp_path / "concept.png"
    Image.new("RGB", (8, 8), "blue").save(image)
    concept = st.store.put_file(image, kind="image", meta={
        "role": "generation", "project": "game", "entity": "barracks",
        "batch": "batch-1", "prompt": "Barracks"})
    st.db.save_asset(concept)

    sent = handoff.card_brief("game", "world/civs", "barracks")
    text = sent["text"]
    assert sent["section_label"] == "Civs" and sent["title"] == "Barracks"
    assert str(documents.directory("game", "world/civs") / "barracks.md") in text
    assert "Race: Humans" in text
    neighbours = text.split("**Neighbours**", 1)[1].split("\n- **", 1)[0]
    assert "forge.md" in neighbours and "totem.md" not in neighbours
    assert concept.id in text and "Chosen concept** — none" in text
    assert "not gone to 3D" in text
    assert Path(sent["path"]).name == "card-world-civs-barracks.md"

    handoff.send_card("game", "world/civs", "barracks")
    assert tabs.created[-1]["title"] == "Civs · Barracks"
