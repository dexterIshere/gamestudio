"""A world card's workbench: generation only comes once the card is written.

What these tests protect: the card seeds the prompt; its concepts carry its
entity and are filed with it; the chosen concept is the only one to go to 3D;
3D never starts without consent; renaming a card loses neither its concepts nor
its character.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.service import build, documents, entities, using, world
from gamestudio.service.context import Studio
from gamestudio.service.errors import PaymentRequired, ServiceError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

CARD = """\
# Arachne

## In short

<!-- One sentence: what it is, and what sets it apart. -->
A cave **weaver**.

## Appearance

- grey skin, eight eyes

## In the game

Chapter 2 boss.
"""


@pytest.fixture
def isolated_studio(tmp_path: Path) -> Iterator[Studio]:
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path,
                        context_dir=tmp_path / "context")
    with using(build(settings)) as current:
        yield current


@pytest.fixture
def card(isolated_studio: Studio) -> str:
    section = world.create_section("game", "Characters", "character")
    documents.create_document("game", "Arachne", "card", section["folder"])
    documents.write_document("game", "arachne", CARD, folder=section["folder"])
    return "arachne"


def _concept(st: Studio, entity: str, batch: str = "batch1") -> str:
    found = st.space("game")
    asset = found.store.put_bytes(PNG + batch.encode(), ".png", kind="image", meta={
        "role": "generation", "project": "game", "prompt": "Arachne", "batch": batch,
        "entity": entity})
    found.db.save_asset(asset)
    return asset.id


def test_the_card_seeds_the_prompt_without_its_comments(card: str) -> None:
    bench = entities.workbench("game", "characters", card)
    assert bench["prompt"] == "Arachne, A cave weaver, grey skin, eight eyes"
    assert bench["entity"] == "arachne"
    assert bench["concepts"] == [] and bench["concept"] is None
    assert bench["character"] is None


def test_a_card_written_in_french_seeds_the_prompt_too() -> None:
    """Users write their cards in English or French: both headings are read."""
    text = "# Arachne\n\n## En bref\n\nUne tisseuse.\n\n## Apparence\n\n- huit yeux\n\n" \
           "## Dans le jeu\n\nBoss.\n"
    assert entities.seed_prompt(text, "Arachne") == "Arachne, Une tisseuse, huit yeux"


def test_a_concept_batch_waits_for_consent_without_writing(
        isolated_studio: Studio, card: str) -> None:
    """The refusal states the amount, and the card's workbench is not created yet."""
    with pytest.raises(PaymentRequired, match=r"~\$0\.012 for 2 image"):
        entities.generate_concepts("game", "characters", card, count=2)
    workbench = documents.directory("game", world.folder("characters")) / \
        f"{card}{documents.WORKBENCH}"
    assert not workbench.exists()
    assert isolated_studio.space("game").queue.stats("game") == {}


def test_a_concept_batch_carries_the_card_entity(
        isolated_studio: Studio, card: str) -> None:
    queued = entities.generate_concepts("game", "characters", card, count=2, confirm=True)
    job = isolated_studio.space("game").db.get_job(queued["queued"][0])
    assert job is not None
    assert job.project == "game"
    assert job.payload["entity"] == "arachne"
    assert job.payload["prompt"].startswith("Arachne, A cave weaver")
    assert job.step == "concepts:arachne"
    assert entities.workbench("game", "characters", card)["pending"][0]["id"] == job.id


def test_concepts_are_filed_with_the_entity(isolated_studio: Studio, card: str) -> None:
    concept = _concept(isolated_studio, "arachne")
    assert [row["id"] for row in entities.workbench("game", "characters", card)["concepts"]] \
        == [concept]
    librarian = isolated_studio.space("game").librarian
    librarian.sync_project("game")
    tree = librarian.tree("game")
    assert any(line.startswith("2d/arachne/concepts/") for line in tree), tree
    assert not any(line.startswith("generations/") for line in tree)


def test_only_the_chosen_concept_becomes_an_entity(isolated_studio: Studio, card: str) -> None:
    with pytest.raises(ServiceError, match="no concept chosen"):
        entities.realize("game", "characters", card, confirm=True)
    concept = _concept(isolated_studio, "arachne")
    assert entities.choose_concept("game", "characters", card, concept)["concept"] == concept
    # 3D costs money: never without consent.
    with pytest.raises(PaymentRequired):
        entities.realize("game", "characters", card)
    queued = entities.realize("game", "characters", card, confirm=True)
    job = isolated_studio.space("game").db.get_job(queued["queued"][0])
    assert job is not None
    assert job.payload["reference"] == concept
    assert job.payload["name"] == "arachne"
    assert job.project == "game"


def test_two_cards_with_the_same_name_do_not_share_concepts(
        isolated_studio: Studio, card: str) -> None:
    entities.choose_concept("game", "characters", card, None)
    section = world.create_section("game", "Creatures", "creature")
    documents.create_document("game", "Arachne", "card", section["folder"])
    entities.choose_concept("game", "creatures", "arachne", None)
    assert entities.workbench("game", "creatures", "arachne")["entity"] == "creatures-arachne"
    assert entities.workbench("game", "characters", card)["entity"] == "arachne"


def test_renaming_the_card_keeps_its_entity(isolated_studio: Studio, card: str) -> None:
    concept = _concept(isolated_studio, "arachne")
    entities.choose_concept("game", "characters", card, concept)
    documents.rename_document("game", card, "The Weaver", "world/characters")
    bench = entities.workbench("game", "characters", "the-weaver")
    assert bench["entity"] == "arachne"
    assert bench["concept"] == concept
    assert [row["id"] for row in bench["concepts"]] == [concept]
    # Deleting the card takes its workbench, and only that.
    documents.delete_document("game", "the-weaver", "world/characters")
    assert not list(documents.directory("game", "world/characters").glob("*.workbench.json"))
    assert isolated_studio.space("game").db.get_asset(concept) is not None
