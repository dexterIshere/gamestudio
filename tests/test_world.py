"""A project's world, and its workspace logo.

What these tests protect: the world starts empty and only fills with what the
user declares; a section holding cards is not erased in one gesture -- except
with `force`, which then takes the texts and nothing they produced; an axis
files cards without ever renaming them, and renaming a label detaches nobody;
an axis belongs to the project -- reused from one section to another, corrected
everywhere at once; the logo is recognized by its bytes, not its name.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, documents, entities, using, workspace, world
from gamestudio.service.context import Studio
from gamestudio.service.errors import NotFound, ServiceError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"/>'


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    with using(build(settings)) as current:
        yield current


def test_the_world_starts_empty(isolated_studio: Studio) -> None:
    assert world.sections("game") == []


def test_a_declared_section_becomes_a_shelf_of_cards(isolated_studio: Studio) -> None:
    created = world.create_section("game", "Buildings", "building")
    assert created == {"id": "buildings", "label": "Buildings", "icon": "building",
                       "folder": "world/buildings", "documents": 0, "axes": []}

    card = documents.create_document("game", "The Tower", "card", created["folder"])
    assert card["path"].endswith(".gamestudio/documents/world/buildings/the-tower.md")
    assert world.sections("game")[0]["documents"] == 1
    # A shelf never shows up in another one, nor at the root.
    assert documents.documents("game") == []


def test_creation_order_is_kept(isolated_studio: Studio) -> None:
    for label in ("Characters", "Factions", "Places"):
        world.create_section("game", label)
    assert [entry["id"] for entry in world.sections("game")] == [
        "characters", "factions", "places"]


def test_a_section_is_not_created_twice(isolated_studio: Studio) -> None:
    world.create_section("game", "Characters")
    with pytest.raises(ServiceError, match="already exists"):
        world.create_section("game", "characters")


def test_an_unknown_icon_is_refused(isolated_studio: Studio) -> None:
    with pytest.raises(ServiceError, match="unknown icon"):
        world.create_section("game", "Characters", "pink-dragon")


def test_renaming_keeps_the_folder(isolated_studio: Studio) -> None:
    world.create_section("game", "Characters")
    updated = world.update_section("game", "characters", label="Heroes", icon="star")
    assert updated["id"] == "characters"
    assert updated["label"] == "Heroes"
    assert updated["icon"] == "star"


def test_a_full_section_is_not_erased(isolated_studio: Studio) -> None:
    created = world.create_section("game", "Characters")
    documents.create_document("game", "Arachne", "card", created["folder"])
    with pytest.raises(ServiceError, match="still holds"):
        world.delete_section("game", "characters")

    documents.delete_document("game", "arachne", created["folder"])
    assert world.delete_section("game", "characters")["deleted"] is True
    assert world.sections("game") == []
    with pytest.raises(NotFound):
        world.delete_section("game", "characters")


def test_the_logo_is_set_replaced_and_cleared(isolated_studio: Studio) -> None:
    assert workspace.read_meta("game")["logo"] is None

    meta = workspace.set_logo("game", PNG)
    assert meta["logo"]["path"].endswith(".gamestudio/documents/logo.png")

    # An SVG replaces the PNG: only one remains.
    workspace.set_logo("game", SVG)
    assert workspace.logo_path("game").name == "logo.svg"
    assert not (isolated_studio.space("game").paths.documents / "logo.png").exists()

    assert workspace.clear_logo("game")["logo"] is None


def test_a_logo_that_is_not_an_image_is_refused(isolated_studio: Studio) -> None:
    with pytest.raises(ServiceError, match="PNG, JPEG"):
        workspace.set_logo("game", b"#!/bin/sh\necho hello\n")
    assert workspace.logo_path("game") is None


# ------------------------------------------------------------- axes


def _characters(isolated_studio: Studio) -> None:
    """A "Characters" section, its "Race" axis, and a filed card."""
    world.create_section("game", "Characters", "character")
    world.set_axes("game", "characters", [
        {"label": "Race", "values": [{"label": "Human"}, {"label": "Elf"}]},
    ])
    documents.create_document("game", "Arachne", "card", "world/characters")


def test_an_axis_files_cards_without_renaming_them(isolated_studio: Studio) -> None:
    """What the axis promises: a card is filed, and its folder does not move."""
    _characters(isolated_studio)
    entry = world.section("game", "characters")
    assert [axis["id"] for axis in entry["axes"]] == ["race"]
    assert [value["id"] for value in entry["axes"][0]["values"]] == ["human", "elf"]

    bench = entities.set_axes("game", "characters", "arachne", {"race": "Elf"})
    assert bench["axes"] == {"race": "elf"}
    assert bench["path"].endswith("world/characters/arachne.md")
    assert entities.entities("game")[0]["axes"] == {"race": "elf"}


def test_renaming_detaches_no_card(isolated_studio: Studio) -> None:
    """The id is stable, the label can be corrected: nobody moves."""
    _characters(isolated_studio)
    entities.set_axes("game", "characters", "arachne", {"race": "elf"})

    entry = world.set_axes("game", "characters", [
        {"id": "race", "label": "People", "values": [
            {"id": "human", "label": "Human"}, {"id": "elf", "label": "Dark elf"}]},
    ])
    assert entry["detached"] == 0
    assert entry["axes"][0]["label"] == "People"
    assert entities.entities("game")[0]["axes"] == {"race": "elf"}


def test_removing_a_value_detaches_its_cards(isolated_studio: Studio) -> None:
    """A removed value leaves no card filed under a ghost."""
    _characters(isolated_studio)
    entities.set_axes("game", "characters", "arachne", {"race": "elf"})

    entry = world.set_axes("game", "characters", [
        {"id": "race", "label": "Race", "values": [{"id": "human", "label": "Human"}]},
    ])
    assert entry["detached"] == 1
    assert entities.entities("game")[0]["axes"] == {}

    # Removing the whole axis detaches too, and leaves nothing behind.
    entities.set_axes("game", "characters", "arachne", {"race": "human"})
    assert world.set_axes("game", "characters", [])["detached"] == 1
    assert entities.entities("game")[0]["axes"] == {}


def test_filing_outside_the_grid_is_refused(isolated_studio: Studio) -> None:
    """A wrong filing only shows once the cards are lost: refuse it."""
    _characters(isolated_studio)
    with pytest.raises(ServiceError, match="is not a value"):
        entities.set_axes("game", "characters", "arachne", {"race": "Orc"})
    with pytest.raises(ServiceError, match="does not declare the axis"):
        entities.set_axes("game", "characters", "arachne", {"faction": "Order"})
    assert entities.entities("game")[0]["axes"] == {}


def test_a_value_can_also_be_given_by_its_label(isolated_studio: Studio) -> None:
    """The agent writes "Elf": the id is what gets filed on the card."""
    _characters(isolated_studio)
    assert entities.set_axes("game", "characters", "arachne",
                             {"race": "elf"})["axes"] == {"race": "elf"}
    assert entities.set_axes("game", "characters", "arachne",
                             {"race": "Human"})["axes"] == {"race": "human"}


def test_two_axes_coexist_and_read_together(isolated_studio: Studio) -> None:
    _characters(isolated_studio)
    world.set_axes("game", "characters", [
        {"id": "race", "label": "Race", "values": [{"id": "elf", "label": "Elf"}]},
        {"id": "faction", "label": "Faction", "values": [{"id": "order", "label": "Order"}]},
    ])
    bench = entities.set_axes("game", "characters", "arachne",
                              {"race": "elf", "faction": "order"})
    assert bench["axes"] == {"race": "elf", "faction": "order"}
    # An empty value detaches that axis, and that axis only.
    bench = entities.set_axes("game", "characters", "arachne", {"faction": ""})
    assert bench["axes"] == {"race": "elf"}


def _buildings(isolated_studio: Studio) -> None:
    """A second section that reuses the characters' "Race" axis."""
    _characters(isolated_studio)
    world.create_section("game", "Buildings", "building")
    world.set_axes("game", "buildings", ["race"])
    documents.create_document("game", "Forge", "card", "world/buildings")


