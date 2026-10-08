"""Editing the filing: file elsewhere, rename, paste, delete.

The rule tested here is the one that protects the studio: a sheet piece is
edited freely, an asset a production holds -- a character, a world card, an
effect -- is not deleted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gamestudio.config import Settings
from gamestudio.domain.models import (
    Character,
    CharacterSpec,
    Rig,
)
from gamestudio.domain.skeleton import Archetype
from gamestudio.pipeline.base import Context
from gamestudio.sheet import import_sheet
from gamestudio.store.assets import AssetStore
from gamestudio.store.curation import Curator
from gamestudio.store.db import Database
from gamestudio.store.library import Librarian

SHEET = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40">
  <g id="square" transform="translate(4,4)"><rect width="16" height="16"/></g>
  <g id="circle" transform="translate(64,4)"><circle cx="8" cy="8" r="8"/></g>
</svg>
"""


@pytest.fixture()
def studio(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", project_root=tmp_path)
    settings.ensure_dirs()
    db = Database(settings.db_path)
    store = AssetStore(settings.assets_dir)
    librarian = Librarian(db, store, settings.library_dir)
    curator = Curator(db, store, librarian, documents=tmp_path / "documents")

    source = tmp_path / "pack.svg"
    source.write_text(SHEET, encoding="utf-8")
    with Context(project="game", settings=settings, db=db, store=store) as ctx:
        import_sheet(source, ctx, multi=True)
    librarian.sync_project("game")
    return settings, db, store, librarian, curator, source


def piece(db, name: str):
    return next(a for a in db.list_assets(limit=100) if a.meta.get("name") == name)


def files(librarian, project: str, sheet: str) -> list[str]:
    folder = librarian.project_dir(project) / "icons" / sheet
    return sorted(p.name for p in folder.glob("*")) if folder.exists() else []


# ----------------------------------------------------------------------- rename


def test_renaming_a_piece_renames_its_file(studio):
    _, db, _, librarian, curator, _ = studio
    outcome = curator.rename(piece(db, "square").id, "Naïve café")

    assert outcome.ok
    # Accents are kept, as everywhere in the library; only punctuation
    # becomes a dash.
    assert piece(db, "naïve-café")
    assert files(librarian, "game", "pack") == ["circle.svg", "naïve-café.svg",
                                                "sheet.json"]


def test_a_taken_name_is_refused(studio):
    _, db, _, _, curator, _ = studio
    outcome = curator.rename(piece(db, "square").id, "circle")
    assert not outcome.done
    assert "already exists" in next(iter(outcome.refused.values()))


def test_an_empty_name_is_refused(studio):
    _, db, _, _, curator, _ = studio
    assert not curator.rename(piece(db, "square").id, "   ").done


# ------------------------------------------------------------------------- file


def test_filing_a_piece_in_another_sheet(studio):
    _, db, _, librarian, curator, _ = studio
    outcome = curator.move([piece(db, "square").id], project="game", sheet="shapes")

    assert outcome.ok
    assert files(librarian, "game", "shapes") == ["sheet.json", "square.svg"]
    assert files(librarian, "game", "pack") == ["circle.svg", "sheet.json"]


def test_a_piece_does_not_change_project(studio):
    """Each project has its store and mirror: moving a piece there means importing it."""
    _, db, _, librarian, curator, _ = studio
    outcome = curator.move([piece(db, "circle").id], project="other-game", sheet="shapes")

    assert not outcome.done
    assert "outside project" in next(iter(outcome.refused.values()))
    assert "circle.svg" in files(librarian, "game", "pack")


# ----------------------------------------------------------------------- delete


def test_deleting_removes_from_database_store_and_mirror(studio):
    _, db, store, librarian, curator, _ = studio
    target = piece(db, "square")
    outcome = curator.delete([target.id])

    assert outcome.ok
    assert db.get_asset(target.id) is None
    assert store.path_for(target.id) is None
    assert files(librarian, "game", "pack") == ["circle.svg", "sheet.json"]


def test_what_a_character_holds_is_not_deleted(studio):
    """The bare mesh of a rigged entity is not a free file: the agent starts from it."""
    _, db, store, _librarian, curator, _ = studio
    bare = store.put_bytes(b"glTF bare", ".glb", kind="mesh", meta={"role": "raw_mesh"})
    rigged = store.put_bytes(b"glTF rigged", ".glb", kind="mesh",
                             meta={"role": "imported_mesh"})
    for asset in (bare, rigged):
        db.save_asset(asset)
    hero = Character(
        spec=CharacterSpec(id="hero", name="Hero", subject="test"),
        style_pack_id="style",
        rig3d=Rig(archetype=Archetype.BIPED, bones=["hips"], mesh_asset_id=rigged.id,
                  source_mesh_asset_id=bare.id),
    )
    db.save_character("game", hero)

    for held in (bare, rigged):
        outcome = curator.delete([held.id])
        assert not outcome.done
        assert "hero" in next(iter(outcome.refused.values()))
        assert db.get_asset(held.id) is not None

    assert not curator.move([bare.id], project="game", sheet="elsewhere").done
    assert not curator.rename(bare.id, "golem").done


def test_a_card_s_chosen_concept_is_not_deleted(studio, tmp_path: Path):
    """Even before it goes to 3D, the chosen concept is held by its card."""
    _, db, store, _, curator, _ = studio
    concept = store.put_bytes(b"concept png", ".png", kind="image",
                              meta={"role": "generation", "project": "game"})
    db.save_asset(concept)
    workbench = tmp_path / "documents" / "world" / "characters" / "golem.workbench.json"
    workbench.parent.mkdir(parents=True)
    workbench.write_text(json.dumps({"version": 1, "concept": concept.id}), encoding="utf-8")

    outcome = curator.delete([concept.id])
    assert not outcome.done
    assert "world/characters/golem" in outcome.refused[concept.id]
    assert store.path_for(concept.id) is not None

    workbench.write_text(json.dumps({"version": 1, "concept": None}), encoding="utf-8")
    assert curator.delete([concept.id]).ok


def test_only_an_effect_s_latest_render_is_held(studio):
    """An effect render is a whole; earlier renders are free."""
    _, db, store, _, curator, _ = studio
    frames = {}
    for build in ("20261001T000000", "20261002T000000"):
        frame = store.put_bytes(f"frame {build}".encode(), ".png", kind="image",
                                meta={"role": "effect_frame", "project": "game",
                                      "effect": "aura", "build": build, "index": 0})
        db.save_asset(frame)
        frames[build] = frame.id

    held = curator.delete([frames["20261002T000000"]])
    assert "effect “aura”" in next(iter(held.refused.values()))
    assert curator.delete([frames["20261001T000000"]]).ok


def test_the_store_workbench_suffix_is_the_documents_one():
    from gamestudio.service import documents
    from gamestudio.store import curation

    assert curation.WORKBENCH == documents.WORKBENCH


# ------------------------------------------------------------------------ paste


def test_pasting_a_file_adds_it_to_the_sheet(studio, tmp_path: Path):
    _, db, _, librarian, curator, _ = studio
    from_elsewhere = tmp_path / "star.svg"
    from_elsewhere.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect width="10" height="10"/></svg>', encoding="utf-8")

    outcome = curator.add_files([from_elsewhere], project="game", sheet="pack")
    assert outcome.ok
    assert "star.svg" in files(librarian, "game", "pack")
    assert piece(db, "star").meta["role"] == "icon"


def test_pasting_twice_does_not_overwrite_the_first(studio, tmp_path: Path):
    _, _, _, librarian, curator, _ = studio
    for content in ("<svg xmlns='http://www.w3.org/2000/svg'><rect width='4' height='4'/></svg>",
                    "<svg xmlns='http://www.w3.org/2000/svg'><rect width='6' height='6'/></svg>"):
        source = tmp_path / "star.svg"
        source.write_text(content, encoding="utf-8")
        curator.add_files([source], project="game", sheet="pack")

    names = files(librarian, "game", "pack")
    assert "star.svg" in names and "star-2.svg" in names


def test_an_unknown_format_is_refused(studio, tmp_path: Path):
    _, _, _, _, curator, _ = studio
    text = tmp_path / "notes.txt"
    text.write_text("nothing to see", encoding="utf-8")
    outcome = curator.add_files([text], project="game", sheet="pack")
    assert not outcome.done
    assert "unsupported format" in outcome.refused["notes.txt"]
