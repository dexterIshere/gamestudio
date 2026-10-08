"""Creating in a section on request: the brief an agent receives, its tab.

What these tests protect: a section's "New" belongs to it -- the brief carries
the request as is, the section's template (its shelf's, `card` for the world),
the documents it already has (so they are not duplicated) and the game folder,
read-only; a request made in a world section's group ("Race: Humans") files
each card created there; an empty request is refused; the discussion opens in
the project folder, named after the section. No terminal tab is opened: the
manager is faked.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, documents, folders, handoff, using, world
from gamestudio.service.errors import ServiceError

# A real user request, with its multiplication sign.
REQUEST = "The inventory screen: a grid of 6 × 4 cells, the weight at the bottom."  # noqa: RUF001


class Tabs:
    """The terminal manager, faked: it records what it is asked."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create(self, **request: Any) -> SimpleNamespace:
        self.created.append(request)
        described = {"id": "tab-1", "title": request["title"], "cwd": request["cwd"]}
        return SimpleNamespace(describe=lambda: described)


@pytest.fixture
def game(tmp_path: Path, isolated_data: Path) -> Iterator[Path]:
    """An isolated studio, and a game folder open as the project `game`."""
    studio_dir = tmp_path / "studio"
    studio_dir.mkdir()
    settings = Settings(data_dir=isolated_data, project_root=studio_dir,
                        context_dir=tmp_path / "context")
    root = tmp_path / "my-game"
    root.mkdir()
    with using(build(settings)):
        folders.open_folder(str(root), "game")
        yield root


def test_the_brief_carries_the_request_the_template_and_the_neighbours(game: Path) -> None:
    documents.create_document("game", "Top bar", "interface", "design/interface")
    brief = handoff.create_brief("game", "design/interface", REQUEST)

    assert brief["section_label"] == "Interface"
    assert brief["template"] == "interface"
    text = brief["text"]
    assert f"> {REQUEST}" in text
    assert "`top-bar.md` — “Top bar”" in text
    assert 'template="interface", folder="design/interface"' in text
    assert str(game) in text and "read-only" in text
    # A game design card follows the skill's writing rules.
    assert "game-survey" in text
    path = Path(brief["path"])
    assert path.is_file() and path.name.startswith("create-design-interface-")
    assert str(path) in brief["prompt"]


def test_a_world_section_writes_cards(game: Path) -> None:
    section = world.create_section("game", "Buildings")
    brief = handoff.create_brief("game", section["folder"], "A village forge, in stone.")
    assert brief["section_label"] == "Buildings"
    assert brief["template"] == "card"


def test_a_request_in_a_group_files_its_cards_there(
        game: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gamestudio.service import entities

    world.create_section("game", "Civs", "star")
    world.set_axes("game", "civs", [{"label": "Race", "values": [
        {"label": "Humans"}, {"label": "Orcs"}]}])
    documents.create_document("game", "Barracks", "card", "world/civs")
    documents.create_document("game", "Totem", "card", "world/civs")
    entities.set_axes("game", "civs", "barracks", {"race": "humans"})
    entities.set_axes("game", "civs", "totem", {"race": "orcs"})

    brief = handoff.create_brief("game", "world/civs", "A forge and a market, in stone.",
                                 axes={"race": "Humans"})
    assert brief["axes"] == [{"axis": "race", "axis_label": "Race",
                              "value": "humans", "value_label": "Humans"}]
    text = brief["text"]
    assert "“Civs” · Race: Humans" in text
    assert 'values={"race": "humans"}' in text
    # The group's neighbours, and only them.
    group = text.split("## The group", 1)[1].split("##", 1)[0]
    assert "barracks.md" in group and "totem.md" not in group
    assert "(Race: Humans)" in brief["prompt"]

    tabs = Tabs()
    monkeypatch.setattr(handoff, "manager", tabs)
    handoff.send_create("game", "world/civs", "A forge and a market, in stone.",
                        axes={"race": "humans"})
    assert tabs.created[0]["title"] == "Civs · Humans · new"

    with pytest.raises(ServiceError, match="is not a value"):
        handoff.create_brief("game", "world/civs", "A forge, in stone.", axes={"race": "Elves"})
    with pytest.raises(ServiceError, match="is not a world section"):
        handoff.create_brief("game", "design/interface", REQUEST, axes={"race": "humans"})


def test_a_note_does_not_follow_the_card_rules(game: Path) -> None:
    brief = handoff.create_brief("game", "notes", "Think about the offline mode.")
    assert brief["template"] == "note"
    assert "game-survey" not in brief["text"]


def test_a_too_short_request_is_refused(game: Path) -> None:
    with pytest.raises(ServiceError, match="too short"):
        handoff.create_brief("game", "design/interface", "  menu ")


def test_the_discussion_opens_in_the_game_named_after_the_section(
        game: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tabs = Tabs()
    monkeypatch.setattr(handoff, "manager", tabs)
    sent = handoff.send_create("game", "design/mechanics", REQUEST)

    assert "text" not in sent
    (created,) = tabs.created
    assert created["title"] == "Mechanics · new"
    assert created["cwd"] == str(game)
    assert sent["path"] in created["first_message"]


def test_images_dropped_with_the_request_go_into_the_brief(game: Path) -> None:
    from gamestudio.service import inbox

    image = inbox.add_bytes(b"\x89PNG\r\n\x1a\n" + _png_body(), "mood.png")
    brief = handoff.create_brief("game", "design/direction",
                                 "The intent: a tender and bright space opera",
                                 [image["relative"]])
    assert brief["images"] == [str(Path(image["path"]).resolve())]
    text = brief["text"]
    assert "## The reference images" in text and image["path"] in text
    assert "card_reference_add(" in text
    with pytest.raises(ServiceError, match="not found"):
        handoff.create_brief("game", "design/direction", "The game's intent, plainly",
                             ["inbox/missing.png"])
    without = handoff.create_brief("game", "design/direction", "The game's intent, plainly")
    assert without["images"] == [] and "The reference images" not in without["text"]


def _png_body() -> bytes:
    """The rest of a valid PNG, without its signature."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "orange").save(buffer, "PNG")
    return buffer.getvalue()[8:]