def test_a_project_axis_is_reused_not_rewritten(isolated_studio: Studio) -> None:
    """A new section cites the axis: its grid is the project's."""
    _buildings(isolated_studio)
    assert world.section("game", "buildings")["axes"] == world.section("game", "characters")["axes"]
    [race] = world.axes("game")
    assert [entry["id"] for entry in race["sections"]] == ["characters", "buildings"]
    bench = entities.set_axes("game", "buildings", "forge", {"race": "Elf"})
    assert bench["axes"] == {"race": "elf"}


def test_correcting_an_axis_applies_everywhere(isolated_studio: Studio) -> None:
    """A label and a value added from one section show in the other."""
    _buildings(isolated_studio)
    world.set_axes("game", "buildings", [
        {"id": "race", "label": "People", "values": [
            {"id": "human", "label": "Human"}, {"id": "elf", "label": "Elf"},
            {"label": "Orc"}]},
    ])
    other = world.section("game", "characters")["axes"][0]
    assert other["label"] == "People"
    assert [value["id"] for value in other["values"]] == ["human", "elf", "orc"]


def test_removing_a_shared_value_detaches_in_every_section(isolated_studio: Studio) -> None:
    _buildings(isolated_studio)
    entities.set_axes("game", "characters", "arachne", {"race": "elf"})
    entities.set_axes("game", "buildings", "forge", {"race": "elf"})

    entry = world.set_axes("game", "buildings", [
        {"id": "race", "label": "Race", "values": [{"id": "human", "label": "Human"}]},
    ])
    assert entry["detached"] == 2
    assert entry["detached_in"] == {"characters": 1, "buildings": 1}
    assert all(row["axes"] == {} for row in entities.entities("game"))


def test_dropping_an_axis_detaches_only_its_cards(isolated_studio: Studio) -> None:
    """The section dropping the axis loses its filing; the axis stays in the project."""
    _buildings(isolated_studio)
    entities.set_axes("game", "characters", "arachne", {"race": "elf"})
    entities.set_axes("game", "buildings", "forge", {"race": "human"})

    assert world.set_axes("game", "buildings", [])["detached_in"] == {"buildings": 1}
    rows = {row["name"]: row["axes"] for row in entities.entities("game")}
    assert rows == {"arachne": {"race": "elf"}, "forge": {}}
    [race] = world.axes("game")
    assert [entry["id"] for entry in race["sections"]] == ["characters"]


def test_naming_an_existing_axis_reuses_it_without_removing_anything(
        isolated_studio: Studio) -> None:
    """Declaring "Race=Orc" on a new section adds Orc; Human and Elf stay."""
    _characters(isolated_studio)
    entities.set_axes("game", "characters", "arachne", {"race": "elf"})
    world.create_section("game", "Units", "weapon")
    entry = world.set_axes("game", "units", [{"label": "race", "values": [{"label": "Orc"}]}])
    assert entry["detached"] == 0
    assert entry["axes"][0]["label"] == "Race"
    assert [value["id"] for value in entry["axes"][0]["values"]] == ["human", "elf", "orc"]
    assert entities.entities("game")[0]["axes"] == {"race": "elf"}


def test_an_unknown_axis_cited_by_id_is_refused(isolated_studio: Studio) -> None:
    world.create_section("game", "Units", "weapon")
    with pytest.raises(ServiceError, match="does not exist in the project"):
        world.set_axes("game", "units", ["faction"])


def test_an_axis_is_forgotten_only_when_unused(isolated_studio: Studio) -> None:
    _characters(isolated_studio)
    with pytest.raises(ServiceError, match="still in use"):
        world.forget_axis("game", "race")
    world.set_axes("game", "characters", [])
    assert world.forget_axis("game", "race")["forgotten"] is True
    assert world.axes("game") == []


def test_a_v2_declaration_moves_its_axes_up_to_the_project(isolated_studio: Studio) -> None:
    """Two "Race" axes declared per section become one grid, losing nothing."""
    where = documents.directory("game", "world")
    where.mkdir(parents=True, exist_ok=True)
    (where / "sections.json").write_text(json.dumps({"version": 2, "sections": [
        {"id": "characters", "label": "Characters", "icon": "character", "axes": [
            {"id": "race", "label": "Race", "values": [{"id": "elf", "label": "Elf"}]}]},
        {"id": "buildings", "label": "Buildings", "icon": "building", "axes": [
            {"id": "race", "label": "Race", "values": [{"id": "orc", "label": "Orc"}]}]},
    ]}), encoding="utf-8")
    [race] = world.axes("game")
    assert [value["id"] for value in race["values"]] == ["elf", "orc"]
    assert len(race["sections"]) == 2
    # The next write is v3: sections only cite the id.
    world.update_section("game", "buildings", icon="place")
    saved = json.loads((where / "sections.json").read_text(encoding="utf-8"))
    assert saved["version"] == 3
    assert [row["axes"] for row in saved["sections"]] == [["race"], ["race"]]


def test_removing_a_full_section_with_force(isolated_studio: Studio) -> None:
    """`force` takes the cards and their workbenches, and leaves the library."""
    _characters(isolated_studio)
    entities.set_axes("game", "characters", "arachne", {"race": "elf"})
    where = documents.directory("game", "world/characters")
    assert (where / "arachne.workbench.json").is_file()

    with pytest.raises(ServiceError, match="still holds"):
        world.delete_section("game", "characters")

    gone = world.delete_section("game", "characters", force=True)
    assert gone["cards"] == ["arachne"] and gone["deleted"] is True
    assert world.sections("game") == []
    assert not where.exists()
